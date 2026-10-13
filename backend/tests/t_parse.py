"""Smoke test: parse the real main.tex and print the structured model."""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.latex_parser import parse

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
src = io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read()
r = parse(src)

print("SECTION ORDER: %s" % " > ".join(s.replace("\\&", "&") for s in r.section_order))
print()
print("SUMMARY (%d chars)" % len(r.summary))
print("  %s..." % r.summary[:100])
print()
print("SKILLS (%d lines)" % len(r.skills))
for s in r.skills:
    print("  L%-4d %-22s %d: %s" % (s.line, s.category, len(s.items), ", ".join(s.items)))
print()
for label, entries in (("EXPERIENCE", r.experience),
                       ("PROJECTS", r.projects),
                       ("EDUCATION", r.education)):
    print("%s (%d)" % (label, len(entries)))
    for e in entries:
        print("  L%-4d %s" % (e.line, e.title))
        print("        meta=%r date=%r" % (e.meta, e.date))
        if e.tech:
            print("        tech: %s" % ", ".join(e.tech))
        for b in e.bullets:
            print("        - [%s L%d] %s" % (b.id, b.line, b.text[:70]))
    print()
print("CERTIFICATIONS (%d)" % len(r.certifications))
for c in r.certifications:
    print("  L%-4d %s" % (c.line, c.plain[:78]))
print()
print("PUBLICATIONS (%d)" % len(r.publications))
for p in r.publications:
    print("  L%-4d %s" % (p.line, p.plain[:78]))
print()
print("TOTAL BULLETS: %d" % len(r.all_bullets()))
print("EVIDENCE CORPUS: %d chars" % len(r.evidence_text()))
print("WARNINGS: %s" % (r.warnings or "none"))
