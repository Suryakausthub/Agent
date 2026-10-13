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

    # Bullets in scope for rewriting: experience always, plus kept projects.
    for e in resume.experience + p.projects_kept:
        for b in e.bullets:
            s, matched = _relevance(b.text, jd_keys, vocab)
            p.bullets.append(RankedBullet(bullet=b, entry_id=e.id,
                                          score=s, matched=matched))
    return p


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
    """Model rewrites retrieved text only, one bullet per call.

    `on_bullet(index, total, bullet_id, outcome)` is called after each bullet
    so the UI can show which one is being worked on. This stage is the bulk of
    the wall-clock time -- one network round trip per bullet -- and without
    per-bullet reporting it reads as a single unexplained 25-second pause.
    """
    out = TailorResult(plan=p)
    jd_keys = jd.all_keys() | set(jd.ats_terms)
    total = len(p.bullets)

    for idx, rb in enumerate(p.bullets, start=1):
        b = rb.bullet
        if on_bullet:
            on_bullet(idx, total, b.id, "start")
        # ONLY the JD terms this bullet already demonstrates.
        #
        # Passing the full JD vocabulary was measurably harmful: it took the
        # run from 0 to 2 hallucinations, both MySQL, which the model pulled
        # from a "Postgres or MySQL" requirement and inserted into a bullet
        # about generic SQL. Offering a term the candidate does not have is
        # offering it as material. Retrieved context must contain only what is
        # already supported -- that is the whole point of extract-then-rewrite
        # (spec 15.1), and widening it quietly reopens the hole.
        terms = rb.matched
        try:
            res = llm.rewrite_masked(
                _SYSTEM, _user_prompt(b.text, terms),
                effort=cfg["llm"].get("reasoning_effort", "low"),
            )
        except HallucinatedNumber as e:
            # Hard block, never a silent fallback to the model's text.
            out.skipped.append((b.id, "number guard: %s" % e))
            if on_bullet:
                on_bullet(idx, total, b.id, "blocked")
            continue
        except LLMError as e:
            out.skipped.append((b.id, "llm error: %s" % e))
            if on_bullet:
                on_bullet(idx, total, b.id, "error")
            continue

        text = sanitise_latex(res.text.strip().strip('"').strip(), b.text)
        out.tokens_in += res.prompt_tokens
        out.tokens_out += res.completion_tokens

        if not text:
            out.skipped.append((b.id, "empty response"))
            if on_bullet:
                on_bullet(idx, total, b.id, "empty")
            continue

        ok, why = accept_rewrite(b.text, text, jd_keys, vocab)
        if on_bullet:
            on_bullet(idx, total, b.id, "kept" if ok else "rejected")
        if not ok:
            # Keep the candidate's own wording. A rejected rewrite is a normal
            # outcome, not an error: the master resume is already decent prose.
            out.rejected.append((b.id, why, text))
            continue

        out.rewritten[b.id] = text

    return out


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
