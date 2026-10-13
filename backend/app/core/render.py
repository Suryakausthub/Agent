"""Render a tailored resume by splicing edits into the original main.tex.

Nothing is regenerated from a template. Every edit is a character-range
replacement against the master source, applied in descending offset order so
earlier spans stay valid while later ones are rewritten. The preamble, the
macro definitions, the indentation and the itemize that spans Experience and
Projects all survive untouched, because they are never rewritten -- only the
regions we explicitly target are.

This is also what makes the audit trail real: each replacement is tied to the
exact source offsets it came from, so "which line did this bullet come from"
is a lookup, not a reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Entry, Resume
from .tailor import TailorPlan, TailorResult, sanitise_latex


@dataclass
class Edit:
    start: int
    end: int
    replacement: str
    kind: str            # bullet | drop_project | skills | summary
    ref: str = ""

    @property
    def is_deletion(self) -> bool:
        return not self.replacement.strip()


@dataclass
class RenderResult:
    tex: str
    edits: list[Edit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.edits:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out


def _apply(src: str, edits: list[Edit]) -> str:
    """Apply non-overlapping edits from the end of the file backwards."""
    ordered = sorted(edits, key=lambda e: -e.start)
    out = src
    last_start = len(src) + 1
    for e in ordered:
        if e.end > last_start:
            raise ValueError(
                "overlapping edits: %s at %d-%d collides with an edit starting %d"
                % (e.kind, e.start, e.end, last_start))
        out = out[:e.start] + e.replacement + out[e.end:]
        last_start = e.start
    return out


def build_edits(resume: Resume, plan: TailorPlan,
                result: TailorResult) -> list[Edit]:
    edits: list[Edit] = []

    # 1. Rewritten bullets. The span covers the whole \resumeItem{...} call,
    # so the macro is reissued rather than patched inside its braces.
    by_id = {b.id: b for b in resume.all_bullets()}
    for bid, text in result.rewritten.items():
        b = by_id.get(bid)
        if b is None:
            continue
        edits.append(Edit(
            start=b.span.start, end=b.span.end,
            replacement="\\resumeItem{%s}" % text,
            kind="bullet", ref=bid,
        ))

    # 2. Dropped projects, cut at the safe boundary.
    for e in plan.projects_dropped:
        if e.span is None:
            continue
        end = e.remove_end or e.span.end
        edits.append(Edit(start=e.span.start, end=end,
                          replacement="", kind="drop_project", ref=e.id))

    # 3. Skills reordering: rewrite the block as one contiguous replacement so
    # individual line spans cannot interleave.
    if plan.skill_order and resume.skills:
        by_line = {s.id: s for s in resume.skills}
        ordered = [by_line[sid] for sid in plan.skill_order if sid in by_line]
        # A SHORTER order means the trim loop dropped lines, which is the
        # whole point of skills_lowest_relevance. An earlier version required
        # len(ordered) == len(resume.skills) and so silently skipped the edit
        # the moment a line was dropped -- the loop reported cuts that never
        # reached the document.
        if ordered:
            first = min(s.span.start for s in resume.skills)
            last = max(s.span.end for s in resume.skills)
            block = "\n\n".join(
                resume.source[s.span.start:s.span.end].strip() for s in ordered)
            edits.append(Edit(start=first, end=last,
                              replacement=block + "\n\n",
                              kind="skills", ref="skills"))

    # 4. Summary, if it was tailored.
    if result.summary and resume.summary_span:
        edits.append(Edit(
            start=resume.summary_span.start, end=resume.summary_span.end,
            replacement="\n\\small\n%s\n\n\n" % result.summary,
            kind="summary", ref="summary"))

    return edits


def _bank_block(e: Entry, result: TailorResult) -> str:
    """LaTeX for a Knowledge Base project, in the same macros main.tex uses.

    The date slot is left empty: the knowledge base knows when a repository was
    last pushed, not when the project was done, and guessing a year would be
    an unsupported claim.
    """
    tech = ", ".join(sanitise_latex(t, "") for t in e.tech)
    head = "\\textbf{%s}" % sanitise_latex(e.title, "")
    if tech:
        head += " --- %s" % tech
    items = "\n\n".join("\\resumeItem{%s}" % result.rewritten.get(b.id, b.text)
                        for b in e.bullets)
    return ("\\resumeProjectHeading\n{%s}{%s}\n\n\\resumeItemListStart\n\n"
            "%s\n\n\\resumeItemListEnd" % (head, e.date, items))


def _projects_region(resume: Resume, plan: TailorPlan,
                     result: TailorResult) -> Edit | None:
    """Rebuild the whole Projects list when Knowledge Base projects are used.

    Span splicing can only edit text that already exists in main.tex; a
    knowledge-base project has no span, and the selector's order can differ
    from the file's. So the region from the first master project heading to
    the last one's list terminator is replaced as one edit: kept master
    projects are copied from source (with their own bullet rewrites applied),
    bank projects are generated, in the plan's ranked order. Everything
    outside the region -- \\section{Projects}, the enclosing
    \\resumeSubHeadingListEnd -- is untouched.
    """
    masters = [e for e in resume.projects if e.span is not None]
    if not masters:
        return None
    start = min(e.span.start for e in masters)
    end = max(e.remove_end or e.span.end for e in masters)

    by_id = {e.id: e for e in masters}
    blocks: list[str] = []
    for e in plan.projects_kept:
        if e.origin == "bank":
            blocks.append(_bank_block(e, result))
            continue
        src_e = by_id.get(e.id)
        if src_e is None:
            continue
        lo, hi = src_e.span.start, src_e.remove_end or src_e.span.end
        local = [Edit(b.span.start - lo, b.span.end - lo,
                      "\\resumeItem{%s}" % result.rewritten[b.id], "bullet", b.id)
                 for b in src_e.bullets if b.id in result.rewritten]
        blocks.append(_apply(resume.source[lo:hi], local).strip())
    return Edit(start=start, end=end, replacement="\n\n".join(blocks),
                kind="projects", ref="projects")


def render(resume: Resume, plan: TailorPlan, result: TailorResult) -> RenderResult:
    edits = build_edits(resume, plan, result)

    if any(e.origin == "bank" for e in plan.projects_kept):
        region = _projects_region(resume, plan, result)
        if region is not None:
            # The region edit owns every project; drop per-project edits that
            # would overlap it.
            edits = [e for e in edits
                     if e.kind not in ("drop_project",)
                     and not (e.kind == "bullet"
                              and region.start <= e.start < region.end)]
            edits.append(region)

    # A bullet inside a dropped project would be an overlapping edit; the
    # deletion wins, so discard those rewrites rather than failing.
    drops = [(e.start, e.end) for e in edits if e.kind == "drop_project"]
    kept: list[Edit] = []
    dropped_bullets = 0
    for e in edits:
        if e.kind == "bullet" and any(s <= e.start and e.end <= t for s, t in drops):
            dropped_bullets += 1
            continue
        kept.append(e)

    tex = _apply(resume.source, kept)
    res = RenderResult(tex=tex, edits=kept)
    if dropped_bullets:
        res.warnings.append(
            "%d rewritten bullet(s) discarded - their project was dropped"
            % dropped_bullets)

    _structural_check(tex, res)
    return res


def _structural_check(tex: str, res: RenderResult) -> None:
    """Catch a broken render before it ever reaches the compiler."""
    pairs = [
        (r"\begin{itemize}", r"\end{itemize}"),
        (r"\resumeItemListStart", r"\resumeItemListEnd"),
        (r"\resumeSubHeadingListStart", r"\resumeSubHeadingListEnd"),
        (r"\begin{document}", r"\end{document}"),
    ]
    for open_tok, close_tok in pairs:
        a, b = tex.count(open_tok), tex.count(close_tok)
        if a != b:
            res.warnings.append(
                "unbalanced %s/%s (%d/%d)" % (open_tok, close_tok, a, b))

    if tex.count("{") != tex.count("}"):
        res.warnings.append("brace count mismatch (%d open, %d close)"
                            % (tex.count("{"), tex.count("}")))
