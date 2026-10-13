"""Exercise term extraction and implication expansion against a real-shaped JD."""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.latex_parser import parse
from app.core.vocabulary import Vocabulary

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
resume = parse(io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read())

JD = """
Software Development Engineer - Backend

We are looking for engineers to build scalable backend services.

Required:
- Strong programming skills in Java or Kotlin
- Experience with Spring Boot and RESTful microservices
- Solid understanding of data structures and algorithms
- Hands-on with relational databases (Postgres or MySQL) and query tuning
- Familiarity with containerization (Docker, K8s)
- Experience with CI/CD pipelines and version control

Preferred:
- Exposure to Kafka or other event-driven systems
- Knowledge of Kubernetes and cloud platforms (AWS)
- Experience with Golang
- Prior work with Elasticsearch
"""

print("=" * 66)
print("TERM EXTRACTION FROM JD")
print("=" * 66)
jd_hits = vocab.extract(JD)
for key in sorted(jd_hits):
    print("  %-28s <- %s" % (vocab.canonical(key), ", ".join(sorted(set(jd_hits[key])))))
print("  total: %d distinct terms" % len(jd_hits))

print()
print("=" * 66)
print("LONGEST-MATCH CHECK (the ambiguity cases)")
print("=" * 66)
for probe in ["Spring Boot", "spring", "data structures and algorithms",
              "K8s", "Postgres", "CI/CD", "RESTful microservices"]:
    hits = vocab.extract(probe)
    print("  %-34s -> %s" % (probe, [vocab.canonical(k) for k in hits] or "NO MATCH"))

print()
print("=" * 66)
print("CANDIDATE TERMS FROM RESUME")
print("=" * 66)
res_hits = vocab.extract(resume.evidence_text())
direct = set(res_hits)
print("  direct: %d terms" % len(direct))
print("  %s" % ", ".join(sorted(vocab.canonical(k) for k in direct)))

print()
expanded = vocab.expand(direct)
implied_only = {k: v for k, v in expanded.items() if k not in direct}
print("  implied (not directly stated): %d" % len(implied_only))
for k, v in sorted(implied_only.items(), key=lambda x: -x[1]):
    print("    %-28s %.2f" % (vocab.canonical(k), v))

print()
print("=" * 66)
print("COVERAGE OF THIS JD")
print("=" * 66)
for key in sorted(jd_hits):
    score = expanded.get(key, 0.0)
    label = "STRONG " if score >= 0.85 else ("PARTIAL" if score >= 0.50 else "MISSING")
    note = ""
    if key not in direct and score > 0:
        note = "  (implied)"
    print("  %-7s %-28s %.2f%s" % (label, vocab.canonical(key), score, note))
