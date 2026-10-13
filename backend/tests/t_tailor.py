"""ATS coverage, code-side selection, model rewrite, and the whitelist gate."""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core import ats as ats_mod
from app.core.jd_extract import extract
from app.core.latex_parser import parse
from app.core.llm import LLM
from app.core.tailor import plan, rewrite
from app.core.vocabulary import Vocabulary
from app.core.whitelist import Whitelist, build_diff

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"),
                          encoding="utf-8"))
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
resume = parse(io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read())
llm = LLM(cfg["llm"], env_path=os.path.join(ROOT, ".env"))

JD = io.open(os.path.join(os.path.dirname(__file__), "sample_jd.txt"),
             encoding="utf-8").read()
jd = extract(JD, llm, vocab)

print("=" * 74)
print("ATS KEYWORD COVERAGE")
print("=" * 74)
rep = ats_mod.analyse(resume, jd, vocab, cfg["scoring"]["thresholds"])
print("  coverage: %.0f%% exact, %.0f%% weighted"
      % (rep.coverage * 100, rep.weighted_coverage * 100))
for bucket, (found, total) in rep.by_bucket.items():
    print("    %-10s %d/%d" % (bucket, found, total))
print()
print("  %-9s %-28s %-6s %s" % ("STATUS", "KEYWORD", "JD x", "FOUND IN"))
print("  " + "-" * 68)
for r in rep.table():
    loc = ", ".join(r.locations) if r.locations else (
        ("implied by " + r.implied_by) if r.implied_by else "-")
    print("  %-9s %-28s %-6d %s" % (r.status, r.canonical[:28], r.jd_hits, loc[:30]))
print()
print("  PLACEMENT WARNINGS")
for w in rep.placement_warnings:
    print("   - %s" % w)

print()
print("=" * 74)
print("STEP A - CODE SELECTION (no model)")
print("=" * 74)
p = plan(resume, jd, vocab, cfg)
for n in p.notes:
    print("  %s" % n)
print()
print("  skills reordered: %s"
      % " > ".join(next(s.category for s in resume.skills if s.id == sid)
                   for sid in p.skill_order))
print("  bullets in scope: %d" % len(p.bullets))

print()
print("=" * 74)
print("STEP B - MODEL REWRITE (numbers masked, one bullet per call)")
print("=" * 74)
res = rewrite(p, jd, llm, vocab, cfg)
print("  rewritten: %d   skipped: %d   tokens: %d in / %d out"
      % (len(res.rewritten), len(res.skipped), res.tokens_in, res.tokens_out))
if res.skipped:
    for bid, why in res.skipped:
        print("   SKIPPED %s - %s" % (bid, why))

print()
print("=" * 74)
print("WHITELIST + DIFF (spec 15.2 / 17)")
print("=" * 74)
wl = Whitelist(resume.evidence_text(), vocab)
pairs = []
for rb in p.bullets:
    b = rb.bullet
    if b.id in res.rewritten:
        pairs.append((b.id, b.text, res.rewritten[b.id], b.line))
lines, check = build_diff(pairs, wl)

print("  checked %d bullets - gate %s  (%d high, %d low)"
      % (check.checked, "PASS" if check.ok else "BLOCKED",
         len(check.high), len(check.low)))
print()
for dl in lines:
    hi = [v for v in dl.violations if v.severity == "high"]
    flag = "!!" if hi else ("? " if dl.violations else ("~ " if dl.changed else "= "))
    print(" %s [%s  L%d]" % (flag, dl.bullet_id, dl.source_line))
    print("      was: %s" % dl.source[:96])
    print("      now: %s" % dl.tailored[:96])
    for v in dl.violations:
        print("      [%s] %s: %r" % (v.severity.upper(), v.kind, v.value))
    print()

if check.violations:
    print("  BY KIND:")
    for kind, vs in check.by_kind().items():
        print("   %-20s %d  %s" % (kind, len(vs), sorted({v.value for v in vs})[:8]))

print()
print("=" * 74)
print("NEGATIVE CONTROL - inject a skill the candidate does not have")
print("=" * 74)
fake = "Automated regression suites in Selenium and Cypress across microservices."
neg = Whitelist(resume.evidence_text(), vocab)
_, bad = build_diff([("fake.0", resume.experience[0].bullets[0].text, fake, 0)], neg)
print("  gate blocked: %s   (%d high, %d low)"
      % (not bad.ok, len(bad.high), len(bad.low)))
for v in bad.violations[:8]:
    print("   [%s] %s: %r" % (v.severity.upper(), v.kind, v.value))
