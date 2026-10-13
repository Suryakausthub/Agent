"""GitHub API client for one-time project discovery.

Read-only, public repositories only. Authentication is optional but strongly
preferred: unauthenticated callers get 60 requests/hour, which is not enough to
enrich 75 repositories, while a token raises that to 5,000.

Token handling: read from GITHUB_TOKEN in .env at call time, held only in the
Authorization header, and never logged, returned, cached or persisted. Nothing
in this module prints a request header.

Responses are cached to backend/data/github_cache/ so re-running discovery
costs no API budget and so the raw payloads stay auditable -- the classifier's
reasons can always be checked against exactly what GitHub returned.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "https://api.github.com"
_UA = "job-agent-project-discovery"


class GitHubError(RuntimeError):
    pass


class RateLimited(GitHubError):
    def __init__(self, reset_in: float):
        super().__init__("GitHub rate limit exhausted; resets in %ds" % int(reset_in))
        self.reset_in = reset_in


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Repo:
    """Exactly the fields the classifier is allowed to reason about."""
    name: str
    full_name: str
    html_url: str
    description: str
    fork: bool
    archived: bool
    size_kb: int
    language: str
    topics: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    pushed_at: str = ""
    default_branch: str = "main"
    stargazers: int = 0
    forks_count: int = 0
    open_issues: int = 0
    has_pages: bool = False
    license_name: str = ""

    # Filled only when a token allows enrichment; None means "not checked",
    # which the classifier must treat as unknown rather than as absent.
    has_readme: bool | None = None
    readme_bytes: int | None = None
    commits_total: int | None = None
    commits_by_owner: int | None = None
    head_sha: str = ""

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Repo":
        lic = d.get("license") or {}
        return cls(
            name=d.get("name", ""),
            full_name=d.get("full_name", ""),
            html_url=d.get("html_url", ""),
            description=(d.get("description") or "").strip(),
            fork=bool(d.get("fork")),
            archived=bool(d.get("archived")),
            size_kb=int(d.get("size") or 0),
            language=(d.get("language") or "").strip(),
            topics=list(d.get("topics") or []),
            created_at=(d.get("created_at") or "")[:10],
            updated_at=(d.get("updated_at") or "")[:10],
            pushed_at=(d.get("pushed_at") or "")[:10],
            default_branch=d.get("default_branch") or "main",
            stargazers=int(d.get("stargazers_count") or 0),
            forks_count=int(d.get("forks_count") or 0),
            open_issues=int(d.get("open_issues_count") or 0),
            has_pages=bool(d.get("has_pages")),
            license_name=(lic.get("spdx_id") or "") if lic else "",
        )


class GitHubClient:
    def __init__(self, cache_dir: str, env_path: str | None = None,
                 timeout: float = 25.0):
        if env_path:
            _load_dotenv(env_path)
        self._token = os.environ.get("GITHUB_TOKEN", "").strip()
        self.authenticated = bool(self._token)
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

        headers = {"Accept": "application/vnd.github+json", "User-Agent": _UA,
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = "Bearer %s" % self._token
        self._client = httpx.Client(timeout=timeout, headers=headers,
                                    follow_redirects=True)

        self.remaining: int | None = None
        self.reset_at: float = 0.0
        self.requests_made = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ core

    def _cache_path(self, url: str) -> str:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        return os.path.join(self.cache_dir, "%s.json" % key)

    def _get(self, path: str, *, use_cache: bool = True,
             allow_404: bool = False) -> Any:
        url = path if path.startswith("http") else API + path
        cached = self._cache_path(url)

        if use_cache and os.path.exists(cached):
            self.cache_hits += 1
            with open(cached, encoding="utf-8") as fh:
                payload = json.load(fh)
            return None if payload.get("_status") == 404 else payload["data"]

        # Stop before spending the last of the budget on a partial run.
        if self.remaining is not None and self.remaining <= 1:
            raise RateLimited(max(0.0, self.reset_at - time.time()))

        resp = self._client.get(url)
        self.requests_made += 1

        rem = resp.headers.get("x-ratelimit-remaining")
        rst = resp.headers.get("x-ratelimit-reset")
        if rem is not None:
            self.remaining = int(rem)
        if rst is not None:
            self.reset_at = float(rst)

        if resp.status_code == 404 and allow_404:
            self._write_cache(cached, {"_status": 404, "data": None})
            return None
        if resp.status_code == 403 and self.remaining == 0:
            raise RateLimited(max(0.0, self.reset_at - time.time()))
        if resp.status_code == 401:
            raise GitHubError(
                "GitHub rejected the credentials. Check GITHUB_TOKEN in .env "
                "(the token value is never printed).")
        if resp.status_code >= 400:
            raise GitHubError("GitHub returned HTTP %d for %s"
                              % (resp.status_code, _redact(url)))

        data = resp.json()
        self._write_cache(cached, {"_status": 200, "data": data})
        return data

    def _write_cache(self, path: str, payload: dict) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

    # --------------------------------------------------------------- public

    def list_repos(self, user: str, use_cache: bool = True) -> list[Repo]:
        """Every public repo, paginated. One request per 100 repos."""
        out: list[Repo] = []
        page = 1
        while True:
            data = self._get(
                "/users/%s/repos?per_page=100&page=%d&sort=updated" % (user, page),
                use_cache=use_cache)
            if not data:
                break
            out.extend(Repo.from_api(d) for d in data)
            if len(data) < 100:
                break
            page += 1
        return out

    def enrich(self, repo: Repo, owner: str) -> Repo:
        """Add README and authorship signals. Requires spare rate budget.

        Leaves the optional fields as None when the budget will not allow it,
        so the classifier can tell "no README" apart from "not checked".
        """
        readme = self._get("/repos/%s/readme" % repo.full_name,
                           allow_404=True)
        repo.has_readme = readme is not None
        repo.readme_bytes = int(readme.get("size", 0)) if readme else 0

        stats = self._get("/repos/%s/contributors?per_page=100&anon=0"
                          % repo.full_name, allow_404=True)
        if isinstance(stats, list) and stats:
            total = sum(int(c.get("contributions") or 0) for c in stats)
            mine = sum(int(c.get("contributions") or 0) for c in stats
                       if (c.get("login") or "").lower() == owner.lower())
            repo.commits_total = total
            repo.commits_by_owner = mine
        return repo

    def budget(self) -> str:
        if self.remaining is None:
            return "unknown"
        mins = max(0, int((self.reset_at - time.time()) / 60))
        return "%d remaining, resets in %dm" % (self.remaining, mins)

    def close(self) -> None:
        self._client.close()


def _redact(url: str) -> str:
    """URLs never carry the token, but be explicit about it anyway."""
    return url.split("?")[0]
