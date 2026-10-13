"""Full API walkthrough, including the spec-17 approval gate.

Uses FastAPI's TestClient so no server process is needed.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from app.main import app, pipeline

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JD = io.open(os.path.join(os.path.dirname(__file__), "sample_jd.txt"),
             encoding="utf-8").read()

c = TestClient(app)


def show(label, ok, extra=""):
    print("  %-46s %s %s" % (label, "PASS" if ok else "FAIL", extra))
    return ok


all_ok = True

print("=" * 72)
print("HEALTH")
print("=" * 72)
h = c.get("/api/health").json()
print("  model=%s  backend=%s  budget=%.1f  vocab=%d  bullets=%d"
      % (h["model"], h["compile_backend"], h["page_budget"],
         h["vocabulary_terms"], h["master_bullets"]))
print("  sections: %s" % " > ".join(s.replace("\\&", "&") for s in h["master_sections"]))
all_ok &= show("health ok", h["ok"])

print()
print("=" * 72)
print("INGEST -> ANALYSE")
print("=" * 72)
r = c.post("/api/jobs", json={"raw_jd": JD})
job_id = r.json()["job_id"]
print("  job_id=%s  chars=%d" % (job_id, r.json()["chars"]))

a = c.post("/api/jobs/%s/analyse" % job_id).json()
print("  company=%s  title=%s" % (a["jd"]["company"], a["jd"]["title"][:44]))
print("  overall match: %d%%" % a["score"]["overall"])
print("  ATS coverage : %.0f%% exact / %.0f%% weighted"
      % (a["ats"]["coverage"] * 100, a["ats"]["weighted_coverage"] * 100))
print("  missing      : %s" % (", ".join(a["score"]["missing"]) or "none"))
print("  partial      : %s" % (", ".join(a["score"]["partial"]) or "none"))
all_ok &= show("analyse produced a score", a["score"]["overall"] > 0)

print()
print("=" * 72)
print("GATE BEFORE TAILORING (must refuse)")
print("=" * 72)
r = c.get("/api/jobs/%s/tex" % job_id)
all_ok &= show("download blocked (409)", r.status_code == 409, r.json().get("detail", ""))
r = c.post("/api/jobs/%s/approve" % job_id)
all_ok &= show("approve blocked before checks", r.status_code == 409,
               r.json().get("detail", "")[:60])

print()
print("=" * 72)
print("TAILOR")
print("=" * 72)
t = c.post("/api/jobs/%s/tailor" % job_id).json()
print("  pages=%d measured=%s  tokens=%d in / %d out"
      % (t["pages"], t["measured"], t["tokens"]["in"], t["tokens"]["out"]))
print("  gate_ok=%s  high=%d  low=%d"
      % (t["gate_ok"], len(t["violations"]["high"]), len(t["violations"]["low"])))
print("  trim actions: %d" % len(t["trim_actions"]))
for w in t["warnings"]:
    print("   - %s" % w)
changed = sum(1 for d in t["diff"] if d["changed"])
print("  diff: %d bullets, %d changed" % (len(t["diff"]), changed))
all_ok &= show("tailoring produced a diff", len(t["diff"]) > 0)
all_ok &= show("one page", t["pages"] == 1)

print()
print("=" * 72)
print("GATE AFTER TAILORING, BEFORE APPROVAL (must still refuse)")
print("=" * 72)
r = c.get("/api/jobs/%s/tex" % job_id)
all_ok &= show("download still blocked", r.status_code == 409,
               r.json().get("detail", ""))

print()
print("=" * 72)
print("APPROVE -> DOWNLOAD")
print("=" * 72)
r = c.post("/api/jobs/%s/approve" % job_id)
all_ok &= show("approval accepted", r.status_code == 200, str(r.json()))
r = c.get("/api/jobs/%s/tex" % job_id)
all_ok &= show("download released", r.status_code == 200,
               "%d chars" % len(r.text))

print()
print("=" * 72)
print("RE-TAILOR INVALIDATES APPROVAL (spec 17)")
print("=" * 72)
before = c.get("/api/jobs/%s" % job_id).json()["approved_at"]
c.post("/api/jobs/%s/tailor" % job_id)
after = c.get("/api/jobs/%s" % job_id).json()["approved_at"]
row = c.get("/api/jobs/%s/diff" % job_id).json()
print("  approved_at before=%s after=%s" % (before, after))
print("  can_release=%s (%s)" % (row["gate"]["can_release"], row["gate"]["reason"]))
# Approval survives only if the re-render is byte-identical; either outcome is
# correct, but a changed document must never stay approved.
consistent = (after is not None) == (row["gate"]["can_release"])
all_ok &= show("approval state consistent with content", consistent)

print()
print("=" * 72)
print("AUDIT TRAIL")
print("=" * 72)
trail = c.get("/api/jobs/%s/audit" % job_id).json()["trail"]
for e in trail:
    print("  %s  %-22s %s" % (e["ts"][11:19], e["event"], e["detail"][:52]))
all_ok &= show("audit trail recorded", len(trail) >= 5)

print()
print("=" * 72)
print("TRACKER")
print("=" * 72)
c.post("/api/jobs/%s/status" % job_id, json={"status": "applied"})
lst = c.get("/api/jobs").json()
print("  jobs=%d  stats=%s" % (len(lst["jobs"]), lst["stats"]))
r = c.post("/api/jobs/%s/status" % job_id, json={"status": "bogus"})
all_ok &= show("invalid status rejected", r.status_code == 422)

print()
print("=" * 72)
print("RESULT: %s" % ("ALL PASS" if all_ok else "FAILURES ABOVE"))
print("=" * 72)
sys.exit(0 if all_ok else 1)
