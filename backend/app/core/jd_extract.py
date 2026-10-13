"""Turn a raw job description into a structured, scoreable object.

Division of labour is deliberate (spec section 15.1):

  * The model does LINGUISTIC work only -- splitting prose into discrete
    requirement statements, labelling them required vs preferred, and grouping
    alternatives ("Postgres or MySQL" is one requirement with two options).
    It is good at this and it is not a factual judgement.

  * CODE does every TERM RESOLUTION, via the vocabulary. The model never
    decides that "K8s" means Kubernetes, and never assigns a score. That keeps
    matching reproducible run to run, which a model at any temperature is not.

Extracted scalar fields are verified against the source text before being
trusted, so a hallucinated company name or job ID is caught rather than stored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm import LLM
from .vocabulary import Vocabulary, normalise


@dataclass
class Requirement:
    text: str                        # verbatim-ish statement from the JD
    kind: str                        # required | preferred
    phrases: list[str] = field(default_factory=list)   # alternative skill phrases
    keys: list[str] = field(default_factory=list)      # resolved vocabulary keys
    unresolved: list[str] = field(default_factory=list)
    recovered: bool = False          # any_of was rebuilt in code, not by the model
    split: bool = False              # was one conjunctive requirement, separated

    @property
    def is_required(self) -> bool:
        return self.kind == "required"

    @property
    def scoreable(self) -> bool:
        return bool(self.keys)


@dataclass
class Qualifications:
    degree: str = ""
    graduation_year: str = ""
    min_experience_years: str = ""
    other: list[str] = field(default_factory=list)


@dataclass
class JobDescription:
    raw: str = ""
    company: str = ""
    title: str = ""
    location: str = ""
    job_id: str = ""
    employment_type: str = ""
    level: str = ""
    category: str = ""
    requirements: list[Requirement] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    qualifications: Qualifications = field(default_factory=Qualifications)
    ats_terms: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def required(self) -> list[Requirement]:
        return [r for r in self.requirements if r.is_required]

    def preferred(self) -> list[Requirement]:
        return [r for r in self.requirements if not r.is_required]

    def unscoreable(self) -> list[Requirement]:
        return [r for r in self.requirements if not r.scoreable]

    def all_keys(self) -> set[str]:
        out: set[str] = set()
        for r in self.requirements:
            out.update(r.keys)
        return out


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["company", "title", "location", "job_id", "employment_type",
                 "level", "category", "requirements", "soft_skills",
                 "qualifications"],
    "properties": {
        "company": {"type": "string"},
        "title": {"type": "string"},
        "location": {"type": "string"},
        "job_id": {"type": "string"},
        "employment_type": {"type": "string"},
        "level": {"type": "string"},
        "category": {"type": "string"},
        "soft_skills": {"type": "array", "items": {"type": "string"}},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "kind", "any_of"],
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["required", "preferred"]},
                    "any_of": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "qualifications": {
            "type": "object",
            "additionalProperties": False,
            "required": ["degree", "graduation_year", "min_experience_years", "other"],
            "properties": {
                "degree": {"type": "string"},
                "graduation_year": {"type": "string"},
                "min_experience_years": {"type": "string"},
                "other": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

_SYSTEM = """You extract structure from job descriptions. You do not evaluate \
candidates and you do not score anything.

Rules:
1. Copy values from the job description. If a field is not stated, return an \
empty string. Never guess a company name, job ID, or location.
2. Split requirements into one object per distinct requirement.
3. "kind" is "required" for must-haves and "preferred" for nice-to-haves. If \
the posting does not separate them, use your judgement from the wording.
4. "any_of" lists the interchangeable skills in that one requirement. \
"Postgres or MySQL" gives any_of ["Postgres", "MySQL"]. "Java and Spring Boot" \
is TWO separate requirements, each with one entry in any_of.
5. any_of must include EVERY named skill, technology, tool, practice or \
computer-science topic in the requirement -- not only product names. \
"data structures and algorithms", "CI/CD pipelines", "RESTful microservices", \
"query tuning", "unit testing" and "distributed systems" are all skills and \
belong in any_of. Leave any_of empty ONLY when the requirement names no skill \
at all, such as "excellent communication" or "prior internship experience".
6. Put each skill phrase in any_of exactly as the posting writes it. Do not \
normalise, expand, or translate abbreviations.
7. Return only the JSON object."""


_ALTERNATIVE_MARKERS = (" or ", "/", " either ", " any of ", " one of ")

# Conjunction separators: these split a requirement into independent demands.
_AND_SPLIT = re.compile(r"\band\b|\bas well as\b|\bplus\b|[,;]")


def _is_alternation(segment: str, keys: list[str], vocab: Vocabulary) -> bool:
    """Do these terms sit in an either/or relationship within this text?

    Term surface forms are scrubbed first, so punctuation *inside* a name --
    CI/CD, TCP/IP, Node.js -- is not mistaken for an alternation separator.
    """
    scrubbed = " %s " % segment.lower()
    for k in keys:
        term = vocab.terms.get(k)
        surfaces = [vocab.canonical(k)] + (term.aliases if term else [])
        for s in sorted(surfaces, key=len, reverse=True):
            scrubbed = scrubbed.replace(s.lower(), " ")
    return any(m in scrubbed for m in _ALTERNATIVE_MARKERS)


def _split_conjunctions(reqs: list[Requirement], vocab: Vocabulary) -> list[Requirement]:
    """Separate genuine alternatives from conjunctions, including nested ones.

    Real postings mix both in one sentence:

        "JavaScript/TypeScript, Node.js and databases (SQL/NoSQL)"
         = (JS | TS)  AND  Node  AND  (SQL | NoSQL)

    An earlier version decided a whole requirement was EITHER alternatives or
    separate demands. On the line above it saw the slashes, called all five
    terms alternatives, and a candidate holding only SQL scored it 1.00 --
    JavaScript and Node.js disappeared from the Missing list and the overall
    score rose 13 points. So: segment on conjunctions first, then decide
    alternation within each segment independently.
    """
    out: list[Requirement] = []
    for r in reqs:
        if len(r.keys) < 2:
            out.append(r)
            continue

        # Map each AND-segment to the terms it actually contains.
        segments = [s for s in _AND_SPLIT.split(r.text) if s and s.strip()]
        groups: list[list[str]] = []
        claimed: set[str] = set()
        for seg in segments:
            found = [k for k in vocab.extract(seg) if k in r.keys]
            found = [k for k in found if k not in claimed]
            if found:
                groups.append(found)
                claimed.update(found)

        # Terms the segmentation could not place (e.g. the model's any_of
        # listed a phrase absent from `text`) stay together as one group
        # rather than being dropped.
        leftover = [k for k in r.keys if k not in claimed]
        if leftover:
            groups.append(leftover)

        if len(groups) <= 1:
            keys = groups[0] if groups else r.keys
            if _is_alternation(r.text, keys, vocab):
                out.append(r)
                continue
            groups = [[k] for k in keys]

        for i, keys in enumerate(groups):
            seg_text = next((s.strip() for s in segments
                             if set(vocab.extract(s)) & set(keys)), r.text)
            if len(keys) > 1 and not _is_alternation(seg_text, keys, vocab):
                sub = [[k] for k in keys]
            else:
                sub = [keys]
            for ks in sub:
                out.append(Requirement(
                    text=seg_text.strip() or r.text, kind=r.kind,
                    phrases=[vocab.canonical(k) for k in ks], keys=list(ks),
                    unresolved=list(r.unresolved) if i == 0 else [],
                    recovered=r.recovered, split=True,
                ))
    return out


def _verify(value: str, source: str, field_name: str, warnings: list[str]) -> str:
    """Scalar fields must actually occur in the JD; otherwise drop them."""
    if not value:
        return ""
    if normalise(value) in normalise(source):
        return value
    warnings.append("dropped %s=%r (not found in source text)" % (field_name, value))
    return ""


def extract(raw: str, llm: LLM, vocab: Vocabulary) -> JobDescription:
    res = llm.complete(_SYSTEM, raw, json_schema=_SCHEMA, effort="medium")
    try:
        data = json.loads(res.text)
    except json.JSONDecodeError as e:
        raise ValueError("model returned non-JSON: %s" % e) from e

    jd = JobDescription(raw=raw)
    w = jd.warnings

    jd.company = _verify(data.get("company", ""), raw, "company", w)
    jd.title = _verify(data.get("title", ""), raw, "title", w)
    jd.location = _verify(data.get("location", ""), raw, "location", w)
    jd.job_id = _verify(data.get("job_id", ""), raw, "job_id", w)
    # level / type / category are classifications, not quotes, so not verified
    jd.employment_type = data.get("employment_type", "")
    jd.level = data.get("level", "")
    jd.category = data.get("category", "")
    jd.soft_skills = [s for s in data.get("soft_skills", []) if s]

    q = data.get("qualifications", {}) or {}
    jd.qualifications = Qualifications(
        degree=q.get("degree", ""),
        graduation_year=_verify(q.get("graduation_year", ""), raw, "graduation_year", w),
        min_experience_years=_verify(q.get("min_experience_years", ""), raw,
                                     "min_experience_years", w),
        other=[o for o in (q.get("other") or []) if o],
    )

    # Term resolution happens here, in code, never in the model.
    for item in data.get("requirements", []) or []:
        phrases = [p for p in (item.get("any_of") or []) if p.strip()]
        text = (item.get("text") or "").strip()
        recovered = False

        # Even with rule 5, the model still returns an empty any_of for some
        # requirements that plainly name a skill. Those would be dropped from
        # the scoring denominator and inflate the result, so recover them in
        # code by scanning the requirement text. Deterministic, and it does not
        # depend on the model cooperating.
        if not phrases and text:
            hits = vocab.extract(text)
            if hits:
                phrases = [vocab.canonical(k) for k in hits]
                recovered = True

        keys: list[str] = []
        unresolved: list[str] = []
        for p in phrases:
            k = vocab.resolve(p)
            if k is not None:
                if k not in keys:
                    keys.append(k)
                continue
            # A single phrase can name several terms -- "RESTful microservices"
            # is REST plus Microservices. Take all of them; whether they end up
            # as alternatives or as separate requirements is decided by
            # _split_conjunctions from the surrounding text, not here.
            hits = vocab.extract(p)
            if hits:
                for hk in hits:
                    if hk not in keys:
                        keys.append(hk)
            else:
                unresolved.append(p)

        jd.requirements.append(Requirement(
            text=text, kind=item.get("kind", "required"),
            phrases=phrases, keys=keys, unresolved=unresolved,
            recovered=recovered,
        ))

    jd.requirements = _split_conjunctions(jd.requirements, vocab)
    jd.ats_terms = vocab.extract(raw)

    if not jd.requirements:
        w.append("no requirements extracted")

    n_recovered = sum(1 for r in jd.requirements if r.recovered)
    if n_recovered:
        w.append("%d requirement(s) had an empty any_of and were recovered in code"
                 % n_recovered)

    unresolved_all = sorted({p for r in jd.requirements for p in r.unresolved})
    if unresolved_all:
        w.append("unresolved skill phrases (consider adding to vocabulary.yaml): %s"
                 % ", ".join(unresolved_all[:12]))
    return jd
