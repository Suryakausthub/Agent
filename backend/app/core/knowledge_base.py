"""Project Knowledge Base: one rich, verified record per repository.

This supersedes the three-bullet Project Bank entry (project_extract.py). The
old entry was a finished answer -- three fixed bullets -- so every job got the
same three lines. A knowledge-base record is the *material* instead: what the
project is, how it is built, what is actually implemented, and a library of
ten candidate bullets that each cover a different strength. Per job, the
selector (project_select.py) picks at most three of them.

Division of labour, same as everywhere else in this agent:

  * The MODEL reads the evidence bundle once and writes the structured record.
  * CODE then checks the record against the evidence and removes what it
    cannot support, rather than trusting the model to have behaved:
      - a technology is kept only if it appears in the repository (manifests,
        README, file paths or file contents);
      - a bullet naming a technology the repository does not contain is
        dropped, not softened;
      - numbers survive only when they trace to a harvested measurable claim;
      - overstated phrasing is capped (reused from project_extract);
      - evidence paths must be paths that were actually read;
      - near-duplicate bullets are removed so the ten are genuinely distinct.

Everything lands as needs_review. Regenerating a record resets its review,
because approving one text must never carry over to a different text.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .llm import LLM, LLMError
from .project_extract import (_support_flags, normalise_text,
                              parse_dependencies, soften_claims)
from .vocabulary import Vocabulary

KB_VERSION = 2
BULLETS_WANTED = 10

FOCUS = ["backend", "api", "database", "distributed_systems", "performance",
         "reliability", "testing", "automation", "cloud", "devops", "security",
         "architecture", "frontend", "data", "ml", "networking", "systems",
         "other"]

TECH_CATEGORIES = ["languages", "frameworks", "libraries", "databases",
                   "apis_integrations", "messaging", "cloud_devops", "testing",
                   "tools"]

_STR_LIST = {"type": "array", "items": {"type": "string"}}

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "summary", "description", "problem",
                 "architecture_overview", "components", "tech_stack",
                 "implementation", "contributions", "bullets", "keywords",
                 "gaps", "ownership_note", "complexity_note"],
    "properties": {
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "problem": {"type": "string"},
        "architecture_overview": {"type": "string"},
        "components": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "responsibility", "paths"],
            "properties": {"name": {"type": "string"},
                           "responsibility": {"type": "string"},
                           "paths": _STR_LIST}}},
        "tech_stack": {
            "type": "object", "additionalProperties": False,
            "required": TECH_CATEGORIES,
            "properties": {c: _STR_LIST for c in TECH_CATEGORIES}},
        "implementation": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["detail", "status", "evidence"],
            "properties": {
                "detail": {"type": "string"},
                # Enums are validated only AFTER generation on Groq, so one
                # off-list value fails the whole call. Normalised in code.
                "status": {"type": "string"},
                "evidence": _STR_LIST}}},
        "contributions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "evidence"],
            "properties": {"text": {"type": "string"},
                           "evidence": _STR_LIST}}},
        "bullets": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "focus", "technologies", "evidence"],
            "properties": {
                "text": {"type": "string"},
                "focus": {"type": "string"},
                "technologies": _STR_LIST,
                "evidence": _STR_LIST}}},
        "keywords": _STR_LIST,
        "gaps": _STR_LIST,
        "ownership_note": {"type": "string"},
        "complexity_note": {"type": "string"},
    },
}

_SYSTEM = """You build a factual knowledge-base record for ONE software \
project from repository evidence. The record is reused for many job \
applications, so it must be complete, specific and strictly true.

Absolute rules:
- Use ONLY what the evidence shows. Never invent a technology, feature, \
endpoint, dataset, user count, metric or performance figure.
- Technologies: list only what appears in the manifests, file paths, \
README or code shown. Put each in the most fitting tech_stack category; \
leave a category empty rather than guessing.
- Numbers: the only figures you may write are the MEASURABLE CLAIMS listed \
in the brief, exactly as given. Version numbers, ports and years are not \
achievements. Digits that are part of a technology name (IPv6, S3, OAuth2) \
are fine.
- This is a student or personal project. Never call it production-grade, \
enterprise, highly scalable, fault-tolerant, high-availability or \
industry-standard unless the evidence proves it.
- Separate what EXISTS from what is planned. A README "TODO", "roadmap", \
"coming soon" or an empty stub is status planned or partial, and must not \
appear in any bullet.
- Evidence paths must be copied exactly from the FILES READ list (or \
README.md).

Fields:
- summary: one sentence.
- description: 4-6 sentences - what it does, who it is for, the main flow.
- problem: the problem it solves, one or two sentences.
- architecture_overview: how the pieces fit together, 2-4 sentences.
- components: the major modules/services and their responsibility.
- implementation: 6-12 concrete technical details (modules, APIs, data \
model, integrations, design decisions, tests, deployment), each with status.
- contributions: 3-6 key engineering contributions or outcomes.
- bullets: EXACTLY ten candidate resume bullets. Each:
    * one line (under 30 words), starts with a strong past-tense verb \
(Built, Designed, Implemented, Engineered, Developed, Automated, Optimized, \
Integrated, Containerized, Tested...);
    * focus is one of: %s;
    * covers a DIFFERENT aspect from the others - give each a distinct focus \
where the project supports it (backend, api, database, architecture, testing, \
devops, security, performance, data, ml...). Never restate the same feature \
in different words;
    * names the real technologies involved, naturally, without stuffing;
    * describes only implemented work; uses a measurable claim only if listed;
    * stands on its own (no "also", no reference to other bullets).
- keywords: 10-25 searchable skills/concepts genuinely demonstrated.
- gaps: what could NOT be determined from the evidence (e.g. "no tests \
found", "README describes features not visible in the code read", \
"authorship unclear"). Empty only if nothing is uncertain.
- ownership_note: what is known about who wrote it (solo, team, fork, \
downloaded archive). Say "unknown" if the evidence does not show it.
- complexity_note: one honest sentence on how advanced this genuinely is.

implementation status is one of: implemented, partial, planned.

Return only the JSON object.""" % ", ".join(FOCUS)


# ------------------------------------------------------------------- brief

def build_brief(ev: dict, row: dict | None = None,
                readme_chars: int = 4000, code_budget: int = 7000) -> str:
    """Evidence bundle for one repository.

    Larger than the old Project Bank brief, because this call has to explain
    the implementation, not just summarise it. Still bounded so one call stays
    inside the free tier's tokens-per-minute ceiling.
    """
    row = row or {}
    deps = parse_dependencies(ev.get("files") or [])
    lines = ["REPOSITORY: %s" % ev.get("repo", ""),
             "FILES IN REPO: %d, %.1f MB" % (ev.get("file_count", 0),
                                             (ev.get("total_bytes") or 0) / 1e6)]
    langs = ev.get("languages") or {}
    if langs:
        lines.append("SOURCE BY EXTENSION: %s" % ", ".join(
            "%s %dKB" % (k, v // 1024) for k, v in list(langs.items())[:6]))
    lines.append("LAYOUT: %s" % "; ".join((ev.get("dir_summary") or [])[:12]))
    lines.append("HAS TESTS: %s | DOCKER: %s | CI: %s"
                 % (ev.get("has_tests"), ev.get("has_docker"), ev.get("has_ci")))

    prov = []
    if row.get("is_fork"):
        prov.append("this repository is a FORK")
    sig = row.get("signals") or []
    if "archive-suffix" in sig:
        prov.append("name ends in -master/-develop, which is how downloaded "
                    "zips unpack - possibly not the candidate's own code")
    foreign = foreign_module_owners(ev)
    if foreign:
        prov.append("go.mod module path belongs to another GitHub user (%s) - "
                    "the code may originate from their project"
                    % ", ".join(foreign))
    if row.get("description"):
        prov.append("GitHub description: %s" % row["description"][:200])
    if prov:
        lines.append("PROVENANCE: %s" % "; ".join(prov))

    files = ev.get("files") or []
    lines += ["", "FILES READ (use these exact paths as evidence):"]
    lines += ["  %s (%s)" % (f.get("path"), f.get("kind")) for f in files[:30]]

    if deps:
        lines += ["", "DECLARED DEPENDENCIES (parsed from manifests - facts):"]
        for path, names in deps.items():
            lines.append("  %s: %s" % (path, ", ".join(names[:25])))

    nums = ev.get("numbers_found") or []
    lines.append("")
    if nums:
        lines.append("MEASURABLE CLAIMS (the ONLY figures you may use):")
        for n in nums[:12]:
            lines.append("  %s %s  (from %s: %s)" % (
                n["value"], n["unit"], n["source"], n["evidence"][:110]))
    else:
        lines.append("MEASURABLE CLAIMS: none - write no metrics.")

    readme = (ev.get("readme") or "").strip()
    if readme:
        lines += ["", "README (excerpt):", readme[:readme_chars]]

    # Infra first (it states deployment as fact), then entry points and the
    # largest source files, then tests.
    order = {"infra": 0, "entry": 1, "source": 2, "test": 3, "doc": 4}
    code = sorted((f for f in files if f.get("kind") in order
                   and not f.get("path", "").lower().endswith("readme.md")),
                  key=lambda f: order[f["kind"]])
    if code:
        lines += ["", "FILE CONTENTS (excerpts):"]
        budget = code_budget
        for f in code:
            if budget <= 200:
                break
            cap = 600 if f["kind"] == "infra" else 1400
            body = (f.get("content") or "")[:min(cap, budget)]
            lines.append("--- %s (%s) ---" % (f["path"], f["kind"]))
            lines.append(body)
            budget -= len(body)
    return "\n".join(lines)


_GO_MODULE = re.compile(r"^module\s+github\.com/([\w.\-]+)/", re.M)


def foreign_module_owners(ev: dict) -> list[str]:
    """Owners named in go.mod module paths that are not the repo owner.

    A module path is written once, when the project is created, so a foreign
    owner there is strong evidence the code started as someone else's.
    """
    owner = (ev.get("repo") or "").split("/")[0].lower()
    found = set()
    for f in ev.get("files") or []:
        if (f.get("path") or "").endswith("go.mod"):
            for m in _GO_MODULE.finditer(f.get("content") or ""):
                if m.group(1).lower() != owner:
                    found.add(m.group(1))
    return sorted(found)


# -------------------------------------------------------------- verification

def _corpus(ev: dict) -> str:
    parts = [ev.get("readme") or "", " ".join(ev.get("dir_summary") or [])]
    for f in ev.get("files") or []:
        parts.append(f.get("path") or "")
        parts.append(f.get("content") or "")
    for names in parse_dependencies(ev.get("files") or []).values():
        parts.append(" ".join(names))
    return "\n".join(parts)


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", s.lower()).strip()


class Evidence:
    """What the repository demonstrably contains, for mechanical checks."""

    def __init__(self, ev: dict, vocab: Vocabulary):
        self.vocab = vocab
        raw = _corpus(ev)
        self.flat = " %s " % _flat(raw)
        self.keys = set(vocab.extract(raw))
        self.paths = {f.get("path", "") for f in ev.get("files") or []}
        self.paths_lower = {p.lower() for p in self.paths}
        self.numbers = {n["value"].replace(",", "")
                        for n in ev.get("numbers_found") or []}
        self.number_meta = {n["value"].replace(",", ""): n
                            for n in ev.get("numbers_found") or []}
        self.has_readme = bool(ev.get("readme"))

    def has_tech(self, name: str) -> bool:
        f = _flat(name)
        if not f:
            return False
        if " %s " % f in self.flat:
            return True
        keys = set(self.vocab.extract(name))
        return bool(keys) and keys <= self.keys

    def unknown_terms(self, text: str) -> list[str]:
        return sorted(self.vocab.canonical(k) for k in self.vocab.extract(text)
                      if k not in self.keys)

    def clean_paths(self, paths: list[str]) -> list[str]:
        out = []
        for p in paths or []:
            p = (p or "").strip()
            if p in self.paths:
                out.append(p)
            elif p.lower() in ("readme.md", "readme") and self.has_readme:
                out.append("README.md")
            elif p.lower() in self.paths_lower:
                out.append(next(x for x in self.paths if x.lower() == p.lower()))
        return list(dict.fromkeys(out))


# Artefact claims: things a bullet says EXIST in the repository (a Dockerfile,
# a test suite, a CI pipeline...). The term check above only asks whether the
# word appears anywhere, so "Containerized each service with dedicated
# Dockerfiles" passed for a repo whose docker-compose merely names images -
# there was no Dockerfile at all. These rules ask for the artefact itself.
_TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]+$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$", re.I)

_ARTEFACTS = [
    (re.compile(r"\bdockerfiles?\b", re.I), "a Dockerfile",
     lambda ev, paths, flat: any(p.rsplit("/", 1)[-1].startswith("dockerfile") for p in paths)),
    # Packaging claims only. A bare "Docker" can describe what the program
    # does ("runs external Docker commands"), which the term check covers.
    (re.compile(r"\b(containeri[sz]ed|containeri[sz]ation|dockeri[sz]ed|docker images?|docker[- ]compose)\b", re.I),
     "Docker files",
     lambda ev, paths, flat: bool(ev.get("has_docker"))),
    (re.compile(r"\b(unit|integration|end[- ]to[- ]end|e2e)[- ]tests?\b|\btest suites?\b|\bpytest\b|\bjunit\b|\bgoogletest\b|\btesting library\b", re.I),
     "test files",
     lambda ev, paths, flat: bool(ev.get("has_tests")) or any(_TEST_PATH.search(p) for p in paths)),
    (re.compile(r"\bci/cd\b|\bci\b|\bgithub actions\b|\bjenkins\b|\bcontinuous integration\b", re.I), "a CI configuration",
     lambda ev, paths, flat: bool(ev.get("has_ci"))),
    (re.compile(r"\b(kubernetes|k8s|helm)\b", re.I), "Kubernetes manifests",
     lambda ev, paths, flat: bool(re.search(r"\b(kubernetes|k8s|helm|kind:\s*deployment)\b", flat))),
    (re.compile(r"\bmakefiles?\b", re.I), "a Makefile",
     lambda ev, paths, flat: any(p.rsplit("/", 1)[-1] == "makefile" for p in paths)),
]
_CLOUD = re.compile(r"\b(?:deployed|hosted|running)\s+(?:\w+\s+){0,3}?(?:on|to)\s+(aws|gcp|google cloud|azure|heroku|vercel|netlify|render)\b", re.I)


def artefact_problems(text: str, ev: dict) -> list[str]:
    """Artefacts a bullet claims that the repository does not contain."""
    paths = [(f.get("path") or "").lower() for f in ev.get("files") or []]
    paths += [d.lower() for d in ev.get("dir_summary") or []]
    flat = _corpus(ev).lower()
    out = []
    for pattern, what, present in _ARTEFACTS:
        if pattern.search(text) and not present(ev, paths, flat):
            out.append("claims %s, but the repository has none" % what)
    for m in _CLOUD.finditer(text):
        if m.group(1).lower() not in flat:
            out.append("claims deployment to %s, which the repository never mentions" % m.group(1))
    return out


# A standalone number: not part of an identifier such as IPv6, S3, OAuth2,
# Log4j or HTTP/2. Those are names, not claims.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_/.\-])\d[\d,]*(?:\.\d+)?")


def strip_untraced_numbers(text: str, evidence: Evidence) -> tuple[str, list[str], list[dict]]:
    """Remove every standalone number that is not a harvested claim."""
    stripped, used = [], []
    out = text
    for m in reversed(list(_NUMBER.finditer(text))):
        raw = m.group(0).rstrip(".").replace(",", "")
        if raw in evidence.numbers:
            used.append(evidence.number_meta[raw])
            continue
        stripped.append(m.group(0))
        tail = re.match(r"\s*\+?\s*[A-Za-z%]*", out[m.end():])
        out = out[:m.start()] + out[m.end() + (tail.end() if tail else 0):]
    if stripped:
        out = re.sub(r"\b(of|by|to|at|over|under|around|about|up to)\s+"
                     r"(?=(per|each|\.|,|$))", "", out, flags=re.I)
        out = re.sub(r"\s{2,}", " ", out)
        out = re.sub(r"\s+([,.;])", r"\1", out).strip()
    return out, stripped, used


_WORDS = re.compile(r"[a-z][a-z0-9+#]+")
_STOP = {"the", "and", "for", "with", "using", "into", "from", "that", "this",
         "built", "developed", "implemented", "designed", "engineered",
         "created", "via", "across", "its", "their", "a", "an", "to", "of",
         "in", "on", "by"}


def _sig(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if w not in _STOP}


def _similar(a: str, b: str) -> float:
    x, y = _sig(a), _sig(b)
    return len(x & y) / len(x | y) if x and y else 0.0


# ------------------------------------------------------------------- record

@dataclass
class BuildResult:
    record: dict = field(default_factory=dict)
    ok: bool = False
    warnings: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


def insufficient_record(row: dict, ev: dict | None, reason: str) -> dict:
    """A record that says why nothing could be extracted, instead of guessing."""
    return {
        "kb_version": KB_VERSION,
        "status": "insufficient_evidence",
        "name": row.get("repo_name", ""),
        "repo": row.get("id", ""),
        "github_url": row.get("github_url", ""),
        "summary": "", "description": "", "problem": "",
        "architecture_overview": "", "components": [],
        "tech_stack": {c: [] for c in TECH_CATEGORIES},
        "implementation": [], "contributions": [], "bullets": [],
        "keywords": [], "vocab_keys": [],
        "gaps": [reason] + list((ev or {}).get("warnings") or []),
        "ownership_note": "unknown", "complexity_note": "",
        "evidence_refs": [], "metrics": [], "warnings": [reason],
        "source_head_sha": (ev or {}).get("head_sha", ""),
        "source_pushed_at": row.get("pushed_at", ""),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def build_record(ev: dict, row: dict, llm: LLM, vocab: Vocabulary,
                 effort: str = "medium") -> BuildResult:
    out = BuildResult()
    if not (ev.get("readme") or ev.get("files")):
        out.record = insufficient_record(row, ev, "no readable evidence")
        out.warnings.append("no readable evidence")
        return out

    # Groq counts prompt + max_tokens against the per-minute ceiling for a
    # single request (HTTP 413 when it exceeds it), so a large repository is
    # retried with a smaller evidence bundle and a smaller output allowance.
    attempts = [(4000, 7000, 8192), (2500, 4000, 6000)]
    res = None
    for readme_chars, code_budget, max_tokens in attempts:
        brief = build_brief(ev, row, readme_chars=readme_chars,
                            code_budget=code_budget)
        try:
            res = llm.complete(_SYSTEM, brief, json_schema=_SCHEMA,
                               effort=effort, max_tokens=max_tokens)
            break
        except LLMError as e:
            if "413" in str(e) or "too large" in str(e).lower():
                out.warnings.append("request too large - retried with a "
                                    "smaller evidence bundle")
                time.sleep(65)        # let the per-minute window clear
                continue
            out.warnings.append("llm error: %s" % e)
            return out
    if res is None:
        out.warnings.append("llm error: request too large even when reduced")
        return out
    out.tokens_in, out.tokens_out = res.prompt_tokens, res.completion_tokens
    try:
        data = json.loads(res.text)
    except json.JSONDecodeError as e:
        out.warnings.append("model returned non-JSON: %s" % e)
        return out

    E = Evidence(ev, vocab)
    support = _support_flags(ev)
    warnings: list[str] = []
    softened: set[str] = set()
    stripped_all: list[str] = []
    metrics: list[dict] = []

    def clean(text: str) -> str:
        t = normalise_text(text or "")
        t, stripped, used = strip_untraced_numbers(t, E)
        stripped_all.extend(stripped)
        metrics.extend(u for u in used if u not in metrics)
        t, removed = soften_claims(t, support)
        softened.update(removed)
        return t

    # 1. Tech stack: keep only what the repository contains.
    stack: dict[str, list[str]] = {}
    unverified: list[str] = []
    seen: set[str] = set()
    for cat in TECH_CATEGORIES:
        stack[cat] = []
        for t in (data.get("tech_stack") or {}).get(cat) or []:
            t = normalise_text(t)
            if not t or t.lower() in seen:
                continue
            if E.has_tech(t):
                stack[cat].append(t)
                seen.add(t.lower())
            else:
                unverified.append(t)
    if unverified:
        warnings.append("dropped %d technology claim(s) not found in the "
                        "repository: %s" % (len(unverified),
                                            ", ".join(unverified[:8])))
    verified_lower = {t.lower() for ts in stack.values() for t in ts}

    # 2. Bullets: verify, then de-duplicate.
    bullets: list[dict] = []
    rejected: list[dict] = []
    for b in data.get("bullets") or []:
        text = clean(b.get("text") or "")
        if len(text) < 25:
            rejected.append({"text": b.get("text", ""), "why": "too short"})
            continue
        unknown = E.unknown_terms(text)
        if unknown:
            rejected.append({"text": text, "why": "names technology not in "
                             "the repository: %s" % ", ".join(unknown)})
            continue
        missing = artefact_problems(text, ev)
        if missing:
            rejected.append({"text": text, "why": "; ".join(missing)})
            continue
        dup = next((k for k in bullets if _similar(k["text"], text) >= 0.6), None)
        if dup:
            rejected.append({"text": text, "why": "near-duplicate of %s" % dup["id"]})
            continue
        focus = b.get("focus") if b.get("focus") in FOCUS else "other"
        bullets.append({
            "id": "b%d" % (len(bullets) + 1),
            "text": text,
            "focus": focus,
            "technologies": [t for t in (normalise_text(x) for x in
                                         b.get("technologies") or [])
                             if t.lower() in verified_lower],
            "evidence": E.clean_paths(b.get("evidence") or []),
            "vocab_keys": sorted(vocab.extract(text)),
        })
    if rejected:
        warnings.append("rejected %d of %d generated bullets (see "
                        "rejected_bullets)" % (len(rejected),
                                               len(data.get("bullets") or [])))
    if len(bullets) < BULLETS_WANTED:
        warnings.append("%d verified bullets (wanted %d)"
                        % (len(bullets), BULLETS_WANTED))

    implementation = []
    for it in data.get("implementation") or []:
        detail = clean(it.get("detail") or "")
        if detail:
            implementation.append({
                "detail": detail,
                "status": it.get("status") if it.get("status") in
                ("implemented", "partial", "planned") else "partial",
                "evidence": E.clean_paths(it.get("evidence") or []),
            })

    contributions = [{"text": clean(c.get("text") or ""),
                      "evidence": E.clean_paths(c.get("evidence") or [])}
                     for c in data.get("contributions") or [] if c.get("text")]
    components = [{"name": normalise_text(c.get("name") or ""),
                   "responsibility": clean(c.get("responsibility") or ""),
                   "paths": E.clean_paths(c.get("paths") or [])}
                  for c in data.get("components") or [] if c.get("name")]

    gaps = [normalise_text(g) for g in data.get("gaps") or [] if g.strip()]
    if not ev.get("has_tests") and not any("test" in g.lower() for g in gaps):
        gaps.append("no test files found in the repository")
    if (row.get("is_fork") or "archive-suffix" in (row.get("signals") or [])
            or foreign_module_owners(ev)):
        gaps.append("authorship uncertain - confirm this is your own work "
                    "before approving")
    gaps = list(dict.fromkeys(gaps))

    if stripped_all:
        warnings.append("removed untraceable number(s): %s"
                        % ", ".join(sorted(set(stripped_all))[:8]))
    if softened:
        warnings.append("softened overstated phrasing: %s"
                        % ", ".join(sorted(softened)[:6]))

    rec = {
        "kb_version": KB_VERSION,
        "status": "ok" if bullets else "no_verified_bullets",
        "name": normalise_text(data.get("name") or row.get("repo_name", "")),
        "repo": row.get("id", ev.get("repo", "")),
        "github_url": row.get("github_url") or ev.get("github_url", ""),
        "summary": clean(data.get("summary") or ""),
        "description": clean(data.get("description") or ""),
        "problem": clean(data.get("problem") or ""),
        "architecture_overview": clean(data.get("architecture_overview") or ""),
        "components": components,
        "tech_stack": stack,
        "implementation": implementation,
        "contributions": contributions,
        "bullets": bullets,
        "rejected_bullets": rejected,
        "keywords": [normalise_text(k) for k in data.get("keywords") or []][:25],
        "gaps": gaps,
        "ownership_note": normalise_text(data.get("ownership_note") or "unknown"),
        "complexity_note": clean(data.get("complexity_note") or ""),
        "metrics": metrics,
        "evidence_refs": ([{"kind": "readme", "path": "README.md"}]
                          if ev.get("readme") else []) +
                         [{"kind": f.get("kind"), "path": f.get("path")}
                          for f in ev.get("files") or []],
        "warnings": warnings,
        "softened": sorted(softened),
        "stripped_numbers": sorted(set(stripped_all)),
        "unverified_tech": unverified,
        "source_head_sha": ev.get("head_sha", ""),
        "source_pushed_at": row.get("pushed_at", ""),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": res.model,
    }
    rec["vocab_keys"] = sorted(set(vocab.extract(context_text(rec))) & E.keys)
    out.record = rec
    out.ok = bool(bullets)
    out.warnings = warnings
    return out


# ------------------------------------------------------------- consumers

def all_tech(rec: dict) -> list[str]:
    return [t for c in TECH_CATEGORIES for t in (rec.get("tech_stack") or {}).get(c, [])]


def context_text(rec: dict) -> str:
    """Everything the record says, as one string - for matching and for the
    whitelist that verifies tailored output."""
    parts = [rec.get("name", ""), rec.get("summary", ""),
             rec.get("description", ""), rec.get("problem", ""),
             rec.get("architecture_overview", ""), *all_tech(rec),
             *rec.get("keywords", [])]
    parts += [c.get("name", "") + " " + c.get("responsibility", "")
              for c in rec.get("components", [])]
    parts += [i["detail"] for i in rec.get("implementation", [])
              if i.get("status") == "implemented"]
    parts += [c.get("text", "") for c in rec.get("contributions", [])]
    parts += [b["text"] for b in rec.get("bullets", [])]
    return "\n".join(p for p in parts if p)


def is_kb_record(rec: dict | None) -> bool:
    return bool(rec) and rec.get("kb_version") == KB_VERSION


def authorship_flagged(rec: dict | None) -> bool:
    """True when the build found signs the code may not be the candidate's
    own: a fork, a downloaded archive, or a go.mod owned by someone else."""
    return any("authorship uncertain" in (g or "").lower()
               for g in (rec or {}).get("gaps", []))


def usage(row: dict, bank_cfg: dict) -> tuple[bool, str]:
    """Whether a project may go on a resume, and why (shown in the UI).

    Default policy (project_bank in config.yaml): every analysed project is
    usable unless you said "don't use", it has no verified content, or its
    authorship is uncertain -- those need you to confirm them first.
    """
    rec = row.get("project_json") or {}
    status = row.get("review_status") or "needs_review"
    if not is_kb_record(rec):
        return False, "not analysed yet"
    if rec.get("status") != "ok" or not rec.get("bullets"):
        return False, "no readable content in the repository"
    if status == "rejected":
        return False, "you marked it don't use"
    if row.get("classification") == "exclude":
        return False, "excluded from the library"
    if status == "approved":
        return True, "confirmed by you"
    if bank_cfg.get("require_approval", False):
        return False, "waiting for your approval"
    if bank_cfg.get("exclude_flagged", True) and authorship_flagged(rec):
        return False, "authorship unclear - confirm it is yours to use it"
    return True, "in use"
