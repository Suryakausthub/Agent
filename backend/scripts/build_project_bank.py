"""Generate Project Bank entries from harvested evidence (step 3).

    .venv\\Scripts\\python backend\\scripts\\build_project_bank.py
        [--only keep] [--limit N] [--repo NAME] [--dry-run] [--refresh]

One Groq call per repository. The free tier allows 8,000 tokens/minute, so the
run is throttled; a full pass over ~32 repositories takes roughly 15 minutes.

Everything lands as `needs_review`. Nothing generated here can be used on a
resume until it is approved.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml

from app.core.llm import LLM
from app.core.project_extract import build_brief, extract
from app.db import Store

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB = os.path.join(ROOT, "job_agent.db")
ENV = os.path.join(ROOT, ".env")
CFG = os.path.join(ROOT, "backend", "data", "config.yaml")

# Free tier: 8,000 tokens/minute. A brief plus its response runs 3-4k, so two
# calls per minute is the safe ceiling.
MIN_SECONDS_BETWEEN_CALLS = 32.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", choices=["", "keep", "review"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repo", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the brief that would be sent; no model calls")
    ap.add_argument("--refresh", action="store_true",
                    help="re-extract repositories that already have an entry")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(CFG, encoding="utf-8"))
    store = Store(DB)

    rows = store.list_projects()
    wanted = {args.only} if args.only else {"keep", "review"}
    todo = [r for r in rows if r["classification"] in wanted]
    if args.repo:
        todo = [r for r in todo if r["repo_name"].lower() == args.repo.lower()]

    usable, skipped_unusable = [], []
    for r in rows:
        if r not in todo:
            continue
        full = store.get_project(r["id"])
        ev = (full or {}).get("evidence_json")
        if not ev:
            continue
        if any("NO USABLE EVIDENCE" in w for w in ev.get("warnings", [])):
            skipped_unusable.append(r["repo_name"])
            continue
        usable.append((r, ev))

    if args.limit:
        usable = usable[:args.limit]

    print("Project Bank generation")
    print("  candidates with usable evidence : %d" % len(usable))
    if skipped_unusable:
        print("  skipped (no readable content)   : %s"
              % ", ".join(skipped_unusable))
    print("  model                           : %s" % cfg["llm"]["model"])
    print("  throttle                        : 1 call / %ds (free tier)"
          % MIN_SECONDS_BETWEEN_CALLS)
    print()

    if args.dry_run:
        for row, ev in usable[:3]:
            brief = build_brief(ev)
            print("=" * 74)
            print("BRIEF for %s  (%d chars, ~%d tokens)"
                  % (row["repo_name"], len(brief), len(brief) // 4))
            print("=" * 74)
            print(brief[:2600])
            print()
        return 0

    llm = LLM(cfg["llm"], env_path=ENV)
    done = failed = cached = 0
    tok_in = tok_out = 0
    last = 0.0

    for i, (row, ev) in enumerate(usable, 1):
        name = row["repo_name"]
        if row.get("project_json") and not args.refresh:
            print("  [%2d/%2d] %-44s cached" % (i, len(usable), name[:44]))
            cached += 1
            continue

        wait = MIN_SECONDS_BETWEEN_CALLS - (time.time() - last)
        if last and wait > 0:
            sys.stdout.write("  [%2d/%2d] %-44s waiting %2ds for rate limit\r"
                             % (i, len(usable), name[:44], int(wait)))
            sys.stdout.flush()
            time.sleep(wait)

        sys.stdout.write("  [%2d/%2d] %-44s extracting...   \r"
                         % (i, len(usable), name[:44]))
        sys.stdout.flush()
        last = time.time()

        p = extract(ev, llm, cfg)
        tok_in += p.tokens_in
        tok_out += p.tokens_out

        if not p.resume_bullets:
            print("  [%2d/%2d] %-44s FAILED: %s"
                  % (i, len(usable), name[:44],
                     "; ".join(p.warnings)[:60] or "no bullets"))
            failed += 1
            continue

        store.save_project_extract(
            row["id"],
            {
                "name": p.name, "github_url": p.github_url,
                "description": p.description, "technologies": p.technologies,
                "skills": p.skills, "features": p.features,
                "architecture": p.architecture,
                "resume_bullets": p.resume_bullets, "metrics": p.metrics,
                "evidence_refs": p.evidence_refs,
                "complexity_note": p.complexity_note,
                "softened": p.softened,
                "stripped_numbers": p.stripped_numbers,
                "warnings": p.warnings,
                "needs_attention": p.needs_attention,
            },
            title=p.name, summary=p.description,
            relevance_text=p.relevance_text())
        done += 1

        flags = []
        if p.metrics:
            flags.append("%d metric" % len(p.metrics))
        if p.stripped_numbers:
            flags.append("%d stripped" % len(p.stripped_numbers))
        if p.softened:
            flags.append("%d softened" % len(p.softened))
        if p.needs_attention:
            flags.append("NEEDS EDIT")
        print("  [%2d/%2d] %-44s ok  %s          "
              % (i, len(usable), name[:44], ", ".join(flags) or "clean"))

    print()
    print("Extracted: %d   cached: %d   failed: %d" % (done, cached, failed))
    print("Tokens: %d in / %d out" % (tok_in, tok_out))
    counts = {}
    for r in store.list_projects():
        if r.get("project_json"):
            counts["extracted"] = counts.get("extracted", 0) + 1
    print("Project Bank: %d entries, all needs_review"
          % counts.get("extracted", 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
