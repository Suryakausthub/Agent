"""Harvest repository evidence for KEEP/REVIEW candidates (Project Bank, step 2).

    .venv\\Scripts\\python backend\\scripts\\harvest_evidence.py
        [--only keep] [--limit N] [--repo NAME] [--refresh]

Costs ONE GitHub API request per repository (the recursive file tree); file
contents come from raw.githubusercontent.com, which does not consume the API
rate limit. Results are cached on disk and written to the projects table, so
re-running is free.

No model calls. This step only collects facts.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.github_fetch import GitHubClient, GitHubError, RateLimited
from app.core.project_evidence import EvidenceHarvester
from app.db import Store

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE = os.path.join(ROOT, "backend", "data", "github_cache")
EV_CACHE = os.path.join(ROOT, "backend", "data", "evidence_cache")
DB = os.path.join(ROOT, "job_agent.db")
ENV = os.path.join(ROOT, ".env")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", choices=["", "keep", "review"],
                    help="restrict to one classification (default: keep+review)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repo", default="", help="single repository by name")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    client = GitHubClient(CACHE, env_path=ENV)
    harvester = EvidenceHarvester(client, EV_CACHE)
    store = Store(DB)

    rows = store.list_projects()
    wanted = {args.only} if args.only else {"keep", "review"}
    todo = [r for r in rows if r["classification"] in wanted]
    if args.repo:
        todo = [r for r in todo if r["repo_name"].lower() == args.repo.lower()]
    if args.limit:
        todo = todo[:args.limit]

    print("Evidence harvest")
    print("  authenticated : %s" % ("yes" if client.authenticated
                                    else "no (60 req/hr shared budget)"))
    print("  candidates    : %d" % len(todo))
    print("  cost          : 1 API request each; file contents are free")
    print()

    done = failed = skipped = 0
    for i, row in enumerate(todo, 1):
        name = row["repo_name"]
        sys.stdout.write("  [%2d/%2d] %-46s " % (i, len(todo), name[:46]))
        sys.stdout.flush()

        if row.get("evidence_json") and not args.refresh:
            print("cached")
            skipped += 1
            continue

        try:
            ev = harvester.harvest(row["id"], row["github_url"],
                                   _branch(row), use_cache=not args.refresh)
        except RateLimited as e:
            print("RATE LIMITED")
            print()
            print("  Stopped after %d repositories. %s" % (done, e))
            print("  Add GITHUB_TOKEN to .env to raise the limit to 5,000/hr,")
            print("  or re-run later - completed repositories are cached.")
            break
        except GitHubError as e:
            print("failed (%s)" % e)
            failed += 1
            continue

        store.save_project_evidence(row["id"], ev.to_json(), ev.head_sha)
        done += 1
        print("%3d files, %5d chars, %2d numbers%s"
              % (ev.file_count, ev.bundle_chars(), len(ev.numbers_found),
                 "  [no README]" if not ev.readme else ""))

    print()
    print("Harvested: %d   cached: %d   failed: %d" % (done, skipped, failed))
    print("API: %d requests, %d cache hits (%s)"
          % (client.requests_made, client.cache_hits, client.budget()))

    harvester.close()
    client.close()
    return 0


def _branch(row: dict) -> str:
    """Use the branch GitHub reported. Many of these repos are on 'master',
    and guessing 'main' would make every raw file fetch silently return
    nothing -- an empty bundle that looks like a repo with no content."""
    return row.get("default_branch") or "main"


if __name__ == "__main__":
    raise SystemExit(main())
