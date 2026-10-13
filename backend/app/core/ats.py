"""ATS keyword coverage (spec section 5).

Pure string/synonym lookup through the vocabulary -- identical output on every
run, by design. The spec is explicit that this must not be an LLM judgement
call, because an applicant tracking system is itself a deterministic matcher
and any drift here would misreport how the resume will actually be read.

Beyond presence, this tracks PLACEMENT. Most ATS implementations weight the
skills section and role titles above prose, and a keyword that appears only in
the summary is weak evidence. A resume can be "100% covered" and still read
badly if every match is buried in one sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .jd_extract import JobDescription
from .model import Resume
from .vocabulary import Vocabulary

FOUND = "Found"
PARTIAL = "Partial"
MISSING = "Missing"

# vocabulary category -> ATS bucket from the spec
_BUCKET = {
    "language": "Technical",
    "framework": "Technical",
    "concept": "Technical",
    "tool": "Tools",
    "platform": "Tools",
    "database": "Tools",
    "practice": "Domain",
}

# Rough weighting of where a keyword lands, for the placement warnings.
_PLACEMENT_WEIGHT = {
    "skills": 1.0,
    "experience": 0.9,
    "projects": 0.8,
    "certifications": 0.5,
    "summary": 0.4,
    "publications": 0.3,
}


@dataclass
class KeywordRow:
    key: str
    canonical: str
    bucket: str                                  # Technical | Tools | Domain
    jd_hits: int                                 # times it appears in the JD
    status: str                                  # Found | Partial | Missing
    strength: float                              # 0-1 from the implication graph
    locations: list[str] = field(default_factory=list)
    implied_by: str = ""


@dataclass
class ATSReport:
    rows: list[KeywordRow] = field(default_factory=list)
    coverage: float = 0.0                        # Found only
    weighted_coverage: float = 0.0               # Found + partial credit
    by_bucket: dict[str, tuple[int, int]] = field(default_factory=dict)
    placement_warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def table(self) -> list[KeywordRow]:
        order = {FOUND: 0, PARTIAL: 1, MISSING: 2}
        return sorted(self.rows, key=lambda r: (order[r.status], r.bucket, -r.jd_hits))


def _section_hits(resume: Resume, vocab: Vocabulary) -> dict[str, set[str]]:
    """Which vocabulary terms appear in which section of the resume."""
    out: dict[str, set[str]] = {}

    skills_text = " ".join(
        "%s %s" % (s.category, " ".join(s.items)) for s in resume.skills)
    out["skills"] = set(vocab.extract(skills_text))

    exp_text = " ".join(
        "%s %s %s" % (e.title, e.meta, " ".join(b.text for b in e.bullets))
        for e in resume.experience)
    out["experience"] = set(vocab.extract(exp_text))

    proj_text = " ".join(
        "%s %s %s" % (e.title, " ".join(e.tech), " ".join(b.text for b in e.bullets))
        for e in resume.projects)
    out["projects"] = set(vocab.extract(proj_text))

    out["summary"] = set(vocab.extract(resume.summary))
    out["certifications"] = set(vocab.extract(
        " ".join(c.plain or c.text for c in resume.certifications)))
    out["publications"] = set(vocab.extract(
        " ".join(p.plain or p.text for p in resume.publications)))
    return out


def analyse(resume: Resume, jd: JobDescription, vocab: Vocabulary,
            thresholds: dict | None = None) -> ATSReport:
    thresholds = thresholds or {"strong": 0.85, "partial": 0.50}
    rep = ATSReport()

    sections = _section_hits(resume, vocab)
    direct: set[str] = set()
    for keys in sections.values():
        direct |= keys
    held = vocab.expand(direct)

    # Every term the JD mentions anywhere, not only inside a parsed requirement
    # -- an ATS scans the whole posting, so we should too.
    jd_terms = jd.ats_terms or vocab.extract(jd.raw)

    for key, surfaces in jd_terms.items():
        strength = held.get(key, 0.0)
        locations = [name for name, keys in sections.items() if key in keys]

        if key in direct:
            status = FOUND
        elif strength >= thresholds.get("partial", 0.5):
            status = PARTIAL
        else:
            status = MISSING

        implied_by = ""
        if status == PARTIAL:
            for held_key in direct:
                for implied, s in vocab.implies.get(held_key, []):
                    if implied == key and s == strength:
                        implied_by = vocab.canonical(held_key)
                        break
                if implied_by:
                    break

        rep.rows.append(KeywordRow(
            key=key, canonical=vocab.canonical(key),
            bucket=_BUCKET.get(vocab.category(key), "Domain"),
            jd_hits=len(surfaces), status=status, strength=strength,
            locations=sorted(locations, key=lambda l: -_PLACEMENT_WEIGHT.get(l, 0)),
            implied_by=implied_by,
        ))

    total = len(rep.rows)
    if total:
        found = sum(1 for r in rep.rows if r.status == FOUND)
        rep.coverage = found / total
        rep.weighted_coverage = sum(
            1.0 if r.status == FOUND else r.strength for r in rep.rows) / total

    for bucket in ("Technical", "Tools", "Domain"):
        rows = [r for r in rep.rows if r.bucket == bucket]
        rep.by_bucket[bucket] = (
            sum(1 for r in rows if r.status == FOUND), len(rows))

    rep.missing = [r.canonical for r in rep.rows if r.status == MISSING]
    rep.placement_warnings = _placement_warnings(rep, jd)
    return rep


def _placement_warnings(rep: ATSReport, jd: JobDescription) -> list[str]:
    """Flag keywords whose only evidence sits in a weak part of the page."""
    warnings: list[str] = []

    weak_only = [
        r.canonical for r in rep.rows
        if r.status == FOUND and r.locations
        and all(_PLACEMENT_WEIGHT.get(l, 0) <= 0.5 for l in r.locations)
    ]
    if weak_only:
        warnings.append(
            "present only in low-weight sections (summary/certs/publications): %s"
            % ", ".join(sorted(weak_only)[:8]))

    not_in_skills = [
        r.canonical for r in rep.rows
        if r.status == FOUND and "skills" not in r.locations
    ]
    if not_in_skills:
        warnings.append(
            "demonstrated in experience/projects but absent from the Skills "
            "section, where most parsers look first: %s"
            % ", ".join(sorted(not_in_skills)[:8]))

    # Title alignment: ATS commonly scores the role title against the posting.
    if jd.title:
        title_terms = set(w for w in jd.title.lower().split() if len(w) > 3)
        if title_terms:
            warnings.append(
                "job title is %r -- confirm the resume reflects this role framing"
                % jd.title)
    return warnings
