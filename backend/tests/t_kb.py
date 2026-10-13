"""Project Knowledge Base: verifiers, code selection, render. Offline.

Uses the real knowledge-base records in job_agent.db (whatever has been
built), treated as approved in memory -- nothing in the database is changed.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core.jd_extract import JobDescription, Requirement
from app.core.knowledge_base import Evidence, is_kb_record, strip_untraced_numbers
from app.core.latex_parser import parse
from app.core.project_select import select
from app.core.render import render
from app.core.tailor import TailorResult, plan
from app.core.vocabulary import Vocabulary
from app.db import Store

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"),
                          encoding="utf-8"))
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
src = io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read()
resume = parse(src)
store = Store(os.path.join(ROOT, "job_agent.db"))

fails = 0


def check(cond, label):
    global fails
    print("  %s %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        fails += 1


print("verifiers")
ev = {"readme": "A Go service using PostgreSQL. Handles 1,200 requests/s.",
      "files": [{"path": "go.mod", "kind": "manifest",
                 "content": "module github.com/me/x\nrequire gorm.io/gorm v1.2"}],
      "numbers_found": [{"value": "1,200", "unit": "requests/s",
                         "source": "README", "evidence": "..."}]}
E = Evidence(ev, vocab)
check(E.has_tech("PostgreSQL") and E.has_tech("Go"), "tech present in repo is kept")
check(not E.has_tech("Kafka"), "tech absent from repo is rejected")
check(E.unknown_terms("Built a Kafka consumer") != [], "bullet naming absent tech is caught")
t, stripped, used = strip_untraced_numbers(
    "Served 1,200 requests/s over IPv6 and cut latency by 40% using S3", E)
check("1,200" in t and "IPv6" in t and "S3" in t, "traced number and identifiers survive")
check("40" not in t and stripped == ["40"], "untraced metric is removed: %r" % t)
check(E.clean_paths(["go.mod", "made/up.py", "readme.md"]) == ["go.mod", "README.md"],
      "evidence paths limited to files actually read")

rows = [r for r in store.knowledge_base(require_approval=False)
        if is_kb_record(r["project_json"]) and r["project_json"].get("bullets")]
print()
print("knowledge base: %d usable record(s)" % len(rows))
if not rows:
    print("  (build some with scripts/build_knowledge_base.py)")
    sys.exit(1 if fails else 0)

# A backend JD built by hand so no model call is needed.
reqs = []
for text, kind in [("Go microservices", "required"), ("PostgreSQL", "required"),
                   ("MongoDB", "preferred"), ("Docker", "required"),
                   ("Java Spring Boot", "preferred")]:
    reqs.append(Requirement(text=text, kind=kind, keys=sorted(vocab.extract(text))))
jd = JobDescription(raw="", title="Backend Engineer", company="Example",
                    requirements=reqs)

print()
print("code selection (no model)")
sel = select(resume, rows, jd, vocab, cfg, llm=None)
for n in sel.notes:
    if n.startswith("selected"):
        print("   ", n[:120])
per = cfg["project_bank"]["max_bullets_per_project"]
check(cfg["resume"]["min_projects"] <= len(sel.kept) <= cfg["resume"]["max_projects"],
      "project count within page limits (%d)" % len(sel.kept))
check(all(len(e.bullets) <= per for e in sel.kept if e.origin == "bank"),
      "no bank project has more than %d bullets" % per)
bank_kept = [e for e in sel.kept if e.origin == "bank"]
for e in bank_kept:
    texts = [b.text for b in e.bullets]
    check(len(set(texts)) == len(texts), "%s: chosen bullets are distinct" % e.title[:40])

print()
print("render with knowledge-base projects")
p = plan(resume, jd, vocab, cfg, selection=sel)
res = render(resume, p, TailorResult(plan=p))
check(not res.warnings, "structurally balanced (%s)" % (res.warnings or "ok"))
out = parse(res.tex)
check(len(out.projects) == len(sel.kept), "re-parse finds %d projects" % len(out.projects))
check([e.title for e in out.projects] ==
      [e.title.replace("&", "\\&") if False else e.title for e in sel.kept],
      "projects rendered in ranked order")
for want, got in zip(sel.kept, out.projects):
    check(len(got.bullets) == len(want.bullets),
          "%s: %d bullet(s) on the page" % (want.title[:40], len(got.bullets)))
check(len(out.experience) == len(resume.experience) and
      len(out.education) == len(resume.education),
      "experience and education untouched")
check("\\section{Projects}" in res.tex and res.tex.count("\\resumeSubHeadingListEnd")
      == src.count("\\resumeSubHeadingListEnd"), "section frame preserved")

print()
print("match score counts knowledge-base projects")
from app.core.project_select import bank_entry
from app.core.scoring import score
extra = [e for e in (bank_entry(r) for r in rows) if e]
base = score(resume, jd, vocab, cfg)
with_kb = score(resume, jd, vocab, cfg, extra_projects=extra)
check(with_kb.project_score >= base.project_score,
      "project relevance %.2f -> %.2f with knowledge base"
      % (base.project_score, with_kb.project_score))
check(any(m.entry_id.startswith("bank.") for m in with_kb.projects),
      "knowledge-base projects appear in the project ranking")

print()
print("FAILED: %d" % fails if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
