"""End-to-end run against a real job posting URL."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from app.core.jd_fetch import fetch_jd, workday_api_url
from app.main import app

URL = sys.argv[1] if len(sys.argv) > 1 else (
    "https://postman.wd108.myworkdayjobs.com/careers/job/Bangalore-India/"
    "Software-Engineer--Fullstack---Collections_JR1000043?source=LinkedIn")

print("=" * 76)
print("FETCH")
print("=" * 76)
print("  url : %s" % URL[:96])
api = workday_api_url(URL)
print("  api : %s" % (api[:96] if api else "(not Workday)"))
text, final = fetch_jd(URL)
print("  got %d chars" % len(text))
print("  ---- first 420 chars ----")
for line in text[:420].splitlines():
    print("  | %s" % line[:88])

c = TestClient(app)
r = c.post("/api/jobs", json={"url": URL})
if r.status_code != 200:
    print("  INGEST FAILED: %s" % r.json())
    sys.exit(1)
job_id = r.json()["job_id"]

print()
print("=" * 76)
print("ANALYSE")
print("=" * 76)
a = c.post("/api/jobs/%s/analyse" % job_id).json()
jd, sc, ats = a["jd"], a["score"], a["ats"]
print("  company : %s" % (jd["company"] or "-"))
print("  title   : %s" % (jd["title"] or "-"))
print("  location: %s" % (jd["location"] or "-"))
print("  job id  : %s" % (jd["job_id"] or "-"))
print("  type=%s  level=%s  category=%s"
      % (jd["employment_type"] or "-", jd["level"] or "-", jd["category"] or "-"))
print("  degree  : %s" % (jd["qualifications"]["degree"] or "-"))
print("  min exp : %s" % (jd["qualifications"]["min_experience_years"] or "-"))
print()
print("  REQUIREMENTS (%d)" % len(jd["requirements"]))
for rq in jd["requirements"]:
    alts = " OR ".join(rq["keys"]) or "(no tech)"
    flag = "!" if rq["unresolved"] else " "
    print("   %s [%-9s] %-42s -> %s" % (flag, rq["kind"], rq["text"][:42], alts[:34]))
if jd["warnings"]:
    print("  warnings:")
    for w in jd["warnings"]:
        print("   - %s" % w[:100])

print()
print("  OVERALL MATCH: %d%%" % sc["overall"])
print("  %-20s %7s %7s %8s" % ("component", "raw", "weight", "points"))
for name, raw, w, pts in [
    ("Required skills", sc["required_score"], sc["weights"]["required_skills"], 0),
    ("Preferred skills", sc["preferred_score"], sc["weights"]["preferred_skills"], 0),
    ("Project relevance", sc["project_score"], sc["weights"]["project_relevance"], 0),
    ("Qualifications", sc["qualification_score"], sc["weights"]["qualifications"], 0)]:
    print("  %-20s %7.2f %7.2f %8.1f" % (name, raw, w, raw * w * 100))
print()
print("  STRONG : %s" % (", ".join(sc["strong"]) or "none"))
print("  PARTIAL: %s" % (", ".join(sc["partial"]) or "none"))
print("  MISSING: %s" % (", ".join(sc["missing"]) or "none"))
print()
print("  ATS: %.0f%% exact / %.0f%% weighted   %s"
      % (ats["coverage"] * 100, ats["weighted_coverage"] * 100,
         " ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in ats["by_bucket"].items())))
print("  ATS missing: %s" % (", ".join(ats["missing"]) or "none"))
print()
print("  PROJECT RELEVANCE")
for p in sc["projects"]:
    print("   %.2f  %-40s %s" % (p["score"], p["title"][:40], ", ".join(p["matched"][:6])))
print()
for n in sc["notes"]:
    print("  note: %s" % n[:110])

print()
print("=" * 76)
print("TAILOR")
print("=" * 76)
t = c.post("/api/jobs/%s/tailor" % job_id).json()
print("  pages=%d measured=%s  gate_ok=%s  high=%d low=%d  tokens=%d/%d"
      % (t["pages"], t["measured"], t["gate_ok"], len(t["violations"]["high"]),
         len(t["violations"]["low"]), t["tokens"]["in"], t["tokens"]["out"]))
for a_ in t["trim_actions"]:
    print("   trim: %s -> %s" % (a_["step"], a_["target"][:40]))
print()
for d in t["diff"]:
    hi = [v for v in d["violations"] if v["severity"] == "high"]
    flag = "!!" if hi else ("~" if d["changed"] else "=")
    print(" %s [%s]" % (flag, d["bullet_id"]))
    print("     was: %s" % d["source"][:92])
    print("     now: %s" % d["tailored"][:92])
    for v in d["violations"]:
        print("     [%s] %s: %r" % (v["severity"].upper(), v["kind"], v["value"]))
if t["violations"]["high"]:
    print()
    print("  HIGH VIOLATIONS -> gate blocked")
    for v in t["violations"]["high"]:
        print("   %s: %r (%s)" % (v["kind"], v["value"], v["bullet_id"]))

print()
print("=" * 76)
print("GATE")
print("=" * 76)
g = c.get("/api/jobs/%s/diff" % job_id).json()["gate"]
print("  can_release=%s  reason=%s" % (g["can_release"], g["reason"]))
print("  job_id=%s" % job_id)
