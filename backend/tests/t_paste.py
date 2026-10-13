"""Run a pasted JD through the pipeline, and compare against the fetched one.

The paste path is the reliable path per the agreed scope, so it gets its own
check. Comparing the two also validates the Workday adapter: if fetching and
pasting the same posting produce different analyses, the adapter is losing or
adding content.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from app.core.jd_fetch import fetch_jd
from app.main import app

HERE = os.path.dirname(__file__)
JD = io.open(os.path.join(HERE, "postman_jd.txt"), encoding="utf-8").read()
URL = ("https://postman.wd108.myworkdayjobs.com/careers/job/Bangalore-India/"
       "Software-Engineer--Fullstack---Collections_JR1000043?source=LinkedIn")

c = TestClient(app)


def run(label, payload):
    r = c.post("/api/jobs", json=payload)
    if r.status_code != 200:
        print("  %s INGEST FAILED %s" % (label, r.json()))
        return None, None
    jid = r.json()["job_id"]
    a = c.post("/api/jobs/%s/analyse" % jid).json()
    return jid, a


print("=" * 76)
print("PASTED JD")
print("=" * 76)
print("  %d chars" % len(JD))
jid, paste = run("paste", {"raw_jd": JD})
jd, sc, ats = paste["jd"], paste["score"], paste["ats"]

print("  company : %s" % (jd["company"] or "-"))
print("  title   : %s" % (jd["title"] or "-"))
print("  location: %s" % (jd["location"] or "-"))
print("  job id  : %s" % (jd["job_id"] or "-"))
print("  type    : %s   level: %s   category: %s"
      % (jd["employment_type"] or "-", jd["level"] or "-", jd["category"] or "-"))
print("  min exp : %s" % (jd["qualifications"]["min_experience_years"] or "-"))
print("  degree  : %s" % (jd["qualifications"]["degree"] or "-"))
print("  soft    : %s" % ", ".join(jd["soft_skills"][:6]))
print()
print("  REQUIREMENTS (%d required / %d preferred)"
      % (sum(1 for r in jd["requirements"] if r["kind"] == "required"),
         sum(1 for r in jd["requirements"] if r["kind"] == "preferred")))
for rq in jd["requirements"]:
    alts = " OR ".join(rq["keys"]) or "(no tech named)"
    flag = "!" if rq["unresolved"] else " "
    print("   %s [%-9s] %-44s -> %s" % (flag, rq["kind"], rq["text"][:44], alts[:32]))
    if rq["unresolved"]:
        print("       unresolved: %s" % ", ".join(rq["unresolved"]))
print()
for w in jd["warnings"]:
    print("  warning: %s" % w[:110])

print()
print("  OVERALL MATCH: %d%%" % sc["overall"])
for name, key, wk in (("Required skills", "required_score", "required_skills"),
                      ("Preferred skills", "preferred_score", "preferred_skills"),
                      ("Project relevance", "project_score", "project_relevance"),
                      ("Qualifications", "qualification_score", "qualifications")):
    raw, w = sc[key], sc["weights"][wk]
    print("   %-19s %5.2f x %.2f = %5.1f pts" % (name, raw, w, raw * w * 100))
print()
print("  STRONG : %s" % (", ".join(sc["strong"]) or "none"))
print("  PARTIAL: %s" % (", ".join(sc["partial"]) or "none"))
print("  MISSING: %s" % (", ".join(sc["missing"]) or "none"))
print()
print("  QUALIFICATIONS")
for q in sc["qualifications"]:
    print("   %-16s want=%-24s have=%-26s %s"
          % (q["name"], q["expected"][:24], q["found"][:26], q["status"]))
print()
print("  ATS %.0f%% exact / %.0f%% weighted   %s"
      % (ats["coverage"] * 100, ats["weighted_coverage"] * 100,
         "  ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in ats["by_bucket"].items())))
print("  ATS missing: %s" % ", ".join(ats["missing"]))
print()
print("  PROJECT RELEVANCE")
for p in sc["projects"]:
    print("   %.2f  %-40s %s" % (p["score"], p["title"][:40],
                                 ", ".join(p["matched"][:6]) or "no overlap"))

print()
print("=" * 76)
print("FETCHED (Workday API) - same posting, for comparison")
print("=" * 76)
try:
    text, _ = fetch_jd(URL)
    print("  %d chars fetched vs %d chars pasted" % (len(text), len(JD)))
    _, fetched = run("fetch", {"url": URL})
    fjd, fsc = fetched["jd"], fetched["score"]
    print()
    print("  %-22s %-34s %s" % ("", "PASTED", "FETCHED"))
    rows = [
        ("company", jd["company"], fjd["company"]),
        ("title", jd["title"], fjd["title"]),
        ("location", jd["location"], fjd["location"]),
        ("job id", jd["job_id"], fjd["job_id"]),
        ("requirements", len(jd["requirements"]), len(fjd["requirements"])),
        ("overall match", "%d%%" % sc["overall"], "%d%%" % fsc["overall"]),
        ("missing", ", ".join(sc["missing"]), ", ".join(fsc["missing"])),
    ]
    for name, a_, b_ in rows:
        same = "same" if str(a_) == str(b_) else "DIFFERS"
        print("  %-22s %-34s %-34s %s"
              % (name, str(a_)[:34], str(b_)[:34], same))
except Exception as e:
    print("  fetch failed: %s" % e)

print()
print("  job_id (pasted) = %s" % jid)
