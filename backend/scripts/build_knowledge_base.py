"""Build or refresh the Project Knowledge Base (one record per repository).

    .venv\\Scripts\\python backend\\scripts\\build_knowledge_base.py
        [--only keep|review] [--repo NAME] [--limit N]
        [--refresh] [--sync] [--force] [--dry-run]

Default: build a record for every KEEP/REVIEW repository that does not have a
current (v2) record yet. Re-running is free for finished repositories, so a
run stopped by a rate limit just picks up where it left off.

  --refresh   regenerate records even when a v2 record exists
  --sync      ask GitHub which repositories changed since their record was
              built (one API request for all of them), re-harvest only those
              and regenerate their records
  --force     also overwrite records you edited by hand (skipped otherwise)
  --dry-run   print the brief for the first repository; no model calls

One model call per repository. The free tier allows 8,000 tokens per minute
and one call is ~9k (prompt + reasoning + record), so the run is throttled
to one call every 75 seconds - roughly 45 minutes for 35 repositories.
Every record lands as needs_review.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml

from app.core.knowledge_base import (build_brief, build_record, context_text,
                                     insufficient_record, is_kb_record)
from app.core.llm import LLM
from app.core.vocabulary import Vocabulary
from app.db import Store

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB = os.path.join(ROOT, "job_agent.db")
ENV = os.path.join(ROOT, ".env")
CFG = os.path.join(ROOT, "backend", "data", "config.yaml")
VOCAB = os.path.join(ROOT, "backend", "data", "vocabulary.yaml")
GH_CACHE = os.path.join(ROOT, "backend", "data", "github_cache")
EV_CACHE = os.path.join(ROOT, "backend", "data", "evidence_cache")

MIN_SECONDS_BETWEEN_CALLS = 75.0


def _changed_repos(store: Store, todo: list[dict]) -> set[str]:
    """Repositories pushed since their record was built. One API request."""
    from app.core.github_fetch import GitHubClient
    client = GitHubClient(GH_CACHE, env_path=ENV)
    owners = {r["id"].split("/")[0] for r in todo}
    latest: dict[str, str] = {}
    for owner in owners:
        for repo in client.list_repos(owner, use_cache=False):
            latest[repo.full_name] = repo.pushed_at
    client.close()

    changed = set()
    for r in todo:
        rec = r.get("project_json") or {}
        built_from = rec.get("source_pushed_at") or r.get("pushed_at") or ""
        now_at = latest.get(r["id"], "")
        if now_at and now_at > built_from:
            changed.add(r["id"])
    return changed


def _reharvest(store: Store, rows: list[dict]) -> None:
    from app.core.github_fetch import GitHubClient
    from app.core.project_evidence import EvidenceHarvester
    client = GitHubClient(GH_CACHE, env_path=ENV)
    harvester = EvidenceHarvester(client, EV_CACHE)
    for r in rows:
        ev = harvester.harvest(r["id"], r["github_url"],
                               r.get("default_branch") or "main",
                               use_cache=False)
        store.save_project_evidence(r["id"], ev.to_json(), ev.head_sha)
        print("  re-harvested %s (%d files)" % (r["repo_name"], ev.file_count))
    harvester.close()
    client.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", choices=["", "keep", "review"])
    ap.add_argument("--repo", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(CFG, encoding="utf-8"))
    store = Store(DB)
    vocab = Vocabulary.load(VOCAB)

    wanted = {args.only} if args.only else {"keep", "review"}
    todo = [store.get_project(r["id"]) for r in store.list_projects()
            if r["classification"] in wanted]
    if args.repo:
        todo = [r for r in todo if r["repo_name"].lower() == args.repo.lower()]

    changed: set[str] = set()
    if args.sync:
        changed = _changed_repos(store, todo)
        print("GitHub reports %d changed repositor%s since their record was "
              "built" % (len(changed), "y" if len(changed) == 1 else "ies"))
        stale = [r for r in todo if r["id"] in changed]
        if stale:
            _reharvest(store, stale)
            todo = [store.get_project(r["id"]) for r in todo]

    def needs_work(r: dict) -> tuple[bool, str]:
        rec = r.get("project_json")
        if rec and rec.get("user_edited") and not args.force:
            return False, "edited by you (use --force to overwrite)"
        if args.refresh or r["id"] in changed:
            return True, ""
        if is_kb_record(rec):
            return False, "current"
        return True, ""

    plan = []
    for r in todo:
        ok, why = needs_work(r)
        if ok:
            plan.append(r)
        elif why != "current":
            print("  skip %-44s %s" % (r["repo_name"][:44], why))
    if args.limit:
        plan = plan[:args.limit]

    print("Project Knowledge Base")
    print("  repositories in scope : %d" % len(todo))
    print("  to (re)build          : %d" % len(plan))
    print("  model                 : %s" % cfg["llm"]["model"])
    print()

    if args.dry_run:
        for r in plan[:1]:
            brief = build_brief(r.get("evidence_json") or {}, r)
            print(brief)
            print("\n(%d chars, ~%d tokens)" % (len(brief), len(brief) // 4))
        return 0
    if not plan:
        return 0

    llm = LLM(cfg["llm"], env_path=ENV)
    done = failed = thin = 0
    tok_in = tok_out = 0
    last = 0.0

    for i, r in enumerate(plan, 1):
        name = r["repo_name"]
        tag = "  [%2d/%2d] %-44s" % (i, len(plan), name[:44])
        ev = r.get("evidence_json") or {}

        if not ev or any("NO USABLE EVIDENCE" in w for w in ev.get("warnings", [])):
            rec = insufficient_record(
                r, ev, "repository has no readable content (notebooks, "
                       "datasets or archives only) - describe it by hand")
            store.save_knowledge_record(r["id"], rec, "")
            print("%s insufficient evidence - recorded as a gap" % tag)
            thin += 1
            continue

        wait = MIN_SECONDS_BETWEEN_CALLS - (time.time() - last)
        if last and wait > 0:
            sys.stdout.write("%s waiting %2ds for rate limit\r" % (tag, wait))
            sys.stdout.flush()
            time.sleep(wait)
        sys.stdout.write("%s analysing...              \r" % tag)
        sys.stdout.flush()
        last = time.time()

        res = build_record(ev, r, llm, vocab)
        tok_in += res.tokens_in
        tok_out += res.tokens_out
        if not res.record:
            print("%s FAILED: %s" % (tag, "; ".join(res.warnings)[:90]))
            failed += 1
            msg = " ".join(res.warnings).lower()
            if "tokens per day" in msg or "tpd" in msg:
                print("\n  Daily token limit reached. Re-run tomorrow; "
                      "finished repositories are kept.")
                break
            continue

        store.save_knowledge_record(r["id"], res.record,
                                    context_text(res.record))
        rec = res.record
        done += 1
        print("%s %2d bullets, %2d tech, %d gaps%s" % (
            tag, len(rec["bullets"]),
            sum(len(v) for v in rec["tech_stack"].values()),
            len(rec["gaps"]),
            "  [%s]" % "; ".join(w[:50] for w in res.warnings[:2])
            if res.warnings else ""))

    print()
    print("Built: %d   insufficient: %d   failed: %d" % (done, thin, failed))
    print("Tokens: %d in / %d out" % (tok_in, tok_out))
    print("All records are needs_review - approve them in the Project Bank "
          "screen before they can be used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
