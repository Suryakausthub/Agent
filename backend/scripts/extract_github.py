"""One-time GitHub discovery and classification (Project Bank, step 1).

    .venv\\Scripts\\python backend\\scripts\\extract_github.py [--user NAME]
                                                             [--no-enrich]
                                                             [--refresh]

Safe to re-run: rows are keyed by owner/repo and updated in place, and API
responses are cached, so a second run costs no rate budget and creates no
duplicates. A classification you corrected by hand is preserved.

This step performs NO content reading and makes NO model calls. It only asks
which repositories are worth looking at.
"""
from __future__ import annotations

import argparse
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.github_fetch import GitHubClient, GitHubError, RateLimited
from app.core.repo_classify import (EXCLUDE, KEEP, REVIEW, classify_all,
                                    summarise)
from app.db import Store

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE = os.path.join(ROOT, "backend", "data", "github_cache")
DB = os.path.join(ROOT, "job_agent.db")
ENV = os.path.join(ROOT, ".env")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default="Suryakausthub")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip README/authorship lookups (1 API request total)")
    ap.add_argument("--refresh", action="store_true",
                    help="bypass the response cache and re-fetch from GitHub")
    ap.add_argument("--report", default="",
                    help="also write the table to this file")
    args = ap.parse_args()

    client = GitHubClient(CACHE, env_path=ENV)
    store = Store(DB)

    print("GitHub project discovery")
    print("  user          : %s" % args.user)
    print("  authenticated : %s" % ("yes (5000 req/hr)" if client.authenticated
                                    else "NO - 60 req/hr, enrichment limited"))
    if not client.authenticated:
        print("                  add GITHUB_TOKEN to .env for README and")
        print("                  authorship signals (public_repo scope only)")
    print()

    try:
        print("Listing repositories...")
        repos = client.list_repos(args.user, use_cache=not args.refresh)
        print("  found %d public repositories" % len(repos))
    except RateLimited as e:
        print("  rate limited: %s" % e)
        return 2
    except GitHubError as e:
        print("  failed: %s" % e)
        return 2

    # Enrichment costs ~2 requests per repo. Only worth attempting with a token.
    if not args.no_enrich and client.authenticated:
        print()
        print("Enriching (README + authorship)...")
        for i, r in enumerate(repos, 1):
            try:
                client.enrich(r, args.user)
            except RateLimited as e:
                print("\n  stopped early: %s" % e)
                print("  remaining repositories keep 'not checked' signals "
                      "and lean to REVIEW")
                break
            except GitHubError:
                pass          # a single failure must not abort the run
            if i % 5 == 0 or i == len(repos):
                sys.stdout.write("\r  %d/%d  (%s)   "
                                 % (i, len(repos), client.budget()))
                sys.stdout.flush()
        print()
    elif not args.no_enrich:
        print("Skipping enrichment: no token, and 75 repos would exhaust the")
        print("unauthenticated budget. Classification uses metadata only.")

    print()
    print("Classifying...")
    verdicts = classify_all(repos, args.user)

    inserted = updated = 0
    for v in verdicts:
        action = store.upsert_project(
            id=v.repo.full_name or ("%s/%s" % (args.user, v.repo.name)),
            repo_name=v.repo.name, github_url=v.repo.html_url,
            description=v.repo.description, language=v.repo.language,
            size_kb=v.repo.size_kb, is_fork=v.repo.fork,
            pushed_at=v.repo.pushed_at, default_branch=v.repo.default_branch,
            classification=v.classification,
            reason=v.reason, signals=v.signals, duplicate_of=v.duplicate_of)
        if action == "inserted":
            inserted += 1
        else:
            updated += 1

    table = render_table(verdicts)
    print()
    print(table)

    counts = summarise(verdicts)
    print()
    print("KEEP:    %d" % counts[KEEP])
    print("REVIEW:  %d" % counts[REVIEW])
    print("EXCLUDE: %d" % counts[EXCLUDE])
    print("TOTAL:   %d" % counts["total"])
    print()
    print("Database: %d inserted, %d updated" % (inserted, updated))
    print("API: %d requests, %d cache hits (%s)"
          % (client.requests_made, client.cache_hits, client.budget()))

    if args.report:
        with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(table + "\n\n")
            fh.write("KEEP: %d  REVIEW: %d  EXCLUDE: %d  TOTAL: %d\n"
                     % (counts[KEEP], counts[REVIEW], counts[EXCLUDE],
                        counts["total"]))
        print("Report: %s" % args.report)

    client.close()
    return 0


def render_table(verdicts) -> str:
    order = {KEEP: 0, REVIEW: 1, EXCLUDE: 2}
    rows = sorted(verdicts,
                  key=lambda v: (order[v.classification], -v.repo.size_kb))
    w = max(len(v.repo.name) for v in rows) if rows else 20
    w = min(w, 44)

    out = ["| %-*s | %-7s | %s" % (w, "Repository", "Class", "Reason"),
           "|%s|%s|%s" % ("-" * (w + 2), "-" * 9, "-" * 60)]
    for v in rows:
        out.append("| %-*s | %-7s | %s"
                   % (w, v.repo.name[:w], v.classification.upper(), v.reason))
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
