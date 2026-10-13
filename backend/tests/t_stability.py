"""Diagnose extraction stability: same posting, different source and repeats.

The score is an average over distinct skill demands. If the model's prose
segmentation changes which demands are produced, the score moves even though
the job has not. This prints the actual demand sets so the divergence can be
seen rather than inferred.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml

from app.core.jd_extract import extract
from app.core.jd_fetch import FetchError, fetch_jd
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

PASTED = io.open(os.path.join(os.path.dirname(__file__), "postman_jd.txt"),
                 encoding="utf-8").read()
URL = sys.argv[1] if len(sys.argv) > 1 else (
    "https://postman.wd108.myworkdayjobs.com/careers/job/Bangalore-India/"
    "Software-Engineer--Fullstack---Collections_JR1000043")

# The fetched comparison needs the SAME posting as postman_jd.txt. Postings
# close; when this one has, the pasted-text repeats still test stability.
try:
    FETCHED, _ = fetch_jd(URL)
except FetchError as e:
    FETCHED = None
    print("fetch skipped (%s) - the posting has probably closed; running the "
          "pasted text only" % e)
    print()

RUNS = [("pasted-1", PASTED), ("pasted-2", PASTED)]
if FETCHED:
    RUNS += [("fetched-1", FETCHED), ("fetched-2", FETCHED)]
else:
    RUNS += [("pasted-3", PASTED)]

results = []
for label, text in RUNS:
    jd = extract(text, llm, vocab)
    rep = score(resume, jd, vocab, cfg)
    demands = sorted({" | ".join(sorted(r.keys)) for r in jd.requirements if r.keys})
    results.append((label, jd, rep, demands))
    print("%-10s  %d rows  %d distinct demands  score %d%%"
          % (label, len(jd.requirements), len(demands), rep.overall))

print()
print("=" * 78)
print("DISTINCT SKILL DEMANDS PER RUN")
print("=" * 78)
universe = sorted({d for _, _, _, ds in results for d in ds})
print("  %-38s %s" % ("demand", "  ".join("%-9s" % l for l, _, _, _ in results)))
print("  " + "-" * 74)
for d in universe:
    marks = "  ".join("%-9s" % ("yes" if d in ds else "-")
                      for _, _, _, ds in results)
    print("  %-38s %s" % (d[:38], marks))

print()
print("=" * 78)
print("SCORE COMPONENTS")
print("=" * 78)
print("  %-10s %8s %8s %8s %8s %6s" % ("run", "required", "preferred",
                                       "projects", "quals", "total"))
for label, jd, rep, _ in results:
    print("  %-10s %8.2f %8.2f %8.2f %8.2f %5d%%"
          % (label, rep.required_score, rep.preferred_score,
             rep.project_score, rep.qualification_score, rep.overall))

print()
print("=" * 78)
print("REQUIRED vs PREFERRED CLASSIFICATION")
print("=" * 78)
for label, jd, _, _ in results:
    print("  %-10s required=%d preferred=%d  unscoreable=%d"
          % (label, len(jd.required()), len(jd.preferred()),
             len(jd.unscoreable())))

print()
scores = [r.overall for _, _, r, _ in results]
print("spread: %d%% .. %d%%  (range %d points)"
      % (min(scores), max(scores), max(scores) - min(scores)))

# Identical input must give an identical score (README: "identical on every
# run"). Different sources of the same posting may differ; that is reported
# above, not asserted.
same = {}
for label, _, rep, _ in results:
    same.setdefault(label.split("-")[0], set()).add(rep.overall)
unstable = {k: v for k, v in same.items() if len(v) > 1}
print("identical input -> identical score: %s"
      % ("PASS" if not unstable else "FAIL %s" % unstable))
sys.exit(1 if unstable else 0)
