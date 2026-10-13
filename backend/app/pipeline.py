"""Orchestrates the MVP pipeline and owns the shared, expensive objects.

    JD -> Summary -> Score -> Project Match -> ATS -> Tailor -> Validate
       -> Diff -> (human approval) -> Download

The master resume and vocabulary are parsed once at startup, not per request;
they are read-only during a run. Each stage persists its own artefact so the UI
can render partial progress and so a failed tailor does not lose the score.

Every stage also announces itself through `progress.broker`, which the UI
follows over SSE. These calls take seconds to minutes -- tailoring makes one
model call per bullet -- and without that the interface is just a spinner.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, is_dataclass
from typing import Any

import yaml

from .core import ats as ats_mod
from .core.compile import calibrate
from .core.jd_extract import JobDescription, extract
from .core.latex_parser import parse as parse_resume
from .core.llm import LLM
from .core.model import Resume
from .core.scoring import score as score_resume
from .core.knowledge_base import usage as kb_usage
from .core.project_select import _same_project, bank_entry
from .core.project_select import select as select_projects
from .core.tailor import plan as plan_tailor
from .core.tailor import rewrite as rewrite_bullets
from .core.tailor import rewrite_summary
from .core.trim import fit_to_pages
from .core.vocabulary import Vocabulary
from .core.whitelist import Whitelist, build_diff
from .db import Store
from .progress import FAIL, OK, WARN, Stage, broker


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return obj


class Pipeline:
    def __init__(self, config_path: str, master_tex_path: str,
                 vocab_path: str, db_path: str, env_path: str | None = None):
        with open(config_path, encoding="utf-8") as fh:
            self.cfg = yaml.safe_load(fh)
        with open(master_tex_path, encoding="utf-8") as fh:
            self.master_src = fh.read()

        self.master: Resume = parse_resume(self.master_src)
        self.vocab = Vocabulary.load(vocab_path)
        self.store = Store(db_path)
        self.llm = LLM(self.cfg["llm"], env_path=env_path)
        self.whitelist = Whitelist(self.master.evidence_text(), self.vocab)

        # The stub compiler is calibrated against the master, which renders as
        # one page. See compile.calibrate.
        self.page_budget = calibrate(
            self.master_src, self.cfg["resume"].get("master_known_pages", 1))

    def knowledge_rows(self) -> list[dict]:
        """Knowledge-base records eligible for resumes right now (see
        knowledge_base.usage). Read fresh, so marking a project in the
        library takes effect without restarting the server."""
        bank_cfg = self.cfg.get("project_bank", {})
        if not bank_cfg.get("enabled", True):
            return []
        rows = self.store.knowledge_base(require_approval=False)
        return [r for r in rows if kb_usage(r, bank_cfg)[0]]

    def knowledge_entries(self) -> list:
        entries = []
        for row in self.knowledge_rows():
            e = bank_entry(row)
            if e is not None and not any(_same_project(e.title, m.title)
                                         for m in self.master.projects):
                entries.append(e)
        return entries

    # ------------------------------------------------------------------ stages

    def ingest(self, raw_jd: str, source_url: str = "") -> str:
        return self.store.create_job(raw_jd, source_url)

    def analyse(self, job_id: str, force: bool = False) -> dict:
        """JD extraction + score + ATS. No tailoring, no model rewriting.

        Extraction is CACHED per job. The model's segmentation of prose into
        requirement rows is stable for identical input but shifts with small
        input changes -- editing one header line moved this posting from 12
        rows to 13 and the score by 13 points. Caching means a given
        application's score never moves once computed, which is the property
        that matters. Pass force=True to deliberately re-extract.
        """
        row = self.store.get(job_id)
        if row is None:
            raise KeyError(job_id)

        broker.clear(job_id)
        cached = self.store.get_json(job_id, "jd_json")

        with Stage(job_id, "extract", "reading the posting") as st:
            if cached is not None and not force:
                jd = _rehydrate_jd(cached, row["raw_jd"])
                st.finish("reused stored extraction - score stays stable")
                self.store.log(job_id, "analyse_cached", "reused stored extraction")
            else:
                jd = extract(row["raw_jd"], self.llm, self.vocab)
                self.store.save_json(job_id, "jd_json", to_jsonable(jd))
                st.finish("%s - %s" % (jd.company or "?", jd.title or "?"),
                          requirements=len(jd.requirements),
                          warnings=len(jd.warnings))
                self.store.log(job_id, "jd_extracted",
                               "%d requirements, %d warnings"
                               % (len(jd.requirements), len(jd.warnings)))

        with Stage(job_id, "resolve", "mapping phrases to known skills") as st:
            scoreable = [r for r in jd.requirements if r.keys]
            unresolved = sorted({p for r in jd.requirements for p in r.unresolved})
            st.finish("%d scoreable demands, %d phrases unmapped"
                      % (len(scoreable), len(unresolved)),
                      status=WARN if unresolved else OK,
                      unresolved=unresolved[:10])

        self.store.update(
            job_id, company=jd.company, title=jd.title,
            location=jd.location, external_id=jd.job_id)

        with Stage(job_id, "score", "comparing against your resume") as st:
            rep = score_resume(self.master, jd, self.vocab, self.cfg,
                               extra_projects=self.knowledge_entries())
            st.finish("overall %d%% - %d strong, %d partial, %d missing"
                      % (rep.overall, len(rep.strong), len(rep.partial),
                         len(rep.missing)),
                      overall=rep.overall, missing=rep.missing[:8])
        self.store.save_json(job_id, "score_json", to_jsonable(rep))
        self.store.update(job_id, match_score=rep.overall)

        with Stage(job_id, "ats", "checking keyword coverage") as st:
            ats = ats_mod.analyse(self.master, jd, self.vocab,
                                  self.cfg["scoring"]["thresholds"])
            st.finish("%d%% exact coverage, %d keywords missing"
                      % (round(ats.coverage * 100), len(ats.missing)),
                      coverage=round(ats.coverage * 100))
        self.store.save_json(job_id, "ats_json", to_jsonable(ats))

        self.store.set_status(job_id, "scored")
        self.store.log(job_id, "scored", "overall %d%%" % rep.overall)
        broker.emit(job_id, "done", OK, "analysis complete")
        return {"jd": to_jsonable(jd), "score": to_jsonable(rep),
                "ats": to_jsonable(ats)}

    def tailor(self, job_id: str) -> dict:
        """Select (code) -> rewrite (model) -> validate -> fit -> diff."""
        row = self.store.get(job_id)
        if row is None:
            raise KeyError(job_id)
        jd_raw = self.store.get_json(job_id, "jd_json")
        if jd_raw is None:
            raise ValueError("run analyse() before tailor()")
        jd = _rehydrate_jd(jd_raw, row["raw_jd"])

        broker.clear(job_id)

        bank_cfg = self.cfg.get("project_bank", {})
        selection = None
        whitelist = self.whitelist
        with Stage(job_id, "select", "choosing what belongs on the page") as st:
            if bank_cfg.get("enabled", True):
                rows = self.knowledge_rows()
                if rows:
                    selection = select_projects(
                        self.master, rows, jd, self.vocab, self.cfg,
                        llm=self.llm if bank_cfg.get("model_selection", True)
                        else None)
                    if selection.bank_evidence:
                        # Tailored bank bullets are verified against the
                        # knowledge-base records they came from, as well as
                        # main.tex.
                        whitelist = Whitelist(
                            self.master.evidence_text() + "\n"
                            + selection.bank_evidence, self.vocab)
            p = plan_tailor(self.master, jd, self.vocab, self.cfg,
                            selection=selection)
            st.finish("%d projects kept, %d dropped, %d bullets in scope"
                      % (len(p.projects_kept), len(p.projects_dropped),
                         len(p.bullets)),
                      kept=[e.title for e in p.projects_kept],
                      dropped=[e.title for e in p.projects_dropped],
                      notes=p.notes)

        with Stage(job_id, "rewrite",
                   "rewriting %d bullets" % len(p.bullets)) as st:
            def _tick(i, n, bid, outcome):
                detail = ("rewriting %d bullets in one call" % n
                          if outcome == "batch" else
                          "checking bullet %d of %d" % (i, n))
                broker.emit(job_id, "rewrite", "running", detail,
                            index=i, total=n, bullet=bid, outcome=outcome)

            result = rewrite_bullets(p, jd, self.llm, self.vocab, self.cfg,
                                     on_bullet=_tick)
            st.finish("%d accepted, %d rejected by the quality gate, %d skipped"
                      " (%d model call%s)"
                      % (len(result.rewritten), len(result.rejected),
                         len(result.skipped), result.calls,
                         "" if result.calls == 1 else "s"),
                      status=WARN if result.skipped else OK,
                      accepted=len(result.rewritten),
                      rejected=[list(r) for r in result.rejected],
                      calls=result.calls, warnings=result.warnings,
                      tokens_in=result.tokens_in, tokens_out=result.tokens_out)

        with Stage(job_id, "summary", "re-emphasising the summary") as st:
            summary_text, summary_reason = rewrite_summary(
                self.master, jd, self.llm, self.vocab, self.cfg)
            if summary_text:
                result.summary = summary_text
            st.finish("rewritten and whitelist-checked" if summary_text
                      else "kept your original (%s)" % summary_reason,
                      status=OK if summary_text else WARN)

        with Stage(job_id, "validate",
                   "verifying every claim against your evidence") as st:
            pairs = [(rb.bullet.id, rb.bullet.text,
                      result.rewritten[rb.bullet.id], rb.bullet.line)
                     for rb in p.bullets if rb.bullet.id in result.rewritten]
            diff_lines, check = build_diff(pairs, whitelist)
            st.finish("%d bullets checked - %d blocking, %d minor"
                      % (check.checked, len(check.high), len(check.low)),
                      status=FAIL if check.high else OK,
                      high=[v.value for v in check.high])

        with Stage(job_id, "fit", "rendering and measuring the page") as st:
            outcome = fit_to_pages(self.master, p, result, self.cfg)
            st.finish("%d page(s)%s%s"
                      % (outcome.pages,
                         "" if outcome.measured else " (estimated)",
                         ", %d trim action(s)" % len(outcome.actions)
                         if outcome.actions else ""),
                      status=OK if outcome.fits else WARN,
                      pages=outcome.pages, measured=outcome.measured)

        content_hash = hashlib.sha256(outcome.tex.encode("utf-8")).hexdigest()[:16]
        gate_ok = check.ok and outcome.fits and not outcome.render.warnings

        self.store.save_json(job_id, "tailor_json", {
            "plan_notes": p.notes,
            "selection": {
                "method": selection.method if selection else "main.tex only",
                # How many projects were in the running (main.tex + GitHub).
                "considered": (sum(n.startswith("candidate ") for n in selection.notes)
                               if selection else len(self.master.projects)),
                "github_projects_available": len(self.knowledge_rows()),
                "projects": [{"id": e.id, "title": e.title, "origin": e.origin,
                              "github_url": e.source_ref,
                              "bullets": [b.id for b in e.bullets],
                              "reason": selection.reasons.get(e.id, "")
                              if selection else ""}
                             for e in p.projects_kept],
            },
            "projects_kept": [e.id for e in p.projects_kept],
            "projects_dropped": [e.id for e in p.projects_dropped],
            "skill_order": p.skill_order,
            "skipped": result.skipped,
            "rejected": result.rejected,
            "summary_rewritten": bool(summary_text),
            "summary_reason": summary_reason,
            "summary_text": summary_text,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "pages": outcome.pages,
            "measured": outcome.measured,
            "trim_actions": to_jsonable(outcome.actions),
            "warnings": result.warnings + outcome.warnings,
            "rewrite_calls": result.calls,
        })
        self.store.save_json(job_id, "diff_json", {
            "lines": to_jsonable(diff_lines),
            "checked": check.checked,
            "gate_ok": check.ok,
            "high": to_jsonable(check.high),
            "low": to_jsonable(check.low),
        })
        self.store.update(job_id, tailored_tex=outcome.tex)
        self.store.record_checks(job_id, gate_ok, content_hash)
        self.store.set_status(job_id, "tailored")
        self.store.log(job_id, "tailored",
                       "%d bullets, %d high / %d low violations, %d page(s)%s"
                       % (len(result.rewritten), len(check.high), len(check.low),
                          outcome.pages, "" if outcome.measured else " (estimated)"))

        broker.emit(job_id, "done", OK if gate_ok else WARN,
                    "ready for your review" if gate_ok
                    else "blocked - unsupported claims found")

        return {
            "diff": to_jsonable(diff_lines),
            "gate_ok": gate_ok,
            "violations": {"high": to_jsonable(check.high),
                           "low": to_jsonable(check.low)},
            "pages": outcome.pages,
            "measured": outcome.measured,
            "trim_actions": to_jsonable(outcome.actions),
            "warnings": outcome.warnings,
            "tokens": {"in": result.tokens_in, "out": result.tokens_out},
            "rejected": result.rejected,
            "summary": {"rewritten": bool(summary_text),
                        "reason": summary_reason, "text": summary_text},
        }

    def release(self, job_id: str) -> tuple[bool, str, str]:
        """Return (allowed, reason, tex). Enforces the spec 17 gate."""
        ok, reason = self.store.can_release(job_id)
        if not ok:
            return False, reason, ""
        row = self.store.get(job_id)
        self.store.log(job_id, "downloaded", "tailored resume released")
        return True, "ok", row["tailored_tex"]


def _rehydrate_jd(data: dict, raw: str) -> JobDescription:
    """Rebuild a JobDescription from its stored JSON.

    Requirements keep their resolved vocabulary keys, so no model call and no
    re-resolution is needed -- the stored analysis is the analysis.
    """
    from .core.jd_extract import Qualifications, Requirement

    jd = JobDescription(raw=raw)
    for f in ("company", "title", "location", "job_id", "employment_type",
              "level", "category"):
        setattr(jd, f, data.get(f, "") or "")
    jd.soft_skills = data.get("soft_skills") or []
    jd.ats_terms = data.get("ats_terms") or {}
    jd.warnings = data.get("warnings") or []
    q = data.get("qualifications") or {}
    jd.qualifications = Qualifications(
        degree=q.get("degree", ""),
        graduation_year=q.get("graduation_year", ""),
        min_experience_years=q.get("min_experience_years", ""),
        other=q.get("other") or [])
    for r in data.get("requirements") or []:
        jd.requirements.append(Requirement(
            text=r.get("text", ""), kind=r.get("kind", "required"),
            phrases=r.get("phrases") or [], keys=r.get("keys") or [],
            unresolved=r.get("unresolved") or [],
            recovered=r.get("recovered", False), split=r.get("split", False)))
    return jd
