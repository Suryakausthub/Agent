"""End-to-end: raw JD -> extraction -> deterministic score, against main.tex."""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core.jd_extract import extract
from app.core.latex_parser import parse
from app.core.llm import LLM
from app.core.scoring import score
from app.core.vocabulary import Vocabulary

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"),
                          encoding="utf-8"))
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
resume = parse(io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read())
llm = LLM(cfg["llm"], env_path=os.path.join(ROOT, ".env"))

JD = """
Software Development Engineer I - Backend Platform
Acme Systems | Bengaluru, India | Full-time | Job ID: ACM-2291

About the role:
You will build and operate scalable backend services powering our payments platform.

Basic qualifications:
- Bachelor's degree in Computer Science or related field, graduating 2026
- Strong programming skills in Java or Kotlin
- Experience building RESTful microservices with Spring Boot
- Solid grounding in data structures and algorithms
- Working knowledge of relational databases such as Postgres or MySQL
- Comfortable with Git and CI/CD pipelines

Preferred qualifications:
- Exposure to Kafka or other event-driven architectures
- Familiarity with Docker and Kubernetes
- Experience with query tuning and performance optimisation
- Prior internship experience in a product company
- Excellent written and verbal communication skills

We are an equal opportunity employer.
"""

print("=" * 72)
print("JD EXTRACTION")
print("=" * 72)
jd = extract(JD, llm, vocab)
print("  company:  %s" % (jd.company or "-"))
print("  title:    %s" % (jd.title or "-"))
print("  location: %s" % (jd.location or "-"))
print("  job id:   %s" % (jd.job_id or "-"))
print("  type:     %s   level: %s   category: %s"
      % (jd.employment_type or "-", jd.level or "-", jd.category or "-"))
print("  degree:   %s" % (jd.qualifications.degree or "-"))
print("  grad yr:  %s" % (jd.qualifications.graduation_year or "-"))
print("  soft:     %s" % ", ".join(jd.soft_skills) or "-")
print()
print("  REQUIREMENTS (%d required, %d preferred)"
      % (len(jd.required()), len(jd.preferred())))
for r in jd.requirements:
    alts = " OR ".join(vocab.canonical(k) for k in r.keys) or "(no tech named)"
    flag = "!" if r.unresolved else " "
    print("   %s [%-9s] %-46s -> %s" % (flag, r.kind, r.text[:46], alts))
    if r.unresolved:
        print("       unresolved: %s" % ", ".join(r.unresolved))
if jd.warnings:
    print()
    print("  WARNINGS:")
    for w in jd.warnings:
        print("   - %s" % w)

print()
print("=" * 72)
print("SCORE")
print("=" * 72)
rep = score(resume, jd, vocab, cfg)
print()
print("  OVERALL MATCH: %d%%" % rep.overall)
print()
print("  %-20s %8s %8s %10s" % ("component", "raw", "weight", "points"))
print("  " + "-" * 50)
for name, raw, w, pts in rep.breakdown():
    print("  %-20s %7.2f %8.2f %9.1f" % (name, raw, w, pts))
print("  " + "-" * 50)
print("  %-20s %26.1f" % ("TOTAL", rep.overall))

print()
print("  SKILL VERDICTS")
for v in rep.skills:
    note = " (implied)" if v.implied else ""
    opts = " / ".join(v.options)
    print("   %-7s %-7s %-34s %.2f%s" % (v.status, v.kind[:7], opts[:34], v.score, note))

print()
print("  MISSING: %s" % (", ".join(rep.missing) or "none"))
print("  PARTIAL: %s" % (", ".join(rep.partial) or "none"))

print()
print("  PROJECT RELEVANCE")
for p in rep.projects:
    print("   %.2f  %-40s %s" % (p.score, p.title[:40], ", ".join(p.matched[:6])))

print()
print("  QUALIFICATIONS")
for q in rep.qualifications:
    print("   %-16s want=%-28s have=%-24s %s"
          % (q.name, q.expected[:28], q.found[:24], q.status))

if rep.notes:
    print()
    print("  NOTES")
    for n in rep.notes:
        print("   - %s" % n)

print()
print("=" * 72)
print("REPRODUCIBILITY (spec 3: score must not drift)")
print("=" * 72)
again = score(resume, jd, vocab, cfg)
print("  run 1: %d%%   run 2: %d%%   identical: %s"
      % (rep.overall, again.overall, rep.overall == again.overall))
