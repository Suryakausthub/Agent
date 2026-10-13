"""Per-job project and bullet selection over main.tex + the Knowledge Base.

The knowledge base holds up to ten candidate bullets per project; the page
takes at most three. Choosing them is a judgement about the project as a
whole -- what it is, how it is built -- not just keyword overlap, so it runs in
two stages:

  1. CODE pre-ranks every candidate project by how much of the JD's vocabulary
     its full context demonstrates (tech stack weighted above prose). This
     bounds the model's input and is the fallback if the model call fails.

  2. ONE model call sees the JD and the top candidates WITH their context
     (description, implemented details, every candidate bullet) and returns
     project ids in rank order plus up to three bullet ids each.

The model only ever returns ids. Code validates every one of them: unknown ids
are discarded, at most three bullets per project, and the page minimum is
topped up from the code ranking. The model therefore cannot introduce text --
it can only choose among bullets that were verified when the knowledge base
was built and approved by a human.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .jd_extract import JobDescription
from .knowledge_base import all_tech, context_text
from .llm import LLM, LLMError
from .model import Bullet, Entry, Resume, Span
from .tailor import sanitise_latex
from .vocabulary import Vocabulary


@dataclass
class Candidate:
    entry: Entry                       # bank entries carry ALL candidate bullets
    origin: str                        # master | bank
    score: float = 0.0
    matched: list[str] = field(default_factory=list)
    record: dict | None = None         # knowledge-base record, bank only


@dataclass
class Selection:
    kept: list[Entry] = field(default_factory=list)       # ranked, bullets trimmed
    dropped_master: list[Entry] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)  # entry id -> why
    method: str = "code"                                   # model | code
    notes: list[str] = field(default_factory=list)
    bank_evidence: str = ""        # text of the bank projects that were used
    tokens_in: int = 0
    tokens_out: int = 0
    considered: int = 0            # projects the model saw in pass 1


# ------------------------------------------------------------- candidates

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def display_name(name: str) -> str:
    """'FlipSpace-Microservice-Marketplace-Platform' -> words, for the page.
    Names that already contain spaces are the author's choice and kept."""
    name = name.strip()
    if " " in name:
        return name
    return re.sub(r"[-_]+", " ", name).strip()


def bank_entry(row: dict) -> Entry | None:
    """Turn an approved knowledge-base record into an Entry with ALL its
    candidate bullets. Selection trims it to at most three."""
    rec = row.get("project_json") or {}
    bullets = rec.get("bullets") or []
    if not bullets:
        return None
    eid = "bank.%s" % _slug(row.get("repo_name") or rec.get("name", ""))
    stack = rec.get("tech_stack") or {}
    head = (stack.get("languages", []) + stack.get("frameworks", [])
            + stack.get("databases", []) + stack.get("messaging", [])
            + stack.get("cloud_devops", []))
    tech = list(dict.fromkeys(head))[:5] or all_tech(rec)[:5]
    return Entry(
        id=eid, kind="project",
        title=display_name(rec.get("name") or row.get("repo_name", "")),
        meta=", ".join(tech), date="", tech=tech,
        bullets=[Bullet(id="%s.%s" % (eid, b["id"]),
                        text=sanitise_latex(b["text"], ""),
                        span=Span(0, 0), line=0)
                 for b in bullets],
        origin="bank", source_ref=rec.get("github_url", ""))


def _same_project(a: str, b: str) -> bool:
    x, y = set(_slug(a).split("-")), set(_slug(b).split("-"))
    x.discard("")
    y.discard("")
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= 0.8


def candidates(resume: Resume, bank_rows: list[dict], jd: JobDescription,
               vocab: Vocabulary) -> list[Candidate]:
    jd_keys = jd.all_keys() | set(jd.ats_terms)
    out: list[Candidate] = []

    def score(text: str, tech: str) -> tuple[float, list[str]]:
        if not jd_keys:
            return 0.0, []
        prose = set(vocab.extract(text)) & jd_keys
        stack = set(vocab.extract(tech)) & jd_keys
        s = (len(prose) + 0.5 * len(stack)) / len(jd_keys)
        return s, sorted(vocab.canonical(k) for k in prose | stack)

    for e in resume.projects:
        text = " ".join([e.title, e.meta, *(b.text for b in e.bullets)])
        s, m = score(text, " ".join(e.tech))
        out.append(Candidate(entry=e, origin="master", score=s, matched=m))

    for row in bank_rows:
        e = bank_entry(row)
        if e is None:
            continue
        if any(_same_project(e.title, m.title) for m in resume.projects):
            continue          # already on the master resume under its own name
        rec = row["project_json"]
        s, m = score(context_text(rec), " ".join(all_tech(rec)))
        out.append(Candidate(entry=e, origin="bank", score=s, matched=m,
                             record=rec))

    out.sort(key=lambda c: (-c.score, c.origin != "master", c.entry.title))
    return out


# ------------------------------------------------------------ code fallback

def _bullet_pick(c: Candidate, jd_keys: set[str], vocab: Vocabulary,
                 limit: int) -> list[Bullet]:
    """Greedy, diversity-aware pick: best JD overlap first, then penalise a
    bullet whose focus or vocabulary repeats one already chosen."""
    focus = {}
    if c.record:
        focus = {"%s.%s" % (c.entry.id, b["id"]): b.get("focus", "other")
                 for b in c.record.get("bullets", [])}
    pool = []
    for i, b in enumerate(c.entry.bullets):
        keys = set(vocab.extract(b.text))
        pool.append((b, keys & jd_keys, keys, i))

    chosen: list[tuple] = []
    while pool and len(chosen) < limit:
        def gain(item):
            b, hits, keys, i = item
            covered = set().union(*(x[1] for x in chosen)) if chosen else set()
            new_hits = len(hits - covered)
            same_focus = any(focus.get(b.id) == focus.get(x[0].id)
                             for x in chosen) if focus else False
            return (new_hits - (0.5 if same_focus else 0), -i)
        best = max(pool, key=gain)
        if chosen and not (best[1] - set().union(*(x[1] for x in chosen))):
            # Nothing new for this JD. Stop rather than pad with weak lines,
            # but keep at least two so the entry is not a single orphan line.
            if len(chosen) >= 2:
                break
        chosen.append(best)
        pool.remove(best)
    return [x[0] for x in sorted(chosen, key=lambda x: x[3])]


def _trimmed(e: Entry, bullets: list[Bullet]) -> Entry:
    t = Entry(**{**e.__dict__})
    t.bullets = bullets
    return t


# ----------------------------------------------------------------- model

_SYSTEM = """You choose which projects and resume bullets best fit a job.

You are given the job's requirements and candidate projects. Each project has \
a description, its real tech stack, and numbered candidate bullets that are \
already verified as true. You only CHOOSE - you never write or edit text.

Rules:
- Rank projects by genuine relevance to the role: the kind of system, the \
engineering problems, and the technologies, judged from the project's \
description and implementation - not keyword overlap alone.
- For each chosen project from the knowledge base, pick at most 3 bullet ids. \
Each pick must add a DIFFERENT strength for this job; never two bullets \
about the same feature or skill. If only one or two are genuinely relevant, \
pick fewer.
- Projects marked FIXED come from the candidate's resume; choose whether to \
include them, but do not pick bullets for them (return an empty list).
- Choose between %d and %d projects.
- reason: one short sentence on why the project fits this job.

Return only the JSON object."""

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["projects"],
    "properties": {"projects": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "bullet_ids", "reason"],
        "properties": {"id": {"type": "string"},
                       "bullet_ids": {"type": "array",
                                      "items": {"type": "string"}},
                       "reason": {"type": "string"}}}}},
}


def _jd_brief(jd: JobDescription, vocab: Vocabulary) -> str:
    lines = ["JOB: %s at %s" % (jd.title or "?", jd.company or "?")]
    for kind in ("required", "preferred"):
        reqs = [r.text for r in jd.requirements if r.kind == kind]
        if reqs:
            lines.append("%s:" % kind.upper())
            lines += ["  - %s" % r[:180] for r in reqs[:14]]
    keys = sorted(vocab.canonical(k) for k in jd.all_keys())
    if keys:
        lines.append("KEY TECHNOLOGIES: %s" % ", ".join(keys[:30]))
    return "\n".join(lines)


def _project_brief(c: Candidate) -> str:
    e = c.entry
    if c.origin == "master":
        lines = ["PROJECT id=%s  [FIXED - from resume]" % e.id,
                 "  name: %s" % e.title, "  tech: %s" % ", ".join(e.tech)]
        lines += ["  - %s" % b.text for b in e.bullets]
        return "\n".join(lines)
    rec = c.record or {}
    impl = [i["detail"] for i in rec.get("implementation", [])
            if i.get("status") == "implemented"][:6]
    lines = ["PROJECT id=%s  [knowledge base]" % e.id,
             "  name: %s" % e.title,
             "  what it is: %s" % (rec.get("description") or rec.get("summary", ""))[:450],
             "  tech: %s" % ", ".join(all_tech(rec)[:16])]
    if impl:
        lines.append("  implemented: %s" % " | ".join(x[:110] for x in impl))
    lines.append("  candidate bullets:")
    for b in rec.get("bullets", []):
        lines.append("    %s [%s] %s" % (b["id"], b.get("focus", ""), b["text"]))
    return "\n".join(lines)


_SHORTLIST_SYSTEM = """You shortlist a candidate's projects for one job.

You see the job and a catalog of ALL the candidate's projects (one line each: \
what it is and its real tech stack). Pick the %d projects that would make the \
strongest resume for THIS job - judge the kind of system and engineering \
work, not just shared keywords. A project in a different language can still \
fit if the work matches (e.g. backend services, distributed systems, data \
pipelines). Return ids best first. Only use ids from the catalog."""

_SHORTLIST_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ids"],
    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
}


def _catalog_line(c: Candidate) -> str:
    e = c.entry
    if c.origin == "master":
        what = " ".join(b.text for b in e.bullets)[:200]
        tech = ", ".join(e.tech)
    else:
        rec = c.record or {}
        what = (rec.get("summary") or rec.get("description") or "")[:200]
        tech = ", ".join(all_tech(rec)[:10])
    return "%s | %s | %s | tech: %s" % (e.id, e.title, what, tech)


def _shortlist(cands: list[Candidate], jd: JobDescription, vocab: Vocabulary,
               llm: LLM, size: int, sel: Selection) -> list[Candidate]:
    """Pass 1 over every project. Falls back to the code ranking."""
    by_id = {c.entry.id: c for c in cands}
    user = (_jd_brief(jd, vocab) + "\n\nPROJECT CATALOG (id | name | what it "
            "is | tech):\n" + "\n".join(_catalog_line(c) for c in cands))
    try:
        res = llm.complete(_SHORTLIST_SYSTEM % size, user,
                           json_schema=_SHORTLIST_SCHEMA, effort="low")
        sel.tokens_in += res.prompt_tokens
        sel.tokens_out += res.completion_tokens
        ids = json.loads(res.text).get("ids") or []
    except (LLMError, ValueError) as e:
        sel.notes.append("shortlist call failed (%s) - using code ranking"
                         % str(e)[:120])
        return cands[:size]
    short = []
    for i in ids:
        c = by_id.get(i)
        if c is not None and c not in short:
            short.append(c)
    # Top up from the code ranking if the model returned too few valid ids.
    for c in cands:
        if len(short) >= size:
            break
        if c not in short:
            short.append(c)
    return short[:size]


# ---------------------------------------------------------------- public

def select(resume: Resume, bank_rows: list[dict], jd: JobDescription,
           vocab: Vocabulary, cfg: dict, llm: LLM | None = None) -> Selection:
    rcfg = cfg["resume"]
    pcfg = cfg.get("project_bank", {})
    max_p = rcfg.get("max_projects", 3)
    min_p = rcfg.get("min_projects", 2)
    per = pcfg.get("max_bullets_per_project", 3)
    shortlist = pcfg.get("candidates_for_model", 6)
    jd_keys = jd.all_keys() | set(jd.ats_terms)

    cands = candidates(resume, bank_rows, jd, vocab)
    by_id = {c.entry.id: c for c in cands}
    sel = Selection()
    for c in cands:
        sel.notes.append("candidate %-6s %-40s relevance=%.2f (%s)" % (
            c.origin, c.entry.title[:40], c.score,
            ", ".join(c.matched[:5]) or "no overlap"))

    picks: list[tuple[Candidate, list[Bullet]]] = []

    if llm is not None and any(c.origin == "bank" for c in cands):
        # Pass 1: the model sees EVERY project (compactly) and shortlists the
        # ones that genuinely fit. Ranking only by keyword overlap first
        # meant a project described in different words never reached it.
        short = _shortlist(cands, jd, vocab, llm, shortlist, sel)
        sel.notes.append("model considered all %d projects; shortlisted %s"
                         % (len(cands), ", ".join(c.entry.title for c in short)))
        sel.considered = len(cands)
        user = _jd_brief(jd, vocab) + "\n\nCANDIDATE PROJECTS:\n\n" + \
            "\n\n".join(_project_brief(c) for c in short)
        try:
            res = llm.complete(_SYSTEM % (min_p, max_p), user,
                               json_schema=_SCHEMA, effort="medium")
            sel.tokens_in += res.prompt_tokens
            sel.tokens_out += res.completion_tokens
            data = json.loads(res.text)
            for p in data.get("projects") or []:
                c = by_id.get(p.get("id", ""))
                if c is None or any(c is x for x, _ in picks):
                    if p.get("id"):
                        sel.notes.append("model returned unknown/duplicate id "
                                         "%r - ignored" % p.get("id"))
                    continue
                if c.origin == "master":
                    bullets = c.entry.bullets
                else:
                    ok = {b.id.rsplit(".", 1)[-1]: b for b in c.entry.bullets}
                    ids = list(dict.fromkeys(p.get("bullet_ids") or []))
                    bullets = [ok[i] for i in ids if i in ok][:per]
                    if not bullets:
                        sel.notes.append("model chose %s without valid "
                                         "bullets - skipped" % c.entry.title)
                        continue
                    order = {b.id: n for n, b in enumerate(c.entry.bullets)}
                    bullets.sort(key=lambda b: order[b.id])
                picks.append((c, bullets))
                sel.reasons[c.entry.id] = (p.get("reason") or "")[:200]
                if len(picks) >= max_p:
                    break
            sel.method = "model"
        except (LLMError, ValueError) as e:
            sel.notes.append("model selection failed (%s) - using code "
                             "ranking" % str(e)[:120])
            picks = []

    # Code ranking: the fallback, and the top-up when the model chose fewer
    # projects than the page holds. Filling to max_projects matches plan():
    # a short page is worse than a weaker project, and the trim loop removes
    # the lowest-ranked one if the page actually overflows.
    target = max_p
    for c in cands:
        if len(picks) >= target:
            break
        if any(c is x for x, _ in picks):
            continue
        bullets = (c.entry.bullets if c.origin == "master"
                   else _bullet_pick(c, jd_keys, vocab, per))
        if bullets:
            picks.append((c, bullets))
            sel.reasons.setdefault(c.entry.id, "code ranking: %s"
                                   % (", ".join(c.matched[:5]) or "no overlap"))

    sel.kept = [_trimmed(c.entry, b) for c, b in picks]
    kept_ids = {e.id for e in sel.kept}
    sel.dropped_master = [e for e in resume.projects if e.id not in kept_ids]

    bank_text = []
    for c, _ in picks:
        if c.origin == "bank" and c.record:
            bank_text.append(context_text(c.record))
    sel.bank_evidence = "\n".join(bank_text)

    for e in sel.kept:
        sel.notes.append("selected %-6s %-40s %d bullet(s) - %s" % (
            e.origin, e.title[:40], len(e.bullets), sel.reasons.get(e.id, "")))
    return sel
