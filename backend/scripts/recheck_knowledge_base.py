"""Re-check existing Project Knowledge Base bullets with the current rules.

    .venv\\Scripts\\python backend\\scripts\\recheck_knowledge_base.py [--apply]

No model calls and no GitHub requests: each stored bullet is checked against
the repository evidence already harvested. Bullets that claim an artefact the
repository does not have (a Dockerfile, tests, CI, Kubernetes...) are moved to
the record's rejected_bullets with the reason. Bullets you edited by hand are
left alone.

Without --apply it only reports what would change.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.knowledge_base import artefact_problems, context_text, is_kb_record
from app.db import Store

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="save the changes")
    args = ap.parse_args()

    store = Store(os.path.join(ROOT, "job_agent.db"))
    removed_total = 0
    for row in store.list_projects():
        full = store.get_project(row["id"])
        rec, ev = full.get("project_json"), full.get("evidence_json") or {}
        if not is_kb_record(rec) or not ev:
            continue
        keep, dropped = [], []
        for b in rec.get("bullets", []):
            problems = [] if b.get("edited") else artefact_problems(b["text"], ev)
            (dropped if problems else keep).append((b, problems))
        if not dropped:
            continue
        removed_total += len(dropped)
        print("%s  (%d -> %d bullets)" % (row["repo_name"], len(keep) + len(dropped), len(keep)))
        for b, problems in dropped:
            print("   - %s\n     %s" % (b["text"][:110], "; ".join(problems)))
        if args.apply:
            rec["bullets"] = [b for b, _ in keep]
            rec.setdefault("rejected_bullets", []).extend(
                {"text": b["text"], "why": "; ".join(p) + " (re-check)"} for b, p in dropped)
            rec.setdefault("warnings", []).append(
                "re-check removed %d bullet(s) claiming artefacts the repository lacks"
                % len(dropped))
            if not rec["bullets"]:
                rec["status"] = "no_verified_bullets"
            store.update_knowledge_record(row["id"], rec, context_text(rec))

    print("\n%d bullet(s) %s." % (removed_total,
                                  "removed" if args.apply else "would be removed (run with --apply)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
