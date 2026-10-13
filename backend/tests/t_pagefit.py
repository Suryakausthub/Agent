"""Page-budget calibration guard.

Ground truth: main.tex renders as exactly ONE page in Overleaf. The stub
compiler's own geometry coefficients were wrong (they scored it as two), so
the budget is derived from that known fact instead. This test asserts the
invariant the rest of the pipeline depends on:

    master == 1 page, and a tailored version must be no taller than master.

Written as a file, not a shell heredoc: backslash-bearing LaTeX macros do not
survive heredoc quoting reliably, and a corrupted \\resumeItem silently drops
bullets from the estimator's view, producing page counts that look like
progress and are not.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core import compile as cm
from app.core.latex_parser import parse

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SRC = io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read()

budget = cm.calibrate(SRC, known_pages=1)
master_lines = cm.line_equivalents(SRC)
master_pages = cm.estimate_pages(SRC, budget)[0]

print("CALIBRATION")
print("  master line-equivalents : %.1f" % master_lines)
print("  budget (master x %.2f)   : %.1f" % (cm._CALIBRATION_MARGIN, budget))
print("  master verdict           : %d page(s)" % master_pages)
ok = master_pages == 1
print("  invariant master == 1 page: %s" % ("PASS" if ok else "FAIL"))

tailored_path = os.path.join(ROOT, "output", "tailored.tex")
if os.path.exists(tailored_path):
    tex = io.open(tailored_path, encoding="utf-8").read()
    lines = cm.line_equivalents(tex)
    pages = cm.estimate_pages(tex, budget)[0]
    p = parse(tex)
    intact = (len(p.experience) == 2 and len(p.projects) >= 2
              and len(p.all_bullets()) > 0 and not p.warnings)
    print()
    print("TAILORED OUTPUT")
    print("  line-equivalents : %.1f  (%+.1f vs master, %+.1f%%)"
          % (lines, lines - master_lines, 100 * (lines - master_lines) / master_lines))
    print("  verdict          : %d page(s)" % pages)
    print("  outline preserved: sections=%d experience=%d projects=%d "
          "bullets=%d certs=%d pubs=%d"
          % (len(p.section_order), len(p.experience), len(p.projects),
             len(p.all_bullets()), len(p.certifications), len(p.publications)))
    print("  structure intact : %s" % ("PASS" if intact else "FAIL"))
    print("  no growth vs master: %s" % ("PASS" if lines <= budget else "FAIL"))
    ok = ok and intact and lines <= budget
else:
    print()
    print("  (run t_render.py first to produce output/tailored.tex)")

print()
print("RESULT: %s" % ("PASS" if ok else "FAIL"))
print()
print("Reminder: this is a calibrated ESTIMATE, not a measurement. It can tell")
print("you the tailored resume is no taller than a document known to fit; it")
print("cannot tell you the absolute page count. Tectonic replaces it.")
sys.exit(0 if ok else 1)
