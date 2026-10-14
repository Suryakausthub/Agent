"""FastAPI application.

Local, single-user, no auth -- per the agreed scope. Binds to 127.0.0.1 only.

The one rule enforced at this layer: /download and the apply/send actions are
refused unless BOTH the automated checks passed and a human approved the diff
(spec section 17). The gate lives in db.Store.can_release so it cannot be
bypassed by calling a different endpoint.
"""

from __future__ import annotations

import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .core.compile import CompileError, render_pdf
from .core.jd_fetch import FetchError, fetch_jd
from .core.knowledge_base import authorship_flagged
from .core.knowledge_base import usage as kb_usage
from .core.referral import EMAIL, FOLLOWUP, LINKEDIN, ReferralRequest, generate
from .pipeline import Pipeline, to_jsonable
from .progress import STAGES, broker

API_VERSION = 4          # 4: job details edit; 3: PDF, library usage, all-project selection

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")

pipeline = Pipeline(
    config_path=os.path.join(BACKEND, "data", "config.yaml"),
    master_tex_path=os.path.join(ROOT, "main.tex"),
    vocab_path=os.path.join(BACKEND, "data", "vocabulary.yaml"),
    db_path=os.path.join(ROOT, "job_agent.db"),
    env_path=os.path.join(ROOT, ".env"),
)

app = FastAPI(title="Job Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------- models

class IngestRequest(BaseModel):
    raw_jd: str = Field("", description="Pasted job description text")
    url: str = Field("", description="Job posting URL (best-effort fetch)")


class StatusRequest(BaseModel):
    status: str


class NotesRequest(BaseModel):
    notes: str


class ReferralBody(BaseModel):
    kind: str = Field(LINKEDIN, description="linkedin | email | followup")
    recipient: str = ""
    recipient_role: str = ""
    supplied_facts: str = Field(
        "", description="Real details about the recipient, supplied by the "
                        "user. Passed through verbatim; never invented.")
    candidate_name: str = ""
    candidate_email: str = ""
    job_url: str = ""


# --------------------------------------------------------------------- routes

@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        # Bumped whenever the UI depends on a new endpoint, so a UI served by
        # an older, still-running server can say "restart" instead of failing
        # in confusing ways.
        "api_version": API_VERSION,
        "github_projects_in_use": len(pipeline.knowledge_rows()),
        "model": pipeline.cfg["llm"]["model"],
        "compile_backend": pipeline.cfg["compile"]["backend"],
        "page_budget": round(pipeline.page_budget, 1),
        "vocabulary_terms": len(pipeline.vocab.terms),
        "master_bullets": len(pipeline.master.all_bullets()),
        "master_sections": pipeline.master.section_order,
    }


@app.get("/api/resume/master")
def master_resume() -> dict:
    m = pipeline.master
    return {
        "sections": m.section_order,
        "summary": m.summary,
        "skills": to_jsonable(m.skills),
        "experience": to_jsonable(m.experience),
        "projects": to_jsonable(m.projects),
        "education": to_jsonable(m.education),
        "certifications": to_jsonable(m.certifications),
        "publications": to_jsonable(m.publications),
        "warnings": m.warnings,
    }


@app.post("/api/jobs")
def create_job(req: IngestRequest) -> dict:
    raw = req.raw_jd.strip()
    source = ""
    if not raw and req.url:
        try:
            raw, source = fetch_jd(req.url)
        except FetchError as e:
            raise HTTPException(
                422, "could not fetch that URL (%s). Paste the description "
                     "instead -- pasting is the reliable path." % e)
    if not raw:
        raise HTTPException(422, "provide raw_jd or a fetchable url")
    job_id = pipeline.ingest(raw, source or req.url)
    return {"job_id": job_id, "chars": len(raw), "source_url": source or req.url}


@app.get("/api/jobs")
def list_jobs(limit: int = 100, status: str | None = None) -> dict:
    return {"jobs": pipeline.store.list_jobs(limit=limit, status=status),
            "stats": pipeline.store.stats()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    row = pipeline.store.get(job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    for col in ("jd_json", "score_json", "ats_json", "tailor_json",
                "diff_json", "referral_json"):
        row[col] = pipeline.store.get_json(job_id, col)
    row.pop("tailored_tex", None)      # fetched via /tex, which is gated
    return row


@app.post("/api/jobs/{job_id}/analyse")
def analyse(job_id: str, force: bool = False) -> dict:
    """force=true re-runs extraction instead of reusing the stored one."""
    try:
        return pipeline.analyse(job_id, force=force)
    except KeyError:
        raise HTTPException(404, "no such job")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{job_id}/tailor")
def tailor(job_id: str) -> dict:
    try:
        return pipeline.tailor(job_id)
    except KeyError:
        raise HTTPException(404, "no such job")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/jobs/{job_id}/diff")
def diff(job_id: str) -> dict:
    data = pipeline.store.get_json(job_id, "diff_json")
    if data is None:
        raise HTTPException(404, "nothing tailored yet")
    row = pipeline.store.get(job_id)
    allowed, reason = pipeline.store.can_release(job_id)
    return {**data, "gate": {"can_release": allowed, "reason": reason,
                             "approved_at": row["approved_at"]}}


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: str) -> dict:
    ok, reason = pipeline.store.approve(job_id)
    if not ok:
        raise HTTPException(409, reason)
    return {"approved": True, "reason": reason}


@app.get("/api/jobs/{job_id}/tex", response_class=PlainTextResponse)
def download_tex(job_id: str) -> PlainTextResponse:
    allowed, reason, tex = pipeline.release(job_id)
    if not allowed:
        # 409, not 403: the request is well-formed, the workflow state is wrong.
        raise HTTPException(409, "blocked: %s" % reason)
    return PlainTextResponse(
        tex, headers={"Content-Disposition":
                      'attachment; filename="%s.tex"' % _resume_name(job_id)})


_LEGAL = re.compile(
    r"\b(inc|incorporated|llc|llp|ltd|limited|pvt|private|plc|corp|corporation|"
    r"co|company|gmbh|ag|sa|bv|india|technologies|technology|solutions|services|"
    r"group|holdings|platforms|systems)\b\.?", re.I)


def short_company(company: str) -> str:
    """The name people use for the company, for file names.

    'Deloitte Touche Tohmatsu India LLP' -> 'Deloitte', 'Zenovo Inc' ->
    'Zenovo', 'Postman (Postdot Technologies ...)' -> 'Postman',
    'Goldman Sachs' -> 'Goldman Sachs'. Legal suffixes and parentheticals are
    dropped; a name still longer than two words keeps its first word.
    """
    name = re.sub(r"\(.*?\)", " ", company or "")
    name = _LEGAL.sub(" ", name)
    words = [w for w in re.split(r"[\s,&]+", name) if w]
    if len(words) > 2:
        words = words[:1]
    return " ".join(words)


def _resume_name(job_id: str) -> str:
    """'Surya_Kausthub_Deloitte_Resume' - the agreed convention:
    <file name>_<Company>_Resume, underscores, no version or date suffixes."""
    row = pipeline.store.get(job_id) or {}
    cand = pipeline.cfg.get("candidate", {})
    person = cand.get("file_name") or cand.get("name", "")
    parts = [person, short_company(row.get("company") or ""), "Resume"]
    slug = "_".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_") or "Resume"


_PDF_CACHE = os.path.join(ROOT, "output", "pdf")


def _pdf(tex: str, draft: bool) -> str:
    try:
        return render_pdf(tex, pipeline.cfg["compile"], _PDF_CACHE, draft=draft)
    except CompileError as e:
        raise HTTPException(500, str(e))


@app.get("/api/jobs/{job_id}/pdf")
def download_pdf(job_id: str) -> FileResponse:
    """The finished resume as a PDF. Same gate as the .tex download."""
    allowed, reason, tex = pipeline.release(job_id)
    if not allowed:
        raise HTTPException(409, "blocked: %s" % reason)
    return FileResponse(_pdf(tex, draft=False), media_type="application/pdf",
                        filename="%s.pdf" % _resume_name(job_id))


@app.get("/api/jobs/{job_id}/preview.pdf")
def preview_pdf(job_id: str) -> FileResponse:
    """Inline preview for reviewing. Watermarked DRAFT until approved, so a
    clean file is only ever produced through the approval gate."""
    row = pipeline.store.get(job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    if not row["tailored_tex"]:
        raise HTTPException(409, "nothing tailored yet")
    allowed, _ = pipeline.store.can_release(job_id)
    path = _pdf(row["tailored_tex"], draft=not allowed)
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": "inline",
                                 "Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/audit")
def audit(job_id: str) -> dict:
    row = pipeline.store.get(job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    return {"job_id": job_id, "trail": pipeline.store.audit_trail(job_id)}


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, req: StatusRequest) -> dict:
    try:
        pipeline.store.set_status(job_id, req.status)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"status": req.status}


@app.post("/api/jobs/{job_id}/notes")
def set_notes(job_id: str, req: NotesRequest) -> dict:
    pipeline.store.update(job_id, notes=req.notes)
    return {"ok": True}


class DetailsRequest(BaseModel):
    company: str | None = None
    title: str | None = None
    location: str | None = None


@app.post("/api/jobs/{job_id}/details")
def set_details(job_id: str, req: DetailsRequest) -> dict:
    """Fill in or correct company / role / location.

    Extraction only keeps a title that literally appears in the posting, so a
    description pasted without its heading has no title. The user supplies
    it here. The stored extraction is updated too, so a cached re-analysis
    and the referral messages use the same values.
    """
    row = pipeline.store.get(job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    fields = {k: v.strip() for k, v in req.model_dump().items()
              if v is not None}
    if not fields:
        return {"ok": True, "changed": []}
    pipeline.store.update(job_id, **fields)
    jd = pipeline.store.get_json(job_id, "jd_json")
    if jd is not None:
        jd.update(fields)
        pipeline.store.save_json(job_id, "jd_json", jd)
    pipeline.store.log(job_id, "details_edited",
                       ", ".join("%s=%s" % kv for kv in fields.items()))
    return {"ok": True, "changed": sorted(fields)}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    if not pipeline.store.delete(job_id):
        raise HTTPException(404, "no such job")
    return {"deleted": True}


# ------------------------------------------------------- live pipeline view

@app.get("/api/stages")
def stages() -> dict:
    """Stage ids and labels, so the UI can lay out the pipeline before it runs."""
    return {"stages": [{"id": k, "label": v} for k, v in STAGES]}


@app.get("/api/jobs/{job_id}/stream")
def stream(job_id: str) -> StreamingResponse:
    """Server-sent pipeline progress: replays history, then follows live."""
    return StreamingResponse(
        broker.stream(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# --------------------------------------------------------- referral (spec 4)

@app.post("/api/jobs/{job_id}/referral")
def referral(job_id: str, body: ReferralBody) -> dict:
    row = pipeline.store.get(job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    jd_raw = pipeline.store.get_json(job_id, "jd_json")
    if jd_raw is None:
        raise HTTPException(400, "analyse the job first")
    if body.kind not in (LINKEDIN, EMAIL, FOLLOWUP):
        raise HTTPException(422, "kind must be linkedin, email or followup")

    from .pipeline import _rehydrate_jd
    jd = _rehydrate_jd(jd_raw, row["raw_jd"])
    cand = pipeline.cfg.get("candidate", {}) or {}
    msg = generate(
        pipeline.master, jd, pipeline.vocab, pipeline.whitelist,
        pipeline.llm, pipeline.cfg,
        ReferralRequest(
            kind=body.kind, recipient=body.recipient,
            recipient_role=body.recipient_role,
            supplied_facts=body.supplied_facts,
            candidate_name=body.candidate_name or cand.get("name", ""),
            candidate_email=body.candidate_email or cand.get("email", ""),
            candidate_standing=cand.get("standing", ""),
            job_url=body.job_url or row["source_url"] or ""))

    stored = pipeline.store.get_json(job_id, "referral_json") or {}
    stored[body.kind] = to_jsonable(msg)
    pipeline.store.save_json(job_id, "referral_json", stored)
    pipeline.store.log(job_id, "referral_generated",
                       "%s - %s" % (body.kind, msg.reason))
    return to_jsonable(msg)


# ------------------------------------------ checks & validation (spec 7)

def _item(label: str, ok: bool, detail: str, warn: bool = False) -> dict:
    return {"label": label,
            "status": "ok" if ok else ("warn" if warn else "fail"),
            "detail": detail}


@app.get("/api/jobs/{job_id}/checks")
def checks(job_id: str) -> dict:
    """Everything the Checks & Validation panel needs, in one call."""
    row = pipeline.store.get(job_id)
    if row is None:
        raise HTTPException(404, "no such job")

    diff = pipeline.store.get_json(job_id, "diff_json") or {}
    tailor = pipeline.store.get_json(job_id, "tailor_json") or {}
    score = pipeline.store.get_json(job_id, "score_json") or {}
    ats = pipeline.store.get_json(job_id, "ats_json") or {}
    allowed, reason = pipeline.store.can_release(job_id)
    max_pages = pipeline.cfg["resume"]["max_pages"]

    groups: list[dict] = [{
        "name": "Resume structure",
        "items": [_item(
            "Master LaTeX parses cleanly",
            not pipeline.master.warnings,
            "; ".join(pipeline.master.warnings) or
            "%d bullets across %d sections"
            % (len(pipeline.master.all_bullets()),
               len(pipeline.master.section_order)),
            warn=True)],
    }]

    if diff:
        high = diff.get("high", [])
        low = diff.get("low", [])
        nums = [v for v in high if v.get("kind") == "invented_number"]
        groups.append({"name": "Factual accuracy", "items": [
            _item("No unsupported technologies", not high,
                  ", ".join(v["value"] for v in high) or
                  "%d bullets traced to your evidence" % diff.get("checked", 0)),
            _item("No invented metrics", not nums,
                  "numbers are masked before the model sees them"),
            _item("Wording notes", not low,
                  ", ".join(sorted({v["value"] for v in low})) or "none",
                  warn=True),
        ]})

    if tailor:
        pages = tailor.get("pages", 99)
        groups.append({"name": "Page length", "items": [
            _item("Fits %d page(s)" % max_pages, pages <= max_pages,
                  "%s page(s)" % pages),
            _item("Measured, not estimated", bool(tailor.get("measured")),
                  "compile backend: %s%s" % (
                      pipeline.cfg["compile"]["backend"],
                      "" if tailor.get("measured")
                      else " - page count is an estimate"),
                  warn=True),
        ]})

    if score:
        groups.append({"name": "JD alignment", "items": [
            _item("Required skills covered",
                  score.get("required_score", 0) >= 0.8,
                  "%.0f%% of required demands"
                  % (score.get("required_score", 0) * 100), warn=True),
            _item("Gaps surfaced, not hidden", True,
                  ", ".join(score.get("missing", [])) or "no gaps found"),
        ]})

    if ats:
        groups.append({"name": "ATS readiness", "items": [
            _item("Keyword coverage", ats.get("coverage", 0) >= 0.7,
                  "%.0f%% exact match" % (ats.get("coverage", 0) * 100),
                  warn=True),
            _item("Keyword placement", not ats.get("placement_warnings"),
                  "; ".join(ats.get("placement_warnings", [])[:2]) or
                  "keywords sit in high-weight sections", warn=True),
        ]})

    groups.append({"name": "Approval gate (spec 17)", "items": [
        _item("Automated checks passed", bool(row["gate_ok"]),
              "required before approval is even possible"),
        _item("Human review recorded", bool(row["approved_at"]),
              row["approved_at"] or "not yet approved", warn=True),
        _item("Release allowed", allowed, reason),
    ]})

    return {"groups": groups, "can_release": allowed, "reason": reason}


# --------------------------------------------- project bank (review gate)

class ReviewBody(BaseModel):
    status: str = Field(..., description="approved | rejected | needs_review")


class ProjectEditBody(BaseModel):
    title: str | None = None
    description: str | None = None
    bullets: list[str] | None = None


class ClassifyBody(BaseModel):
    classification: str = Field(..., description="keep | review | exclude")


@app.get("/api/projects")
def list_projects(classification: str | None = None,
                  extracted_only: bool = False) -> dict:
    rows = pipeline.store.list_projects(classification)
    if extracted_only:
        rows = [r for r in rows if r.get("project_json")]
    bank_cfg = pipeline.cfg.get("project_bank", {})
    in_use = 0
    for r in rows:
        r.pop("evidence_json", None)      # bulky; fetched per project
        if r.get("project_json"):
            import json as _json
            r["project_json"] = _json.loads(r["project_json"])
        ok, why = kb_usage(r, bank_cfg)
        r["usage"] = {"in_use": ok, "reason": why,
                      "flagged": authorship_flagged(r.get("project_json"))}
        in_use += ok
    return {
        "projects": rows,
        "counts": pipeline.store.project_counts(),
        "review": pipeline.store.project_review_counts(),
        "in_use": in_use,
    }


@app.get("/api/projects/{project_id:path}")
def get_project(project_id: str) -> dict:
    row = pipeline.store.get_project(project_id)
    if row is None:
        raise HTTPException(404, "no such project")
    import json as _json
    if row.get("project_json") and isinstance(row["project_json"], str):
        row["project_json"] = _json.loads(row["project_json"])
    # Evidence is returned without file bodies: the reviewer needs to know
    # WHAT was read, not to scroll through it.
    ev = row.get("evidence_json") or {}
    if ev:
        row["evidence_json"] = {
            k: v for k, v in ev.items() if k != "files"}
        row["evidence_json"]["files"] = [
            {"path": f.get("path"), "kind": f.get("kind"),
             "chars": len(f.get("content") or "")}
            for f in (ev.get("files") or [])]
    ok, why = kb_usage(row, pipeline.cfg.get("project_bank", {}))
    row["usage"] = {"in_use": ok, "reason": why,
                    "flagged": authorship_flagged(row.get("project_json"))}
    return row


@app.post("/api/projects/{project_id:path}/review")
def review_project(project_id: str, body: ReviewBody) -> dict:
    try:
        ok = pipeline.store.set_project_review(project_id, body.status)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not ok:
        raise HTTPException(404, "no such project")
    return {"ok": True, "status": body.status}


@app.post("/api/projects/{project_id:path}/edit")
def edit_project(project_id: str, body: ProjectEditBody) -> dict:
    ok = pipeline.store.edit_project(
        project_id, title=body.title, description=body.description,
        bullets=body.bullets)
    if not ok:
        raise HTTPException(404, "no such project")
    return {"ok": True, "note": "review status reset to needs_review"}


@app.post("/api/projects/{project_id:path}/classify")
def classify_project(project_id: str, body: ClassifyBody) -> dict:
    try:
        ok = pipeline.store.set_project_override(project_id,
                                                 body.classification)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not ok:
        raise HTTPException(404, "no such project")
    return {"ok": True, "classification": body.classification}


# --------------------------------------------------------------- static UI
#
# The built React app is served from this same process, so running the tool is
# one command on one port. Vite is only needed when developing the frontend
# (npm run dev, which proxies /api back here).
#
# Mounted LAST so every /api route above wins; the catch-all below would
# otherwise swallow them.

_DIST = os.path.join(ROOT, "frontend", "dist")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> FileResponse:
    """Serve the built UI, falling back to index.html for client routes."""
    if not os.path.isdir(_DIST):
        raise HTTPException(
            503, "UI is not built yet. Run: cd frontend && npm run build "
                 "(or use run.cmd, which builds it for you).")

    # An unknown /api path is a real error, never the UI page. Serving
    # index.html here made a missing endpoint look like success: an outdated
    # server answered "Download PDF" with a web page and HTTP 200.
    if full_path.startswith("api/") or full_path == "api":
        raise HTTPException(404, "no such API endpoint: /%s - if you just "
                                 "updated Job Agent, restart it (run.cmd)"
                            % full_path)

    candidate = os.path.normpath(os.path.join(_DIST, full_path))
    if candidate.startswith(_DIST) and os.path.isfile(candidate):
        return FileResponse(candidate)
    return FileResponse(os.path.join(_DIST, "index.html"))
