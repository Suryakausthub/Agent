"""Harvest factual evidence from a repository. No model calls.

This is the retrieval half of extract-then-generate: it gathers what the
repository actually contains, so a later step can only describe what is
present here. Nothing in this module interprets, summarises or infers.

Budget design. The API allows 60 requests/hour unauthenticated, and 35
candidate repositories cannot each afford several calls. So:

  * ONE API request per repo -- the recursive git tree, which lists every path
    in the default branch in a single response.
  * File CONTENTS then come from raw.githubusercontent.com, which serves
    public files without consuming the API rate limit.

Selection is driven by the tree, never by walking the repo: manifests and
configuration first (they state technologies as fact), then documentation,
then entry points, then the largest source files. Everything is capped so a
20 MB repository yields the same bounded bundle as a 200 KB one.
"""

from __future__ import annotations


import json
import os
import re
from dataclasses import asdict, dataclass, field

import httpx

from .github_fetch import GitHubClient, GitHubError, RateLimited

RAW = "https://raw.githubusercontent.com"

# Roughly four characters per token; the whole bundle must stay well inside
# the 8,000 tokens/minute free-tier ceiling of the later extraction step.
MAX_BUNDLE_CHARS = 24_000
MAX_FILE_CHARS = 6_000
MAX_README_CHARS = 10_000
MAX_SOURCE_FILES = 6

# Manifests state dependencies as fact - the highest-value evidence per byte.
_MANIFESTS = {
    "requirements.txt", "pyproject.toml", "setup.py", "pipfile", "environment.yml",
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod",
    "cargo.toml", "gemfile", "composer.json", "csproj", "makefile", "cmakelists.txt",
}
_INFRA = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "procfile",
    "vercel.json", "netlify.toml", "serverless.yml", ".env.example",
}
_DOCS = re.compile(r"^(readme|architecture|design|docs?/.*\.md|report|paper)", re.I)

# Directories that are never the candidate's own work.
_SKIP_DIRS = re.compile(
    r"(^|/)(node_modules|venv|\.venv|env|dist|build|target|vendor|third_?party|"
    r"site-packages|\.git|__pycache__|\.next|\.idea|coverage|migrations|"
    r"data|datasets?|assets|images?|static/vendor)(/|$)", re.I)

_SOURCE_EXT = (".py", ".java", ".go", ".ts", ".tsx", ".js", ".jsx", ".cpp",
               ".cc", ".c", ".h", ".hpp", ".rs", ".rb", ".php", ".cs", ".kt",
               ".scala", ".sql", ".sh")
_BINARYISH = (".ipynb", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".csv",
              ".parquet", ".model", ".pkl", ".h5", ".bin", ".mp4", ".wav")

_ENTRY = re.compile(
    r"(^|/)(main|app|server|index|__main__|cli|run|api|manage)\.[a-z]+$", re.I)
_TEST = re.compile(r"(^|/)(tests?|spec)/|(^|/)test_[^/]+$|_test\.[a-z]+$", re.I)


@dataclass
class FileEvidence:
    path: str
    kind: str            # manifest | infra | doc | entry | source | test
    size: int
    content: str = ""
    truncated: bool = False


@dataclass
class RepoEvidence:
    repo: str
    github_url: str
    head_sha: str = ""
    default_branch: str = "main"
    languages: dict[str, int] = field(default_factory=dict)
    file_count: int = 0
    total_bytes: int = 0
    dir_summary: list[str] = field(default_factory=list)
    readme: str = ""
    readme_truncated: bool = False
    files: list[FileEvidence] = field(default_factory=list)
    has_tests: bool = False
    has_docker: bool = False
    has_ci: bool = False
    numbers_found: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def bundle_chars(self) -> int:
        return len(self.readme) + sum(len(f.content) for f in self.files)

    def to_json(self) -> dict:
        d = asdict(self)
        return d


# Numeric claims, captured WITH their surrounding line so a later step can cite
# a source. A metric with no entry here must never reach a resume bullet.
_NUM = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*"
    r"(%|percent|ms|milliseconds?|s\b|seconds?|min|minutes?|hours?|"
    r"k\b|m\b|million|billion|rps|qps|tps|req/s|requests?/s|"
    r"users?|records?|rows?|entries|events?|files?|images?|samples?|"
    r"endpoints?|apis?|services?|tables?|tests?|nodes?|threads?|"
    r"gb|mb|kb|accuracy|f1)\b", re.I)


# Lines that contain numbers but never contain metrics. Badge URLs are the
# worst offender: "shields.io/badge/Python-3.9%2B" parses as "3.9 %", turning a
# version number into a fabricated percentage. Dependency pins and copyright
# years are the same class of problem.
_NOT_A_METRIC = re.compile(
    r"(https?://|shields\.io|badge/|!\[|\]\(|"          # badges and links
    r"^\s*[-*]?\s*[\w.\-]+\s*[=~<>!]=|"                 # dependency pins
    r"^\s*\"[\w@/.\-]+\"\s*:\s*\"[\^~]?\d|"             # package.json deps
    r"copyright|\(c\)\s*\d{4}|version\s*[:=]|"          # boilerplate
    r"^\s*#{1,6}\s*\d|^\s*\|?\s*:?-{3,})",              # headings, table rules
    re.I)

# Version-like tokens: 3.9, 2.0.1, v1.2 - never metrics on their own.
_VERSIONISH = re.compile(r"^\d+\.\d+(\.\d+)?$")


def _harvest_numbers(text: str, source: str) -> list[dict]:
    """Collect numeric claims WITH the line that supports them.

    Deliberately conservative: a missed metric costs a slightly weaker bullet,
    whereas a false one puts a fabricated number on a resume. Anything that
    looks like a version, a dependency pin or a badge is discarded.
    """
    out: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > 300:
            continue
        if _NOT_A_METRIC.search(stripped):
            continue
        for m in _NUM.finditer(stripped):
            value, unit = m.group(1), m.group(2).lower()
            # "3.9 %" from a version string is not a percentage.
            if unit in ("%", "percent") and _VERSIONISH.match(value):
                continue
            out.append({
                "value": value,
                "unit": unit,
                "source": source,
                "evidence": stripped[:220],
            })
    return out


def _classify_path(path: str) -> str | None:
    low = path.lower()
    base = low.rsplit("/", 1)[-1]

    if _SKIP_DIRS.search(low):
        return None
    if low.endswith(_BINARYISH):
        return None
    if base in _MANIFESTS or base.endswith(".csproj"):
        return "manifest"
    if base in _INFRA or base.startswith("dockerfile"):
        return "infra"
    if _DOCS.match(low) or (low.endswith(".md") and low.count("/") <= 1):
        return "doc"
    if _TEST.search(low):
        return "test"
    if not low.endswith(_SOURCE_EXT):
        return None
    if _ENTRY.search(low):
        return "entry"
    return "source"


_PRIORITY = {"manifest": 0, "infra": 1, "doc": 2, "entry": 3, "source": 4,
             "test": 5}


def select_files(tree: list[dict]) -> list[tuple[str, str, int]]:
    """Pick what is worth reading. Returns (path, kind, size), best first."""
    picked: list[tuple[str, str, int]] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        size = int(node.get("size") or 0)
        kind = _classify_path(path)
        if kind is None or size == 0:
            continue
        picked.append((path, kind, size))

    def rank(item):
        path, kind, size = item
        depth = path.count("/")
        # Within a kind: shallower first, then larger (more substantive).
        return (_PRIORITY[kind], depth, -size)

    picked.sort(key=rank)

    out: list[tuple[str, str, int]] = []
    source_seen = 0
    for path, kind, size in picked:
        if kind in ("source", "test"):
            if source_seen >= MAX_SOURCE_FILES:
                continue
            source_seen += 1
        out.append((path, kind, size))
        if len(out) >= 24:
            break
    return out


def _dir_summary(tree: list[dict], limit: int = 14) -> list[str]:
    counts: dict[str, int] = {}
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        top = path.split("/")[0] if "/" in path else "(root)"
        counts[top] = counts.get(top, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return ["%s/ (%d files)" % (k, v) if k != "(root)" else "root (%d files)" % v
            for k, v in ordered]


class EvidenceHarvester:
    def __init__(self, client: GitHubClient, cache_dir: str):
        self.gh = client
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        # raw.githubusercontent.com is a plain CDN and does not consume the
        # API rate limit, which is what makes this affordable without a token.
        self._raw = httpx.Client(timeout=25.0, follow_redirects=True,
                                 headers={"User-Agent": "job-agent-evidence"})

    def _raw_get(self, repo: str, branch: str, path: str) -> str:
        url = "%s/%s/%s/%s" % (RAW, repo, branch, path)
        try:
            r = self._raw.get(url)
            if r.status_code != 200:
                return ""
            return r.text
        except httpx.HTTPError:
            return ""

    def harvest(self, full_name: str, html_url: str, branch: str,
                use_cache: bool = True) -> RepoEvidence:
        ev = RepoEvidence(repo=full_name, github_url=html_url,
                          default_branch=branch)

        cached = os.path.join(self.cache_dir,
                              "%s.json" % full_name.replace("/", "__"))
        if use_cache and os.path.exists(cached):
            with open(cached, encoding="utf-8") as fh:
                data = json.load(fh)
            ev = RepoEvidence(**{k: v for k, v in data.items()
                                 if k != "files"})
            ev.files = [FileEvidence(**f) for f in data.get("files", [])]
            return ev

        # The single API request: a recursive tree of the default branch.
        try:
            tree_resp = self.gh._get(
                "/repos/%s/git/trees/%s?recursive=1" % (full_name, branch),
                allow_404=True)
        except RateLimited:
            raise
        except GitHubError as e:
            ev.warnings.append("tree fetch failed: %s" % e)
            return ev

        if not tree_resp:
            ev.warnings.append("no tree for branch %r" % branch)
            return ev

        ev.head_sha = tree_resp.get("sha", "")
        tree = tree_resp.get("tree") or []
        if tree_resp.get("truncated"):
            ev.warnings.append("tree truncated by GitHub (very large repo)")

        blobs = [n for n in tree if n.get("type") == "blob"]
        ev.file_count = len(blobs)
        ev.total_bytes = sum(int(n.get("size") or 0) for n in blobs)
        ev.dir_summary = _dir_summary(tree)

        paths_lower = [n.get("path", "").lower() for n in blobs]
        ev.has_tests = any(_TEST.search(p) for p in paths_lower)
        ev.has_docker = any("dockerfile" in p or "docker-compose" in p
                            for p in paths_lower)
        ev.has_ci = any(p.startswith(".github/workflows/") for p in paths_lower)
        ev.languages = _language_mix(blobs)

        budget = MAX_BUNDLE_CHARS
        for path, kind, size in select_files(tree):
            if budget <= 0:
                break
            text = self._raw_get(full_name, branch, path)
            if not text.strip():
                continue

            if kind == "doc" and path.lower().startswith("readme") and not ev.readme:
                ev.readme = text[:MAX_README_CHARS]
                ev.readme_truncated = len(text) > MAX_README_CHARS
                budget -= len(ev.readme)
                ev.numbers_found += _harvest_numbers(ev.readme, path)
                continue

            cap = min(MAX_FILE_CHARS, budget)
            body = text[:cap]
            ev.files.append(FileEvidence(
                path=path, kind=kind, size=size, content=body,
                truncated=len(text) > cap))
            budget -= len(body)
            if kind in ("manifest", "infra", "doc"):
                ev.numbers_found += _harvest_numbers(body, path)

        # Deduplicate numeric claims, keeping the first citation of each.
        seen: set[tuple] = set()
        unique: list[dict] = []
        for n in ev.numbers_found:
            key = (n["value"], n["unit"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(n)
        ev.numbers_found = unique[:40]

        if not ev.readme:
            ev.warnings.append("no README - evidence is code and manifests only")

        # A bundle with nothing readable cannot support a description, however
        # large the repository is. ML_PROJECT is 19 MB in 5 files -- notebooks
        # and datasets, no source. Say so loudly rather than handing a later
        # step an empty bundle it would be tempted to fill in from the name.
        if ev.bundle_chars() == 0:
            ev.warnings.append(
                "NO USABLE EVIDENCE: %d file(s), %.1f MB, but nothing readable "
                "(notebooks, datasets or archives only) - cannot be described "
                "without opening it yourself"
                % (ev.file_count, ev.total_bytes / 1e6))
        elif ev.bundle_chars() < 800:
            ev.warnings.append(
                "very thin evidence (%d chars) - description would be weak"
                % ev.bundle_chars())

        tmp = cached + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ev.to_json(), fh)
        os.replace(tmp, cached)
        return ev

    def close(self) -> None:
        self._raw.close()


def _language_mix(blobs: list[dict]) -> dict[str, int]:
    """Bytes per extension, as a factual counterweight to GitHub's guess."""
    out: dict[str, int] = {}
    for n in blobs:
        path = n.get("path", "").lower()
        if _SKIP_DIRS.search(path) or not path.endswith(_SOURCE_EXT):
            continue
        ext = "." + path.rsplit(".", 1)[-1]
        out[ext] = out.get(ext, 0) + int(n.get("size") or 0)
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:8])
