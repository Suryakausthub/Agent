"""Proven skills added to the Skills section, and the artefact checks on
GitHub bullets. Offline: uses the stored project library, no model calls."""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core.jd_extract import JobDescription, Requirement
from app.core.knowledge_base import artefact_problems, is_kb_record
from app.core.latex_parser import parse
from app.core.project_select import select
from app.core.render import render
from app.core.tailor import TailorResult, plan
from app.core.trim import _drop_project, TrimOutcome
from app.core.vocabulary import Vocabulary
from app.db import Store

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"), encoding="utf-8"))
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
resume = parse(io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read())
store = Store(os.path.join(ROOT, "job_agent.db"))
fails = 0


def check(cond, label):
    global fails
    print("  %s %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        fails += 1


print("artefact checks on GitHub bullets")
ev_no = {"files": [{"path": "scripts/docker-compose.yml", "kind": "infra", "content": "image: x"}],
         "has_docker": True, "has_tests": False, "has_ci": False}
ev_yes = {"files": [{"path": "api/Dockerfile", "kind": "infra", "content": "FROM python"},
                    {"path": "tests/test_api.py", "kind": "test", "content": "def test_x(): pass"}],
          "has_docker": True, "has_tests": True, "has_ci": False}
check(artefact_problems("Containerized each service with dedicated Dockerfiles.", ev_no),
      "'dedicated Dockerfiles' rejected when the repo has only a compose file")
check(not artefact_problems("Containerized each service with dedicated Dockerfiles.", ev_yes),
      "the same claim passes when a Dockerfile exists")
check(artefact_problems("Wrote a pytest suite for the API.", ev_no) and
      not artefact_problems("Wrote a pytest suite for the API.", ev_yes),
      "test-suite claims need test files")
check(artefact_problems("Set up a CI/CD pipeline with GitHub Actions.", ev_yes),
      "CI claims need a CI configuration")
check(not artefact_problems("Ran external Docker commands from the UI.", ev_no),
      "describing what the program does with Docker is not a packaging claim")
check(artefact_problems("Deployed the service to AWS.", ev_yes),
      "cloud deployment claims need the provider in the repo")

print("\nproven skills on the Skills section")
rows = [r for r in store.knowledge_base(require_approval=False)
        if is_kb_record(r["project_json"]) and r["project_json"].get("bullets")]
# A job asking for Go, MongoDB and Microservices: not in main.tex's Skills, but
# proven by a GitHub project in the library.
reqs = [Requirement(text=t, kind="required", keys=sorted(vocab.extract(t)))
        for t in ("Go", "MongoDB", "Microservices", "Docker")]
jd = JobDescription(raw="", title="Backend Engineer", company="Example", requirements=reqs)
sel = select(resume, rows, jd, vocab, cfg, llm=None)
p = plan(resume, jd, vocab, cfg, selection=sel)
names = [n for _, n, _ in p.skill_additions]
have = {k for line in resume.skills for k in vocab.extract(", ".join(line.items))}
print("    added:", [(lid, n, src) for lid, n, src in p.skill_additions])
check(len(names) > 0, "a skill the job asks for, proven by a GitHub project, is added")
check(all(set(vocab.extract(n)).isdisjoint(have) for n in names),
      "only skills missing from the Skills section are added")
check(len(names) <= cfg["tailoring"]["max_added_skills"], "at most max_added_skills")
for lid, n, src in p.skill_additions:
    check(any(e.id == src and e.origin == "bank" for e in p.projects_kept),
          "%s is proven by a GitHub project on the page" % n)

res = render(resume, p, TailorResult(plan=p))
out = parse(res.tex)
on_page = [i for line in out.skills for i in line.items]
check(all(n in on_page for n in names), "added skills appear in the rendered Skills section")
check(not res.warnings, "document stays balanced (%s)" % (res.warnings or "ok"))
db_line = [l for l in out.skills if "MongoDB" in l.items]
if "MongoDB" in names:
    check(db_line and "PostgreSQL" in db_line[0].items, "MongoDB lands on the Databases line")

# If the proving project is trimmed off the page, its skills go too.
while any(e.origin == "bank" for e in p.projects_kept):
    victim = [e for e in p.projects_kept if e.origin == "bank"][-1]
    p.projects_kept = [e for e in p.projects_kept if e is not victim]
res2 = render(resume, p, TailorResult(plan=p))
on_page2 = [i for line in parse(res2.tex).skills for i in line.items]
check(not any(n in on_page2 for n in names), "skills disappear when their project is trimmed off")

print("\nFAILED: %d" % fails if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
