"""Resume tailoring (spec sections 6, 14, 15.1).

Two strictly separated steps, because a single open-ended "tailor my resume"
prompt is where invented experience comes from:

  Step A -- CODE. Select which bullets, projects and skills are relevant to
  this JD, and rank them. No model involvement: selection determines what
  appears on the page, so it must be reproducible.

  Step B -- MODEL. Rephrase ONLY the retrieved text, for emphasis and JD
  terminology. The model sees one section at a time, with numbers masked, and
  is told it may not introduce anything absent from the text in front of it.

Every output then goes through whitelist.py before it can reach the page.
Ordering is also code: the model never decides what is dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .jd_extract import JobDescription
from .llm import LLM, HallucinatedNumber, LLMError
from .model import Bullet, Entry, Resume
from .vocabulary import Vocabulary


@dataclass
class RankedBullet:
    bullet: Bullet
    entry_id: str
    score: float
    matched: list[str] = field(default_factory=list)


@dataclass
class TailorPlan:
    """What Step A decided, before any model call."""
    projects_kept: list[Entry] = field(default_factory=list)
    projects_dropped: list[Entry] = field(default_factory=list)
    skill_order: list[str] = field(default_factory=list)      # skill line ids
    bullets: list[RankedBullet] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Skills a GitHub project on the page proves, that the job asks for and
    # the Skills section lacks: (skill line id, display name, project entry
    # id). Rendered only while that project is still on the page.
    skill_additions: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class TailorResult:
    plan: TailorPlan
    rewritten: dict[str, str] = field(default_factory=dict)   # bullet_id -> text
    summary: str = ""
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)
    # (bullet_id, why, the rejected text) -- surfaced in the diff so a rejected
    # rewrite is visible rather than silently discarded.
    rejected: list[tuple[str, str, str]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    warnings: list[str] = field(default_factory=list)
    calls: int = 0                 # model calls made by the rewrite step


# --------------------------------------------------------------------- step A


def _relevance(text: str, jd_keys: set[str], vocab: Vocabulary) -> tuple[float, list[str]]:
    hits = set(vocab.extract(text))
    overlap = hits & jd_keys
    if not jd_keys:
        return 0.0, []
    return len(overlap) / len(jd_keys), sorted(vocab.canonical(k) for k in overlap)


def plan(resume: Resume, jd: JobDescription, vocab: Vocabulary,
         cfg: dict, selection=None) -> TailorPlan:
    """Code-only selection and ranking.

    `selection` (project_select.Selection) replaces the project choice when
    the Project Knowledge Base is in use: it has already ranked main.tex and
    knowledge-base projects together and cut each to at most three bullets.
    Without it, projects come from main.tex alone, as before.
    """
    jd_keys = jd.all_keys() | set(jd.ats_terms)
    p = TailorPlan()

    max_projects = cfg["resume"].get("max_projects", 3)
    min_projects = cfg["resume"].get("min_projects", 2)

    if selection is not None:
        p.projects_kept = list(selection.kept)
        p.projects_dropped = list(selection.dropped_master)
        p.notes.extend(selection.notes)
    else:
        scored: list[tuple[float, Entry, list[str]]] = []
        for e in resume.projects:
            text = " ".join([e.title, e.meta, *e.tech, *(b.text for b in e.bullets)])
            s, matched = _relevance(text, jd_keys, vocab)
            scored.append((s, e, matched))
        scored.sort(key=lambda t: -t[0])

        # Keep every project that fits, ranked by relevance. Dropping is the
        # trim loop's job and only happens under real page pressure.
        #
        # An earlier version discarded any project scoring 0 against the JD
        # even when there was room for it, which cut PySpark from a backend
        # application and left visible whitespace on the page. An irrelevant
        # project at the bottom of a page that would otherwise be blank is
        # still better than the blank space -- and spec 14's "no filler" rule
        # is about not padding a FULL page, not about leaving one short.
        keep = scored[:max_projects]

        p.projects_kept = [e for _, e, _ in keep]
        kept_ids = {e.id for e in p.projects_kept}
        p.projects_dropped = [e for _, e, _ in scored if e.id not in kept_ids]

        for s, e, matched in scored:
            tag = "kept" if e.id in kept_ids else "dropped"
            p.notes.append("%s %-40s relevance=%.2f (%s)"
                           % (tag, e.title[:40], s, ", ".join(matched[:5]) or "no overlap"))

    # Skills: rank lines by how much of the JD each covers.
    line_scores: list[tuple[float, str]] = []
    for line in resume.skills:
        s, _ = _relevance(" ".join(line.items), jd_keys, vocab)
        line_scores.append((s, line.id))
    line_scores.sort(key=lambda t: -t[0])
    p.skill_order = [lid for _, lid in line_scores]

    if selection is not None and cfg.get("tailoring", {}).get("add_proven_skills", True):
        p.skill_additions = _proven_skill_additions(
            resume, jd, vocab, selection, p.projects_kept,
            cfg.get("tailoring", {}).get("max_added_skills", 4))
        for line_id, name, src in p.skill_additions:
            p.notes.append("skill added: %s -> %s (proven by %s)" % (name, line_id, src))

    # Bullets in scope for rewriting: experience always, plus kept projects.
    for e in resume.experience + p.projects_kept:
        for b in e.bullets:
            s, matched = _relevance(b.text, jd_keys, vocab)
            p.bullets.append(RankedBullet(bullet=b, entry_id=e.id,
                                          score=s, matched=matched))
    return p


def _proven_skill_additions(resume: Resume, jd: JobDescription,
                            vocab: Vocabulary, selection, kept: list[Entry],
                            limit: int) -> list[tuple[str, str, str]]:
    """Skills to add to the Skills section: asked for by the job, missing from
    the Skills section, and proven by a GitHub project that is on the page.

    Applicant-tracking systems weight the Skills section most, so a skill that
    only appears inside a project bullet is easily missed. Nothing here is new
    information: each skill is in a project's tech stack that was verified
    against its repository when the project library was built.
    """
    if not resume.skills or limit <= 0:
        return []
    have = set()
    for line in resume.skills:
        have |= set(vocab.extract(", ".join(line.items)))
    # How often the job asks for each skill - most-demanded first.
    demand: dict[str, int] = {}
    for r in jd.requirements:
        for k in r.keys:
            demand[k] = demand.get(k, 0) + (2 if r.kind == "required" else 1)

    on_page = [e for e in kept if e.origin == "bank"]
    candidates: dict[str, str] = {}
    for e in on_page:
        for k in selection.proven_skills.get(e.id, []):
            if k in demand and k not in have and k not in candidates:
                candidates[k] = e.id
    picked = sorted(candidates, key=lambda k: (-demand[k], vocab.canonical(k)))[:limit]

    # Put each skill on the line that already holds skills of its kind
    # (MongoDB beside PostgreSQL); otherwise on the line named for it.
    line_cats = []
    for line in resume.skills:
        cats = [vocab.category(k) for k in vocab.extract(", ".join(line.items))]
        line_cats.append((line, cats))
    out = []
    for k in picked:
        cat = vocab.category(k)
        best = max(line_cats, key=lambda lc: (
            lc[1].count(cat),
            cat in lc[0].category.lower(),
            -resume.skills.index(lc[0])))
        line = best[0] if best[1].count(cat) or cat in best[0].category.lower() \
            else resume.skills[-1]
        out.append((line.id, vocab.canonical(k), candidates[k]))
    return out


# --------------------------------------------------------------------- step B

_SYSTEM = """You rewrite one resume bullet at a time for emphasis and clarity.

Absolute rules:
- Use ONLY facts, technologies, tools and claims already present in the bullet \
you are given. You may reorder, tighten and rephrase.
- Never add a technology, tool, framework, company, metric or achievement that \
is not already in the bullet. If the target terminology does not fit the \
bullet's actual content, leave the wording alone.
- Tokens like <N0> are opaque placeholders. Reproduce them exactly. Never \
replace one with a number and never write any digit.
- Keep it to one line, no longer than the original, starting with a strong \
past-tense verb such as Built, Developed, Designed, Implemented, Engineered, \
Optimized, Automated, Debugged or Integrated.
- Never open with a weak verb such as Ensured, Helped, Assisted, Worked on, \
Supported, Handled or Was responsible for.
- If the bullet is already clear and well-phrased, return it EXACTLY as given. \
An unnecessary reword is worse than no change.
- Output the rewritten bullet only. No preamble, no quotes, no explanation."""


# The model emits typographic Unicode that looks fine in a terminal and breaks
# the LaTeX build -- it returned "fault‑tolerant" with a non-breaking
# hyphen during testing. Everything crossing back into the .tex is normalised
# and escaped here, at the boundary, rather than trusting the model to stay in
# ASCII because it was asked to.
_UNICODE_FIXES = {
    "‑": "-", "‐": "-", "‒": "-", "–": "--", "—": "---",
    "‘": "`", "’": "'", "“": "``", "”": "''",
    "…": "...", " ": " ", "−": "-", "×": "x",
    "•": "", "★": "", "﻿": "",
}

# Characters LaTeX treats as special. Backslash is handled separately because
# a rewrite should never introduce one.
_LATEX_ESCAPE = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                 "_": r"\_", "^": r"\^{}", "~": r"\~{}"}


def sanitise_latex(text: str, source: str) -> str:
    """Make model output safe to splice into the .tex.

    Escaping is skipped for any special character that was already present in
    the source bullet in escaped form, so existing "\\&" survives a round trip
    without becoming "\\\\&".
    """
    for bad, good in _UNICODE_FIXES.items():
        text = text.replace(bad, good)

    text = text.replace("\\", "")          # no stray macros from the model

    out: list[str] = []
    for ch in text:
        out.append(_LATEX_ESCAPE.get(ch, ch))
    cleaned = "".join(out)

    # Restore escapes that legitimately came from the source.
    if r"\&" in source:
        cleaned = cleaned.replace(r"\&", r"\&")
    return " ".join(cleaned.split())


# Openers that weaken a bullet. A resume line should say what was BUILT, not
# what was "ensured" or "helped with".
_WEAK_OPENERS = {
    "ensured", "ensuring", "helped", "helping", "assisted", "assisting",
    "worked", "working", "participated", "participating", "involved",
    "responsible", "contributed", "contributing", "engaged", "supported",
    "supporting", "utilized", "utilised", "aided", "facilitated", "handled",
}

_STRONG_OPENERS = {
    "built", "developed", "designed", "implemented", "engineered", "created",
    "delivered", "architected", "automated", "optimized", "optimised",
    "reduced", "improved", "increased", "scaled", "debugged", "resolved",
    "integrated", "deployed", "migrated", "refactored", "launched", "shipped",
    "diagnosed", "fixed", "enhanced", "streamlined", "owned", "led", "drove",
}


def _opener(text: str) -> str:
    words = re.findall(r"[A-Za-z]+", text)
    return words[0].lower() if words else ""


def accept_rewrite(original: str, rewritten: str, jd_terms: set[str],
                   vocab: Vocabulary, check_opener: bool = True) -> tuple[bool, str]:
    """Decide in CODE whether a rewrite is actually an improvement.

    The model has no quality bar of its own: asked to apply JD terminology it
    will happily turn "Developed scalable backend services" into "Ensured
    scalability of backend services", which is limper and less accurate. It is
    optimising for term overlap, not for how the line reads.

    So the rewrite has to earn its place. It is rejected -- and the original
    kept -- if it loses evidence, loses a number, or opens with a weaker verb
    than the source did. Nothing here asks the model to grade itself.
    """
    if not rewritten.strip():
        return False, "empty"

    orig_terms = set(vocab.extract(original))
    new_terms = set(vocab.extract(rewritten))

    lost = orig_terms - new_terms
    if lost:
        return False, "drops %s" % ", ".join(sorted(vocab.canonical(k) for k in lost))

    orig_nums = set(re.findall(r"\d[\d,.]*", original))
    new_nums = set(re.findall(r"\d[\d,.]*", rewritten))
    if orig_nums - new_nums:
        return False, "drops metric %s" % ", ".join(sorted(orig_nums - new_nums))

    if check_opener:
        o_open, n_open = _opener(original), _opener(rewritten)
        if n_open in _WEAK_OPENERS and o_open not in _WEAK_OPENERS:
            return False, "weaker opener %r (was %r)" % (n_open, o_open)

    gained = (new_terms - orig_terms) & jd_terms
    if len(rewritten) > len(original) * 1.15 and not gained:
        return False, "longer without adding relevant evidence"

    return True, "ok"


def _user_prompt(bullet_text: str, jd_terms: list[str]) -> str:
    terms = ", ".join(jd_terms[:10]) if jd_terms else "(none)"
    return (
        "Bullet:\n%s\n\n"
        "Terminology the job description uses (apply ONLY where it already "
        "matches the bullet's real content):\n%s"
        % (bullet_text, terms)
    )


def rewrite(p: TailorPlan, jd: JobDescription, llm: LLM,
            vocab: Vocabulary, cfg: dict, on_bullet=None) -> TailorResult:
    """Model rewrites retrieved text only, many bullets per call.

    Bullets are sent in batches (tailoring.batch_size, default 20 -- one call
    for a normal resume). One call per bullet made tailoring take about two
    minutes and burst through Groq's tokens-per-minute limit; batching keeps
    every safeguard, because each returned line is still checked on its own:

      * numbers are masked PER BULLET, so a placeholder that turns up in a
        different bullet is caught as a moved number, not silently accepted;
      * any digit the model writes itself rejects that line;
      * accept_rewrite and, later, the whitelist run on every line.

    A line that fails keeps the candidate's original wording. If a whole batch
    call fails, that batch falls back to one call per bullet.

    `on_bullet(index, total, bullet_id, outcome)` reports progress per line.
    """
    out = TailorResult(plan=p)
    jd_keys = jd.all_keys() | set(jd.ats_terms)
    total = len(p.bullets)
    size = max(1, int(cfg.get("tailoring", {}).get("batch_size", 20)))

    for start in range(0, total, size):
        chunk = p.bullets[start:start + size]
        try:
            _rewrite_batch(chunk, start, total, jd_keys, llm, vocab, cfg, out,
                           on_bullet)
        except (LLMError, ValueError) as e:
            out.warnings.append("batch rewrite failed (%s) - rewriting one "
                                "bullet at a time" % str(e)[:120])
            for j, rb in enumerate(chunk, start=start + 1):
                _rewrite_single(rb, j, total, jd_keys, llm, vocab, cfg, out,
                                on_bullet)
    return out


_BATCH_SYSTEM = """You rewrite resume bullets for emphasis and clarity. You \
receive several bullets; treat each one completely independently.

Absolute rules, for EACH bullet:
- Use ONLY facts, technologies, tools and claims already present in THAT \
bullet. You may reorder, tighten and rephrase.
- Never move anything between bullets, and never add a technology, tool, \
framework, company, metric or achievement that is not already in that bullet. \
If a target term does not fit the bullet's actual content, leave it out.
- Tokens like <N0> are opaque placeholders. Reproduce each bullet's own \
placeholders exactly. Never write a digit yourself.
- One line, no longer than the original, starting with a strong past-tense \
verb such as Built, Developed, Designed, Implemented, Engineered, Optimized, \
Automated, Debugged or Integrated. Never open with Ensured, Helped, Assisted, \
Worked on, Supported, Handled or Was responsible for.
- If a bullet is already clear and well-phrased, return it EXACTLY as given. \
An unnecessary reword is worse than no change.

Return every bullet id exactly once, with its rewritten text."""

_BATCH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["bullets"],
    "properties": {"bullets": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "text"],
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}}},
}

_PLACEHOLDER = re.compile(r"<N\d+>")


def _rewrite_batch(chunk, offset, total, jd_keys, llm, vocab, cfg, out,
                   on_bullet) -> None:
    from .llm import _NUM_RE           # same number pattern the guard uses

    counter = [0]
    items = []                          # (label, rb, own placeholder map)
    lines = []
    for n, rb in enumerate(chunk, start=1):
        mapping: dict[str, str] = {}

        def mask(m, mapping=mapping):
            key = "<N%d>" % counter[0]
            counter[0] += 1
            mapping[key] = m.group(0)
            return key

        masked = _NUM_RE.sub(mask, rb.bullet.text)
        label = "b%d" % n
        items.append((label, rb, mapping))
        # ONLY the JD terms this bullet already demonstrates. Offering the
        # whole JD vocabulary once put MySQL into a bullet about generic SQL.
        lines.append("[%s]\nbullet: %s\njob terms it already shows: %s"
                     % (label, masked, ", ".join(rb.matched[:10]) or "(none)"))

    if on_bullet:
        on_bullet(offset + 1, total, chunk[0].bullet.id, "batch")
    res = llm.complete(_BATCH_SYSTEM, "\n\n".join(lines),
                       json_schema=_BATCH_SCHEMA,
                       effort=cfg["llm"].get("reasoning_effort", "low"))
    out.calls += 1
    out.tokens_in += res.prompt_tokens
    out.tokens_out += res.completion_tokens
    got = {}
    for it in json.loads(res.text).get("bullets") or []:
        got.setdefault(str(it.get("id", "")).strip(), it.get("text") or "")

    for n, (label, rb, mapping) in enumerate(items, start=offset + 1):
        b = rb.bullet
        raw = got.get(label)
        outcome = _judge(b, raw, mapping, jd_keys, vocab, out)
        if on_bullet:
            on_bullet(n, total, b.id, outcome)


def _judge(b, raw, mapping, jd_keys, vocab, out,
           numbers_checked: bool = False) -> str:
    """Check one returned line; record it as rewritten, rejected or skipped.

    `numbers_checked=True` means the caller already verified and unmasked the
    numbers (llm.rewrite_masked does), so only the quality checks remain.
    """
    if raw is None:
        out.skipped.append((b.id, "not returned by the model"))
        return "missing"
    raw = raw.strip().strip('"').strip()
    if not numbers_checked:
        foreign = [k for k in _PLACEHOLDER.findall(raw) if k not in mapping]
        if foreign:
            out.skipped.append((b.id, "number guard: placeholder %s belongs "
                                "to another bullet" % ", ".join(foreign)))
            return "blocked"
        from .llm import _NUM_RE
        invented = _NUM_RE.findall(_PLACEHOLDER.sub("", raw))
        if invented:
            out.skipped.append((b.id, "number guard: model wrote digits %r"
                                % invented[:3]))
            return "blocked"
        for key, val in mapping.items():
            raw = raw.replace(key, val)
    text = sanitise_latex(raw, b.text)
    if not text:
        out.skipped.append((b.id, "empty response"))
        return "empty"
    ok, why = accept_rewrite(b.text, text, jd_keys, vocab)
    if not ok:
        # Keep the candidate's own wording. A rejected rewrite is a normal
        # outcome, not an error: the master resume is already decent prose.
        out.rejected.append((b.id, why, text))
        return "rejected"
    out.rewritten[b.id] = text
    return "kept"


def _rewrite_single(rb, idx, total, jd_keys, llm, vocab, cfg, out,
                    on_bullet) -> None:
    """One bullet, one call - the fallback when a batch call fails."""
    b = rb.bullet
    if on_bullet:
        on_bullet(idx, total, b.id, "start")
    try:
        res = llm.rewrite_masked(
            _SYSTEM, _user_prompt(b.text, rb.matched),
            effort=cfg["llm"].get("reasoning_effort", "low"))
        out.calls += 1
    except HallucinatedNumber as e:
        # Hard block, never a silent fallback to the model's text.
        out.calls += 1
        out.skipped.append((b.id, "number guard: %s" % e))
        if on_bullet:
            on_bullet(idx, total, b.id, "blocked")
        return
    except LLMError as e:
        out.skipped.append((b.id, "llm error: %s" % e))
        if on_bullet:
            on_bullet(idx, total, b.id, "error")
        return
    out.tokens_in += res.prompt_tokens
    out.tokens_out += res.completion_tokens
    # rewrite_masked already verified and unmasked the numbers.
    outcome = _judge(b, res.text, {}, jd_keys, vocab, out,
                     numbers_checked=True)
    if on_bullet:
        on_bullet(idx, total, b.id, outcome)


_SUMMARY_SYSTEM = """You rewrite a resume summary paragraph for emphasis.

Absolute rules:
- Use ONLY facts, technologies and claims already present in the summary you \
are given. You may reorder and re-emphasise.
- Never add a technology, tool, degree, year, company or achievement that is \
not already there. If a target term does not already appear, leave it out.
- Tokens like <N0> are opaque placeholders. Reproduce them exactly and never \
write any digit.
- Keep the same number of sentences and roughly the same length.
- Output the rewritten summary only. No preamble, no quotes, no explanation."""


def rewrite_summary(resume: Resume, jd: JobDescription, llm: LLM,
                    vocab: Vocabulary, cfg: dict) -> tuple[str, str]:
    """Re-emphasise the summary toward the JD. Returns (text, reason).

    The summary is the most JD-tailorable prose on the page and also the
    loosest, so it gets the same treatment as a bullet: only terms it already
    contains are offered as steering, numbers are masked, and the result is
    accepted only if it keeps every term and metric the original had.

    Returns ("", reason) when the rewrite is declined, and the caller keeps
    the master text.
    """
    if not cfg.get("guardrails", {}).get("summary_rewritable", False):
        return "", "disabled in config"
    if not resume.summary:
        return "", "no summary section"

    have = set(vocab.extract(resume.summary))
    steer = sorted(vocab.canonical(k) for k in (have & jd.all_keys()))

    try:
        res = llm.rewrite_masked(
            _SUMMARY_SYSTEM, _user_prompt(resume.summary, steer),
            effort=cfg["llm"].get("reasoning_effort", "low"))
    except (HallucinatedNumber, LLMError) as e:
        return "", "blocked: %s" % e

    text = sanitise_latex(res.text.strip().strip('"').strip(), resume.summary)
    # Opener rules are for bullets; a summary legitimately starts with a noun
    # phrase ("Computer Science undergraduate..."), so check evidence only.
    ok, why = accept_rewrite(resume.summary, text,
                             jd.all_keys() | set(jd.ats_terms), vocab,
                             check_opener=False)
    if not ok:
        return "", why
    return text, "ok"


def rewrite_one(bullet_text: str, jd_terms: list[str], llm: LLM,
                cfg: dict) -> str:
    """Single-bullet entry point, used by the API and by tests."""
    res = llm.rewrite_masked(
        _SYSTEM, _user_prompt(bullet_text, jd_terms),
        effort=cfg["llm"].get("reasoning_effort", "low"))
    return sanitise_latex(res.text.strip().strip('"').strip(), bullet_text)
