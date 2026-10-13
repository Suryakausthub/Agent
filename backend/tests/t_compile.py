"""Real PDF compilation with Tectonic: measured page counts and the fallback.

Offline once Tectonic's packages are cached (the first run downloads them).
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core.compile import calibrate, compile_tex, xetex_compat

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"),
                          encoding="utf-8"))
ccfg = cfg["compile"]
src = io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read()
fails = 0


def check(cond, label):
    global fails
    print("  %s %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        fails += 1


print("compat shim")
c = xetex_compat(src)
check("glyphtounicode" not in c, "pdfTeX-only glyph mapping removed from the compiled copy")
check("★" not in c, "glyphs missing from the default font are mapped")
check("glyphtounicode" in src, "main.tex itself is untouched")

print("\ntectonic")
r = compile_tex(src, ccfg)
check(r.ok and r.measured and r.backend == "tectonic",
      "master compiles to a real PDF (%s)" % (r.errors or "ok"))
check(r.pages == cfg["resume"]["master_known_pages"],
      "master measures %d page(s), matching Overleaf" % r.pages)
check("Missing character" not in r.log, "no characters dropped by the font")

# Overflow: repeat the Experience ENTRIES (inside the existing list, so the
# document stays well-formed) until it must spill onto a second page.
i = src.index(r"\resumeSubHeadingListStart", src.index(r"\section{Experience}"))
i += len(r"\resumeSubHeadingListStart")
j = src.index(r"\section{Projects}")
long_src = src[:j] + src[i:j] * 3 + src[j:]
r2 = compile_tex(long_src, ccfg)
check(r2.ok and r2.pages >= 2, "overflowing document measures %d pages" % r2.pages)

print("\ntrim loop on a measured overflow")
from app.core.jd_extract import JobDescription, Requirement
from app.core.latex_parser import parse
from app.core.tailor import TailorResult, plan
from app.core.trim import fit_to_pages
from app.core.vocabulary import Vocabulary
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
# One extra experience entry: just over a page, which the configured cuts can
# legitimately recover. (The tripled document above is beyond any allowed
# trim; the loop correctly stops and flags it instead of shrinking type.)
k = src.index(r"\resumeSubheading", i)
k2 = src.index(r"\resumeSubheading", k + 5)
long_resume = parse(src[:j] + src[k:k2] + src[j:])
check(compile_tex(long_resume.source, ccfg).pages == 2,
      "one extra entry pushes the resume to 2 measured pages")
jd = JobDescription(raw="", requirements=[Requirement(
    text="Java Spring Boot", kind="required",
    keys=sorted(vocab.extract("Java Spring Boot")))])
p = plan(long_resume, jd, vocab, cfg)
out = fit_to_pages(long_resume, p, TailorResult(plan=p), cfg)
for a in out.actions:
    print("    %-36s %-34s %d -> %d" % (a.step, a.target[:34], a.pages_before, a.pages_after))
check(out.measured and out.fits and out.pages == 1 and out.actions,
      "2-page document trimmed to 1 MEASURED page in %d step(s)" % len(out.actions))
final = parse(out.tex)
check(len(final.education) == len(long_resume.education)
      and len(final.certifications) == len(long_resume.certifications)
      and len(final.publications) == len(long_resume.publications),
      "protected sections survive the trim")

print("\nfallback")
budget = calibrate(src, 1)
bad = dict(ccfg, tectonic_path="tools/does-not-exist.exe")
os.environ["PATH"] = ""          # make sure no tectonic on PATH either
r3 = compile_tex(src, dict(bad, fallback_to_stub=True), lines_per_page=budget)
check(r3.ok and not r3.measured and r3.pages == 1,
      "missing tectonic falls back to the estimate, labelled unmeasured")
r4 = compile_tex(src, dict(bad, fallback_to_stub=False))
check(not r4.ok, "without fallback, a missing tectonic fails loudly")
broken = src.replace(r"\end{document}", r"\undefinedmacro \end{document}")
os.environ["PATH"] = os.defpath
r5 = compile_tex(broken, dict(ccfg, fallback_to_stub=True), lines_per_page=budget)
check(not r5.ok and r5.backend == "tectonic",
      "a broken document FAILS - it is never papered over with an estimate")

print("\nFAILED: %d" % fails if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
