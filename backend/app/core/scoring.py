"""Deterministic match scoring (spec section 3).

Every number this module produces comes from arithmetic over vocabulary
matches. No model call happens here, at any point. Given the same JD, resume
and vocabulary, the score is bit-identical on every run -- which is the whole
reason the score is computed in code rather than asked for.

The model is allowed to write the *explanation text* elsewhere, never a value
in this file.

Composite:
    overall = 0.50*required + 0.20*preferred + 0.20*projects + 0.10*quals
(weights come from config.yaml and must sum to 1.0)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .jd_extract import JobDescription, Requirement
from .model import Resume
from .vocabulary import Vocabulary, normalise

STRONG = "Strong"
PARTIAL = "Partial"
MISSING = "Missing"


@dataclass
class SkillVerdict:
    requirement: str
    kind: str
    options: list[str]           # canonical names of the alternatives
    best_key: str                # which alternative the candidate has
    best_name: str
    score: float
    status: str                  # Strong | Partial | Missing
    implied: bool = False        # satisfied indirectly, not stated outright


@dataclass
class ProjectMatch:
    entry_id: str
    title: str
    score: float
    matched: list[str] = field(default_factory=list)
    tech: list[str] = field(default_factory=list)


@dataclass
class QualVerdict:
    name: str
    expected: str
    found: str
    status: str


@dataclass
class ScoreReport:
    overall: int = 0
    required_score: float = 0.0
    preferred_score: float = 0.0
    project_score: float = 0.0
    qualification_score: float = 0.0

    skills: list[SkillVerdict] = field(default_factory=list)
    projects: list[ProjectMatch] = field(default_factory=list)
    qualifications: list[QualVerdict] = field(default_factory=list)

    strong: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    weights: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    unscoreable: list[str] = field(default_factory=list)

    def breakdown(self) -> list[tuple[str, float, float, float]]:
        """(component, raw 0-1, weight, contribution in points)"""
        w = self.weights
        rows = [
            ("Required skills", self.required_score, w.get("required_skills", 0.0)),
            ("Preferred skills", self.preferred_score, w.get("preferred_skills", 0.0)),
            ("Project relevance", self.project_score, w.get("project_relevance", 0.0)),
            ("Qualifications", self.qualification_score, w.get("qualifications", 0.0)),
        ]
        return [(n, r, wt, r * wt * 100) for n, r, wt in rows]


def _status(score: float, thresholds: dict) -> str:
    if score >= thresholds.get("strong", 0.85):
        return STRONG
    if score >= thresholds.get("partial", 0.50):
        return PARTIAL
    return MISSING


def _score_requirements(
    reqs: list[Requirement], held: dict[str, float], vocab: Vocabulary,
    direct: set[str], thresholds: dict,
) -> tuple[float, list[SkillVerdict]]:
    """A requirement is satisfied by its BEST alternative, not all of them.

    This is what stops "Postgres or MySQL" scoring as a half-miss when the
    candidate has PostgreSQL.
    """
    verdicts: list[SkillVerdict] = []
    scored: list[float] = []
    seen: set[frozenset[str]] = set()

    for r in reqs:
        if not r.keys:
            # No technology named (e.g. "strong communication skills").
            # Not scoreable against the skills vocabulary; excluded from the
            # denominator rather than counted as a miss.
            continue

        # Score DISTINCT skill demands, not model-emitted rows.
        #
        # The model segments the same posting into a different number of
        # requirement statements from one run to the next, and an average over
        # rows therefore moves with the segmentation: the same Postman posting
        # scored 27% as 12 rows and 40% as 10. Deduplicating on the set of
        # alternatives makes the denominator depend only on what the job
        # actually asks for, which is the thing the score is supposed to
        # measure.
        sig = frozenset(r.keys)
        if sig in seen:
            continue
        seen.add(sig)
        best_key, best = "", 0.0
        for k in r.keys:
            v = held.get(k, 0.0)
            if v > best:
                best_key, best = k, v
        if not best_key:
            best_key = r.keys[0]
        verdicts.append(SkillVerdict(
            requirement=r.text or ", ".join(r.phrases),
            kind=r.kind,
            options=[vocab.canonical(k) for k in r.keys],
            best_key=best_key,
            best_name=vocab.canonical(best_key),
            score=best,
            status=_status(best, thresholds),
            implied=best > 0 and best_key not in direct,
        ))
        scored.append(best)

    avg = sum(scored) / len(scored) if scored else 0.0
    return avg, verdicts


def _score_projects(
    resume: Resume, jd: JobDescription, vocab: Vocabulary, top_n: int,
    extra_projects=(),
) -> tuple[float, list[ProjectMatch]]:
    """Relevance of each project to the JD's technology set.

    Score is the share of the JD's named technologies that the project
    demonstrates, so a project is rewarded for covering what the job asks for,
    not for listing many technologies.

    `extra_projects` are approved Project Knowledge Base entries. They compete
    for the same top_n slots as main.tex projects, because tailoring can put
    them on the page -- leaving them out understated the match for any job
    your GitHub work fits better than main.tex does.
    """
    jd_keys = jd.all_keys()
    matches: list[ProjectMatch] = []

    for e in [*resume.projects, *extra_projects]:
        text = " ".join([e.title, e.meta, *e.tech, *(b.text for b in e.bullets)])
        hits = set(vocab.extract(text))
        overlap = hits & jd_keys
        score = len(overlap) / len(jd_keys) if jd_keys else 0.0
        matches.append(ProjectMatch(
            entry_id=e.id, title=e.title, score=score,
            matched=sorted(vocab.canonical(k) for k in overlap),
            tech=e.tech,
        ))

    matches.sort(key=lambda m: -m.score)
    if not jd_keys:
        return 0.0, matches

    # Coverage of the JD's technology set by the projects that would actually
    # appear on the page. Plainly interpretable: 0.55 means the shortlisted
    # projects collectively demonstrate 55% of what the job asks for.
    #
    # An earlier version blended "best project" against "average of top N" with
    # a self-normalising term, which saturated at 1.0 whenever the best project
    # cleared an arbitrary floor -- it reported 0.83 for a project set covering
    # half the JD. Union coverage has no such knob.
    top = matches[:top_n]
    covered: set[str] = set()
    for m in top:
        covered.update(m.matched)
    canon_jd = {vocab.canonical(k) for k in jd_keys}
    return len(covered & canon_jd) / len(canon_jd), matches


_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _score_qualifications(
    resume: Resume, jd: JobDescription, today_year: int,
) -> tuple[float, list[QualVerdict]]:
    q = jd.qualifications
    verdicts: list[QualVerdict] = []
    checks: list[float] = []

    edu_text = " ".join(f"{e.title} {e.meta} {e.date}" for e in resume.education)
    edu_norm = normalise(edu_text)

    if q.degree:
        want = normalise(q.degree)
        tokens = [t for t in re.split(r"\W+", want) if len(t) > 3]
        hit = sum(1 for t in tokens if t in edu_norm)
        ok = hit / len(tokens) if tokens else 0.0
        verdicts.append(QualVerdict("Degree", q.degree, edu_text.strip() or "-",
                                    "Match" if ok >= 0.5 else "Check"))
        checks.append(1.0 if ok >= 0.5 else 0.0)

    if q.graduation_year:
        years = [int(m.group(0)) for m in _YEAR_RE.finditer(edu_text)]
        grad = max(years) if years else 0
        wanted = [int(m.group(0)) for m in _YEAR_RE.finditer(q.graduation_year)]
        ok = bool(grad and wanted and grad in wanted)
        verdicts.append(QualVerdict("Graduation year", q.graduation_year,
                                    str(grad or "-"), "Match" if ok else "Check"))
        checks.append(1.0 if ok else 0.0)

    if q.min_experience_years:
        m = re.search(r"\d+", q.min_experience_years)
        want_years = int(m.group(0)) if m else 0
        have = _internship_years(resume)
        ok = have >= want_years
        verdicts.append(QualVerdict(
            "Experience", "%s yrs" % want_years, "%.1f yrs (internships)" % have,
            "Match" if ok else "Below"))
        checks.append(1.0 if ok else max(0.0, have / want_years) if want_years else 1.0)

    if jd.location:
        verdicts.append(QualVerdict("Location", jd.location, "see resume header", "Review"))

    return (sum(checks) / len(checks) if checks else 1.0), verdicts


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _internship_years(resume: Resume) -> float:
    """Sum the duration of Experience entries from their date ranges."""
    total_months = 0
    for e in resume.experience:
        d = normalise(e.date)
        parts = re.findall(r"([a-z]{3})[a-z]*\s*((?:19|20)\d{2})", d)
        if len(parts) >= 2:
            (m1, y1), (m2, y2) = parts[0], parts[-1]
            months = (int(y2) - int(y1)) * 12 + (
                _MONTHS.get(m2, 1) - _MONTHS.get(m1, 1))
            total_months += max(0, months)
    return round(total_months / 12.0, 2)


def score(
    resume: Resume, jd: JobDescription, vocab: Vocabulary, cfg: dict,
    today_year: int = 2026, extra_projects=(),
) -> ScoreReport:
    weights = cfg["scoring"]["weights"]
    thresholds = cfg["scoring"]["thresholds"]
    top_n = cfg["resume"].get("max_projects", 3)

    total_w = sum(weights.values())
    rep = ScoreReport(weights=dict(weights))
    if abs(total_w - 1.0) > 1e-6:
        rep.notes.append("weights sum to %.3f, not 1.0 - normalising" % total_w)
        weights = {k: v / total_w for k, v in weights.items()}
        rep.weights = dict(weights)

    direct = set(vocab.extract(resume.evidence_text()))
    held = vocab.expand(direct)

    rep.required_score, req_v = _score_requirements(
        jd.required(), held, vocab, direct, thresholds)
    rep.preferred_score, pref_v = _score_requirements(
        jd.preferred(), held, vocab, direct, thresholds)
    rep.skills = req_v + pref_v

    rep.project_score, rep.projects = _score_projects(
        resume, jd, vocab, top_n, extra_projects)
    rep.qualification_score, rep.qualifications = _score_qualifications(
        resume, jd, today_year)

    rep.overall = round(100 * (
        weights["required_skills"] * rep.required_score
        + weights["preferred_skills"] * rep.preferred_score
        + weights["project_relevance"] * rep.project_score
        + weights["qualifications"] * rep.qualification_score
    ))

    for v in rep.skills:
        bucket = {STRONG: rep.strong, PARTIAL: rep.partial, MISSING: rep.missing}[v.status]
        if v.best_name not in bucket:
            bucket.append(v.best_name)

    # Requirements naming no skill at all are excluded from the denominator.
    # That is defensible, but it must be visible: silently dropping them is
    # exactly how a score inflates without anyone noticing.
    skipped = jd.unscoreable()
    rep.unscoreable = [r.text for r in skipped]
    if skipped:
        rep.notes.append(
            "%d of %d requirements named no skill and were excluded from scoring"
            % (len(skipped), len(jd.requirements)))

    # Spec section 14: an unmet requirement must surface, never be papered over.
    for r in jd.requirements:
        for p in r.unresolved:
            if p not in rep.missing:
                rep.notes.append("unmapped JD phrase not scored: %r" % p)
    return rep
