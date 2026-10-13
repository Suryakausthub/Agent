"""SQLite persistence. Single user, local file, no auth (per the agreed scope).

One row per application. Pipeline artefacts are stored as JSON blobs rather
than normalised tables: they are read back whole, they evolve with the code,
and normalising them would buy nothing for a single-user tool.

The approval columns are the enforcement point for spec section 17. `gate_ok`
records whether the automated whitelist/diff checks passed; `approved_at`
records that a human reviewed the diff. Download and apply require BOTH, and
`approved_at` is cleared whenever the tailored content changes, so approval can
never carry over to output the user has not seen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    source_url      TEXT DEFAULT '',
    raw_jd          TEXT NOT NULL,
    company         TEXT DEFAULT '',
    title           TEXT DEFAULT '',
    location        TEXT DEFAULT '',
    external_id     TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'saved',
    match_score     INTEGER,
    jd_json         TEXT,
    score_json      TEXT,
    ats_json        TEXT,
    tailor_json     TEXT,
    diff_json       TEXT,
    referral_json   TEXT,
    tailored_tex    TEXT,
    content_hash    TEXT DEFAULT '',
    gate_ok         INTEGER NOT NULL DEFAULT 0,
    approved_at     TEXT,
    notes           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL,
    ts        TEXT NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT DEFAULT '',
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

-- Project Bank, step 1: discovery and classification only.
-- Extraction fields (description, bullets, metrics, evidence) come in a later
-- step; this table currently records only what was found on GitHub and how it
-- was classified, with the reason, so nothing is silently discarded.
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,   -- owner/repo, stable across re-runs
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    repo_name       TEXT NOT NULL,
    github_url      TEXT NOT NULL,
    description     TEXT DEFAULT '',    -- GitHub's own description, not generated
    language        TEXT DEFAULT '',
    size_kb         INTEGER DEFAULT 0,
    is_fork         INTEGER DEFAULT 0,
    pushed_at       TEXT DEFAULT '',
    default_branch  TEXT DEFAULT 'main',
    classification  TEXT NOT NULL,      -- keep | review | exclude
    reason          TEXT NOT NULL DEFAULT '',
    signals         TEXT DEFAULT '',    -- JSON list, the evidence behind it
    duplicate_of    TEXT DEFAULT '',
    user_override   TEXT DEFAULT '',    -- set by hand later; never auto-cleared
    status          TEXT NOT NULL DEFAULT 'discovered',
    discovered_at   TEXT NOT NULL,
    head_sha        TEXT DEFAULT '',    -- version processed (one-time contract)
    evidence_json   TEXT,               -- harvested repo facts, auditable
    harvested_at    TEXT,
    extracted_at    TEXT,
    -- Project Bank content. review_status gates resume eligibility: only
    -- 'approved' projects may be used, and extraction never sets it.
    project_json    TEXT,
    title           TEXT DEFAULT '',
    summary         TEXT DEFAULT '',
    relevance_text  TEXT DEFAULT '',
    review_status   TEXT NOT NULL DEFAULT 'needs_review',
    reviewed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_job    ON audit(job_id, ts);
CREATE INDEX IF NOT EXISTS idx_projects_class ON projects(classification);
"""

# Spec section 10 pipeline, plus the terminal states.
STATUSES = [
    "saved", "scored", "tailored", "approved", "referral_requested",
    "referral_received", "applied", "assessment", "interview", "final_round",
    "offer", "rejected", "withdrawn", "no_response",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def _dumps(obj: Any) -> str | None:
    return None if obj is None else json.dumps(obj, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    return None if not raw else json.loads(raw)


class Store:
    def __init__(self, path: str):
        self.con = connect(path)

    # ------------------------------------------------------------------ write

    def create_job(self, raw_jd: str, source_url: str = "") -> str:
        jid = uuid.uuid4().hex[:12]
        ts = now()
        self.con.execute(
            "INSERT INTO jobs (id, created_at, updated_at, source_url, raw_jd) "
            "VALUES (?,?,?,?,?)", (jid, ts, ts, source_url, raw_jd))
        self.con.commit()
        self.log(jid, "created", source_url or "pasted JD")
        return jid

    def update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now()
        cols = ", ".join("%s = ?" % k for k in fields)
        self.con.execute("UPDATE jobs SET %s WHERE id = ?" % cols,
                         (*fields.values(), job_id))
        self.con.commit()

    def save_json(self, job_id: str, column: str, payload: Any) -> None:
        self.update(job_id, **{column: _dumps(payload)})

    def log(self, job_id: str, event: str, detail: str = "") -> None:
        self.con.execute(
            "INSERT INTO audit (job_id, ts, event, detail) VALUES (?,?,?,?)",
            (job_id, now(), event, detail[:2000]))
        self.con.commit()

    def set_status(self, job_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError("unknown status %r" % status)
        self.update(job_id, status=status)
        self.log(job_id, "status", status)

    # ---------------------------------------------------------- approval gate

    def record_checks(self, job_id: str, gate_ok: bool, content_hash: str) -> None:
        """Store automated-check outcome and invalidate stale approval.

        Approval is tied to a specific rendering. If the tailored content
        changes, any prior approval is void -- otherwise a user could approve
        one diff and download a different document (spec 17).
        """
        row = self.get(job_id)
        prior = (row or {}).get("content_hash", "")
        fields: dict[str, Any] = {"gate_ok": 1 if gate_ok else 0,
                                  "content_hash": content_hash}
        if prior and prior != content_hash:
            fields["approved_at"] = None
            self.log(job_id, "approval_invalidated",
                     "tailored content changed since approval")
        self.update(job_id, **fields)

    def approve(self, job_id: str) -> tuple[bool, str]:
        row = self.get(job_id)
        if row is None:
            return False, "no such job"
        if not row["gate_ok"]:
            return False, ("automated checks have not passed; approval cannot "
                           "be recorded (spec 17: checks AND human review)")
        self.update(job_id, approved_at=now())
        self.set_status(job_id, "approved")
        self.log(job_id, "approved", "human approved the diff")
        return True, "approved"

    def can_release(self, job_id: str) -> tuple[bool, str]:
        """Gate for download / apply / send."""
        row = self.get(job_id)
        if row is None:
            return False, "no such job"
        if not row["tailored_tex"]:
            return False, "nothing tailored yet"
        if not row["gate_ok"]:
            return False, "automated validation has not passed"
        if not row["approved_at"]:
            return False, "awaiting human approval of the diff"
        return True, "ok"

    # ------------------------------------------------------------------- read

    def get(self, job_id: str) -> dict | None:
        cur = self.con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_json(self, job_id: str, column: str) -> Any:
        row = self.get(job_id)
        return _loads(row[column]) if row else None

    def list_jobs(self, limit: int = 100, status: str | None = None) -> list[dict]:
        sql = ("SELECT id, created_at, updated_at, company, title, location, "
               "source_url, status, match_score, gate_ok, approved_at, "
               "(tailored_tex IS NOT NULL) AS tailored "
               "FROM jobs")
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY created_at DESC LIMIT ?"
        cur = self.con.execute(sql, (*args, limit))
        return [dict(r) for r in cur.fetchall()]

    def audit_trail(self, job_id: str) -> list[dict]:
        cur = self.con.execute(
            "SELECT ts, event, detail FROM audit WHERE job_id = ? ORDER BY id",
            (job_id,))
        return [dict(r) for r in cur.fetchall()]

    def delete(self, job_id: str) -> bool:
        cur = self.con.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.con.commit()
        return cur.rowcount > 0

    def stats(self) -> dict[str, int]:
        cur = self.con.execute(
            "SELECT status, COUNT(*) c FROM jobs GROUP BY status")
        return {r["status"]: r["c"] for r in cur.fetchall()}

    # ------------------------------------------------- projects (discovery)

    def upsert_project(self, *, id: str, repo_name: str, github_url: str,
                       description: str, language: str, size_kb: int,
                       is_fork: bool, pushed_at: str, default_branch: str,
                       classification: str, reason: str, signals: list[str],
                       duplicate_of: str = "") -> str:
        """Insert or refresh one discovered repository.

        Idempotent by design: re-running discovery updates the classification
        in place rather than creating a second row. A `user_override` set by
        hand is never touched here -- a re-run must not undo a human decision.
        """
        ts = now()
        existing = self.con.execute(
            "SELECT id, discovered_at FROM projects WHERE id = ?", (id,)
        ).fetchone()

        if existing:
            self.con.execute(
                "UPDATE projects SET updated_at=?, repo_name=?, github_url=?, "
                "description=?, language=?, size_kb=?, is_fork=?, pushed_at=?, "
                "default_branch=?, classification=?, reason=?, signals=?, "
                "duplicate_of=? WHERE id=?",
                (ts, repo_name, github_url, description, language, size_kb,
                 1 if is_fork else 0, pushed_at, default_branch, classification,
                 reason, _dumps(signals), duplicate_of, id))
            action = "updated"
        else:
            self.con.execute(
                "INSERT INTO projects (id, created_at, updated_at, repo_name, "
                "github_url, description, language, size_kb, is_fork, "
                "pushed_at, default_branch, classification, reason, signals, "
                "duplicate_of, discovered_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id, ts, ts, repo_name, github_url, description, language,
                 size_kb, 1 if is_fork else 0, pushed_at, default_branch,
                 classification, reason, _dumps(signals), duplicate_of, ts))
            action = "inserted"
        self.con.commit()
        return action

    def list_projects(self, classification: str | None = None) -> list[dict]:
        sql = "SELECT * FROM projects"
        args: tuple = ()
        if classification:
            sql += " WHERE classification = ?"
            args = (classification,)
        sql += " ORDER BY classification, size_kb DESC"
        rows = [dict(r) for r in self.con.execute(sql, args).fetchall()]
        for r in rows:
            r["signals"] = _loads(r.get("signals")) or []
        return rows

    def project_counts(self) -> dict[str, int]:
        cur = self.con.execute(
            "SELECT classification, COUNT(*) c FROM projects "
            "GROUP BY classification")
        out = {r["classification"]: r["c"] for r in cur.fetchall()}
        out["total"] = sum(out.values())
        return out

    def save_project_evidence(self, project_id: str, evidence: dict,
                              head_sha: str) -> None:
        """Store harvested repository facts against the discovery row."""
        self.con.execute(
            "UPDATE projects SET evidence_json=?, head_sha=?, harvested_at=?, "
            "updated_at=?, status='harvested' WHERE id=?",
            (_dumps(evidence), head_sha, now(), now(), project_id))
        self.con.commit()

    def get_project(self, project_id: str) -> dict | None:
        row = self.con.execute("SELECT * FROM projects WHERE id = ?",
                               (project_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["signals"] = _loads(d.get("signals")) or []
        d["evidence_json"] = _loads(d.get("evidence_json"))
        # project_json must be decoded too: edit_project mutates it as a dict,
        # and returning the raw string made that a TypeError at runtime.
        d["project_json"] = _loads(d.get("project_json"))
        return d

    def save_project_extract(self, project_id: str, project: dict,
                             title: str, summary: str,
                             relevance_text: str) -> None:
        """Store the generated Project Bank entry as needs_review.

        Never sets review_status to approved: extraction cannot make a project
        eligible for a resume, only a human can.
        """
        self.con.execute(
            "UPDATE projects SET project_json=?, title=?, summary=?, "
            "relevance_text=?, extracted_at=?, updated_at=?, "
            "status='extracted' WHERE id=?",
            (_dumps(project), title, summary, relevance_text, now(), now(),
             project_id))
        self.con.commit()

    def save_knowledge_record(self, project_id: str, record: dict,
                              relevance_text: str) -> None:
        """Store a knowledge-base record (knowledge_base.py).

        Unlike save_project_extract, this RESETS review_status: a regenerated
        record is new text, and an approval given to the old text must not
        carry over to it.
        """
        self.con.execute(
            "UPDATE projects SET project_json=?, title=?, summary=?, "
            "relevance_text=?, extracted_at=?, updated_at=?, "
            "status='extracted', review_status='needs_review', reviewed_at=NULL "
            "WHERE id=?",
            (_dumps(record), record.get("name", ""),
             record.get("summary") or record.get("description", ""),
             relevance_text, now(), now(), project_id))
        self.con.commit()

    def knowledge_base(self, require_approval: bool = True) -> list[dict]:
        """Knowledge-base records eligible for resume generation.

        With require_approval (the default) only human-approved records
        qualify. Rejected and excluded projects never do.
        """
        sql = ("SELECT * FROM projects WHERE project_json IS NOT NULL "
               "AND classification != 'exclude' AND review_status != 'rejected'")
        if require_approval:
            sql += " AND review_status = 'approved'"
        out = []
        for r in self.con.execute(sql + " ORDER BY repo_name").fetchall():
            d = dict(r)
            d.pop("evidence_json", None)
            d["project_json"] = _loads(d.get("project_json"))
            d["signals"] = _loads(d.get("signals")) or []
            out.append(d)
        return out

    def edit_project(self, project_id: str, *, title: str | None = None,
                     description: str | None = None,
                     bullets: list[str] | None = None) -> bool:
        """Apply a human correction to a generated entry.

        Edits are recorded as user_edited so a later refresh can avoid
        overwriting them, and they reset review_status: changing the text you
        are about to approve must not carry an earlier approval with it.
        """
        row = self.get_project(project_id)
        if row is None:
            return False
        proj = row.get("project_json") or {}
        if title is not None:
            proj["name"] = title
        if description is not None:
            proj["description"] = description
        is_kb = proj.get("kb_version") == 2
        if bullets is not None:
            if is_kb:
                # Keep each bullet's focus/evidence when its slot survives;
                # a human-written bullet has no evidence paths of its own.
                old = proj.get("bullets") or []
                new = []
                for i, text in enumerate(t.strip() for t in bullets):
                    if not text:
                        continue
                    prev = old[i] if i < len(old) else {}
                    new.append({**prev, "id": "b%d" % (len(new) + 1),
                                "text": text,
                                "focus": prev.get("focus", "other"),
                                "technologies": prev.get("technologies", []),
                                "evidence": prev.get("evidence", []),
                                "edited": text != prev.get("text")})
                proj["bullets"] = new
            else:
                proj["resume_bullets"] = bullets
        proj["user_edited"] = True

        if is_kb:
            from .core.knowledge_base import context_text
            rel = context_text(proj)
        else:
            rel = " ".join([proj.get("name", ""), proj.get("description", ""),
                            *proj.get("technologies", []),
                            *proj.get("skills", []),
                            *proj.get("features", []),
                            *proj.get("resume_bullets", [])])
        self.con.execute(
            "UPDATE projects SET project_json=?, title=?, summary=?, "
            "relevance_text=?, review_status='needs_review', updated_at=? "
            "WHERE id=?",
            (_dumps(proj), proj.get("name", row.get("title", "")),
             proj.get("description", ""), rel, now(), project_id))
        self.con.commit()
        return True

    def project_review_counts(self) -> dict[str, int]:
        cur = self.con.execute(
            "SELECT review_status, COUNT(*) c FROM projects "
            "WHERE project_json IS NOT NULL GROUP BY review_status")
        return {r["review_status"]: r["c"] for r in cur.fetchall()}

    def set_project_review(self, project_id: str, status: str) -> bool:
        if status not in ("needs_review", "approved", "rejected"):
            raise ValueError("unknown review status %r" % status)
        cur = self.con.execute(
            "UPDATE projects SET review_status=?, reviewed_at=?, updated_at=? "
            "WHERE id=?", (status, now(), now(), project_id))
        self.con.commit()
        return cur.rowcount > 0

    def approved_projects(self) -> list[dict]:
        """The only projects eligible for resume generation."""
        rows = self.con.execute(
            "SELECT * FROM projects WHERE review_status='approved' "
            "AND project_json IS NOT NULL ORDER BY title").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["project_json"] = _loads(d.get("project_json"))
            d["signals"] = _loads(d.get("signals")) or []
            d.pop("evidence_json", None)
            out.append(d)
        return out

    def set_project_override(self, project_id: str, classification: str) -> bool:
        """Human correction of a classification. Survives re-discovery."""
        if classification not in ("keep", "review", "exclude"):
            raise ValueError("unknown classification %r" % classification)
        cur = self.con.execute(
            "UPDATE projects SET user_override=?, classification=?, "
            "updated_at=? WHERE id=?",
            (classification, classification, now(), project_id))
        self.con.commit()
        return cur.rowcount > 0
