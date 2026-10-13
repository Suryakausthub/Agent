"""Render + trim, with a re-parse of the output as the structural check.

The strongest available test without a working compiler: render the tailored
resume, then parse the RESULT with the same parser and confirm the document is
still structurally intact and the intended edits actually landed.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core import compile as compile_mod
from app.core.jd_extract import extract
from app.core.latex_parser import parse
from app.core.llm import LLM
from app.core.render import render
from app.core.tailor import plan, rewrite
from app.core.trim import fit_to_pages
from app.core.vocabulary import Vocabulary

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"),
                          encoding="utf-8"))
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
src = io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read()
resume = parse(src)
llm = LLM(cfg["llm"], env_path=os.path.join(ROOT, ".env"))
JD = io.open(os.path.join(os.path.dirname(__file__), "sample_jd.txt"),
             encoding="utf-8").read()

print("=" * 74)
print("BASELINE - master resume page estimate")
print("=" * 74)
base_pages, base_lines = compile_mod.estimate_pages(src)
print("  %.1f line-equivalents -> %d page(s)  [ESTIMATE, not measured]"
      % (base_lines, base_pages))

jd = extract(JD, llm, vocab)
p = plan(resume, jd, vocab, cfg)
res = rewrite(p, jd, llm, vocab, cfg)
print("  rewrote %d bullets" % len(res.rewritten))

print()
print("=" * 74)
print("RENDER - splice into main.tex")
print("=" * 74)
r = render(resume, p, res)
print("  edits: %s" % r.summary())
print("  output: %d chars (master %d)" % (len(r.tex), len(src)))
print("  warnings: %s" % (r.warnings or "none"))

print()
print("  RE-PARSE THE RENDERED OUTPUT")
re_parsed = parse(r.tex)
print("    sections:   %d (master %d)" % (len(re_parsed.section_order), len(resume.section_order)))
print("    experience: %d (master %d)" % (len(re_parsed.experience), len(resume.experience)))
print("    projects:   %d (master %d)" % (len(re_parsed.projects), len(resume.projects)))
print("    bullets:    %d (master %d)" % (len(re_parsed.all_bullets()), len(resume.all_bullets())))
print("    certs:      %d (master %d)" % (len(re_parsed.certifications), len(resume.certifications)))
print("    pubs:       %d (master %d)" % (len(re_parsed.publications), len(resume.publications)))
print("    warnings:   %s" % (re_parsed.warnings or "none"))
print("    skill order: %s" % " > ".join(s.category for s in re_parsed.skills))

changed = sum(1 for b in re_parsed.all_bullets()
              if b.text not in {x.text for x in resume.all_bullets()})
print("    bullets differing from master: %d" % changed)

print()
print("=" * 74)
print("TRIM LOOP")
print("=" * 74)
out = fit_to_pages(resume, p, res, cfg)
print("  pages: %d   fits: %s   measured: %s   attempts: %d"
      % (out.pages, out.fits, out.measured, out.attempts))
if out.actions:
    print("  ACTIONS TAKEN:")
    for a in out.actions:
        print("   %-38s %-28s %d -> %d p"
              % (a.step, a.target[:28], a.pages_before, a.pages_after))
else:
    print("  no trimming needed")
print("  warnings:")
for w in out.warnings:
    print("   - %s" % w)

print()
print("=" * 74)
print("FORCED OVERFLOW - trim loop under pressure")
print("=" * 74)
tight = yaml.safe_load(yaml.safe_dump(cfg))
tight["compile"]["backend"] = "stub"
# Shrink the page budget so the loop has to act on this resume. The budget is
# calibrated from main.tex inside trim.fit_to_pages, so it is that calibration
# that must be squeezed -- shrinking compile._LINES_PER_PAGE (the old approach)
# no longer had any effect and this section silently stopped testing anything.
import app.core.trim as trim_mod
trim_mod.calibrate = lambda src, pages: 60.0
p2 = plan(resume, jd, vocab, cfg)
res2 = rewrite(p2, jd, llm, vocab, cfg) if False else res
out2 = fit_to_pages(resume, p2, res2, tight)
print("  pages: %d   fits: %s   attempts: %d" % (out2.pages, out2.fits, out2.attempts))
for a in out2.actions:
    print("   %-38s %-30s %d -> %d p"
          % (a.step, a.target[:30], a.pages_before, a.pages_after))
for w in out2.warnings:
    print("   - %s" % w)

print()
print("  protected sections survived the squeeze:")
final = parse(out2.tex)
print("    Certifications: %d   Publications: %d   Education: %d"
      % (len(final.certifications), len(final.publications), len(final.education)))
print("    structural warnings: %s" % (final.warnings or "none"))
squeeze_ok = (out2.attempts > 0 and out2.actions
              and len(final.certifications) == len(resume.certifications)
              and len(final.publications) == len(resume.publications)
              and len(final.education) == len(resume.education)
              and not final.warnings)
print("  trim loop acted and protected sections survived: %s"
      % ("PASS" if squeeze_ok else "FAIL"))

out_dir = os.path.join(ROOT, "output")
os.makedirs(out_dir, exist_ok=True)
with io.open(os.path.join(out_dir, "tailored.tex"), "w",
             encoding="utf-8", newline="\n") as fh:
    fh.write(r.tex)
print()
print("  wrote output/tailored.tex")
sys.exit(0 if squeeze_ok else 1)
