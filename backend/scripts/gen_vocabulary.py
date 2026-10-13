"""Generate data/vocabulary.yaml from the seed plus the master resume.

Run once to bootstrap, then hand-edit the YAML. Re-running preserves nothing:
it regenerates from seed + resume, so put manual additions in the YAML only
after you stop re-running this, or add them to vocab_seed.py.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.latex_parser import parse
from app.core.vocabulary import Vocabulary

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
OUT = os.path.join(ROOT, "backend", "data", "vocabulary.yaml")

vocab = Vocabulary.from_seed()
print("seed terms: %d" % len(vocab.terms))

src = io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read()
resume = parse(src)
added = vocab.add_from_resume(resume)

print("added from resume: %d" % len(added))
for k in added:
    print("  + %-32s (%s)" % (vocab.canonical(k), vocab.category(k)))

vocab.save(OUT)
print()
print("total terms: %d" % len(vocab.terms))
print("written: %s" % os.path.relpath(OUT, ROOT))
