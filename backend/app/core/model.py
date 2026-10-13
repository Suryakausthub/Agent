"""Structured model of a LaTeX resume.

Every node keeps a (start, end) character span into the ORIGINAL main.tex.
Tailoring mutates the model, then `render` splices spans back into the source
text instead of regenerating LaTeX from scratch. That keeps the preamble, the
indentation, and structural quirks (the itemize that spans Experience and
Projects) byte-identical, and it gives every bullet a traceable origin, which
is what spec section 15.5 requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Span:
    start: int
    end: int

    def text(self, src: str) -> str:
        return src[self.start:self.end]


@dataclass
class Bullet:
    id: str
    text: str            # LaTeX inside \resumeItem{...} or \item
    span: Span           # covers the whole \resumeItem{...} call
    line: int            # 1-indexed line in main.tex, for the audit trail

    # populated by the tailoring pipeline
    tailored: str | None = None

    @property
    def current(self) -> str:
        return self.tailored if self.tailored is not None else self.text


@dataclass
class Entry:
    """An Experience entry, a Project, or an Education entry."""
    id: str
    kind: str                       # experience | project | education
    title: str
    meta: str                       # company+location, or tech stack
    date: str
    bullets: list[Bullet] = field(default_factory=list)
    tech: list[str] = field(default_factory=list)
    span: Span | None = None        # heading through the entry's last bullet
    line: int = 0
    dropped: bool = False           # set by the trim loop

    # Where a safe deletion of this entry must STOP. The parsed span runs to
    # the next heading or the section end, and for the last entry in a section
    # that sweeps up the enclosing \resumeSubHeadingListEnd -- deleting it
    # would leave an unbalanced itemize. remove_end stops just after the
    # entry's own \resumeItemListEnd instead.
    remove_end: int = 0

    # The 4-arg subheading macro renders as a 2x2 grid, and this template
    # reuses the slots with different meaning per section: Experience puts
    # dates top-right and location bottom-right, Education puts CGPA top-right
    # and years bottom-right. Keep the raw slots so nothing is lost.
    org: str = ""                   # arg3, bottom-left
    right_top: str = ""             # arg2
    right_bottom: str = ""          # arg4

    # "master" entries were parsed from main.tex and are spliced by span.
    # "bank" entries come from the Project Knowledge Base, have no span, and
    # are rendered as new LaTeX (render._bank_block).
    origin: str = "master"
    source_ref: str = ""            # GitHub URL for bank entries


@dataclass
class SkillLine:
    id: str
    category: str                   # "Languages"
    items: list[str]
    span: Span
    line: int
    order: int = 0                  # rewritten by relevance ranking


@dataclass
class ListItem:
    """A plain item entry in Certifications or Publications."""
    id: str
    text: str          # raw LaTeX, used for splicing
    span: Span
    line: int
    plain: str = ""    # formatting macros stripped, used for matching/display


@dataclass
class Resume:
    source: str                                  # full original main.tex
    contact: Span | None = None
    summary_span: Span | None = None
    summary: str = ""
    summary_tailored: str | None = None

    skills: list[SkillLine] = field(default_factory=list)
    experience: list[Entry] = field(default_factory=list)
    projects: list[Entry] = field(default_factory=list)
    education: list[Entry] = field(default_factory=list)
    certifications: list[ListItem] = field(default_factory=list)
    publications: list[ListItem] = field(default_factory=list)

    section_order: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def all_bullets(self) -> list[Bullet]:
        out: list[Bullet] = []
        for e in self.experience + self.projects:
            out.extend(e.bullets)
        return out

    def evidence_text(self) -> str:
        """Every factual string the candidate actually has.

        This is the corpus the anti-hallucination whitelist is built from
        (spec section 15.2): nothing may appear in tailored output that is
        not derivable from here.
        """
        parts = [self.summary]
        for s in self.skills:
            parts.extend(s.items)
        for e in self.experience + self.projects + self.education:
            parts += [e.title, e.meta, e.date, *e.tech]
            parts += [b.text for b in e.bullets]
        parts += [c.text for c in self.certifications]
        parts += [p.text for p in self.publications]
        return "\n".join(p for p in parts if p)
