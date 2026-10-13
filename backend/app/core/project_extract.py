"""Turn harvested repository evidence into a Project Bank entry.

One model call per repository. The division of labour matches the rest of this
agent: CODE establishes facts, the model only writes prose over them.

  * Technologies come from PARSED MANIFESTS -- requirements.txt, package.json,
    go.mod, pom.xml -- not from the model reading a file. A dependency list is
    a fact; asking a model to summarise one invites invention.
  * Metrics come only from `numbers_found`, which carries the source line that
    supports each one. Any number the model emits that does not match a
    harvested claim is stripped before storage (spec: never fabricate).
  * Claim strength is capped in code. "production-grade", "at scale" and
    friends are removed unless the evidence actually supports them, so a
    student project is not described as something it is not.

Output lands with status `needs_review`. Nothing here makes a project usable
for resume generation; only human approval does that.
"""

from __future__ import annotations

import json
import time
import re
from dataclasses import dataclass, field

from .llm import LLM, HallucinatedNumber, LLMError

# --------------------------------------------------------------- manifests

_PY_DEP = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:[=<>!~\[]|$)")
_GO_DEP = re.compile(r"^\s+([\w.\-/]+)\s+v[\d.]")
_MVN_DEP = re.compile(r"<artifactId>([^<]+)</artifactId>")
_GRADLE_DEP = re.compile(r"['\"]([\w.\-]+):([\w.\-]+):")

# Dependencies that say nothing about what was built.
_NOISE = re.compile(
    r"^(setuptools|wheel|pip|six|certifi|urllib3|idna|charset|packaging|"
    r"typing[-_]extensions|python[-_]dateutil|pytz|colorama|click|tqdm|"
    r"attrs|pyyaml|toml|filelock|fsspec|zipp|importlib|markupsafe|"
    r"@types/.*|eslint.*|prettier|postcss|autoprefixer|.*-loader|"
    r"junit|hamcrest|slf4j|logback|lombok)$", re.I)


def parse_dependencies(files: list[dict]) -> dict[str, list[str]]:
    """Extract declared dependencies per manifest. Deterministic."""
    out: dict[str, list[str]] = {}
    for f in files:
        if f.get("kind") != "manifest":
            continue
        path = f.get("path", "").lower()
        body = f.get("content", "")
        deps: list[str] = []

        if path.endswith(("requirements.txt", "pipfile", "environment.yml")):
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                m = _PY_DEP.match(line)
                if m:
                    deps.append(m.group(1))
        elif path.endswith("package.json"):
            try:
                data = json.loads(body)
                for key in ("dependencies", "devDependencies"):
                    deps.extend((data.get(key) or {}).keys())
            except (ValueError, AttributeError):
                pass
        elif path.endswith("go.mod"):
            deps.extend(m.group(1).rsplit("/", 1)[-1]
                        for m in _GO_DEP.finditer(body))
        elif path.endswith("pom.xml"):
            deps.extend(_MVN_DEP.findall(body))
        elif path.endswith(("build.gradle", "build.gradle.kts")):
            deps.extend(m.group(2) for m in _GRADLE_DEP.finditer(body))
        elif path.endswith(("pyproject.toml", "setup.py")):
            for line in body.splitlines():
                m = _PY_DEP.match(line.strip().strip('",'))
                if m and len(m.group(1)) > 2:
                    deps.append(m.group(1))

        clean = []
        seen = set()
        for d in deps:
            d = d.strip()
            if not d or _NOISE.match(d) or d.lower() in seen:
                continue
            seen.add(d.lower())
            clean.append(d)
        if clean:
            out[f.get("path", "?")] = clean[:30]
    return out


# ------------------------------------------------------------ claim ceiling

# Phrases that overstate a student project unless the repository proves them.
_INFLATED = [
    (re.compile(r"\bproduction[- ]?(grade|ready)\b", re.I), "has_deploy"),
    (re.compile(r"\benterprise[- ]?(grade|ready|scale)?\b", re.I), None),
    (re.compile(r"\bhighly[- ]scalable\b", re.I), None),
    (re.compile(r"\bat scale\b", re.I), None),
    (re.compile(r"\bfault[- ]tolerant\b", re.I), "has_tests"),
    (re.compile(r"\bhigh[- ]availability\b", re.I), None),
    (re.compile(r"\bmillions? of (users|requests|records)\b", re.I), "has_big_metric"),
    (re.compile(r"\bindustry[- ]standard\b", re.I), None),
    (re.compile(r"\bmission[- ]critical\b", re.I), None),
    (re.compile(r"\bbattle[- ]tested\b", re.I), None),
    (re.compile(r"\breal[- ]world (deployment|traffic)\b", re.I), None),
    (re.compile(r"\bseamless(ly)?\b", re.I), None),
    (re.compile(r"\bcutting[- ]edge\b", re.I), None),
    (re.compile(r"\bstate[- ]of[- ]the[- ]art\b", re.I), None),
]

_SOFTENED = {
    "production-grade": "working",
    "production ready": "working",
    "enterprise-grade": "",
    "highly scalable": "",
    "at scale": "",
    "fault-tolerant": "",
    "high availability": "",
    "industry-standard": "",
    "mission-critical": "",
    "battle-tested": "",
    "seamlessly": "",
    "seamless": "",
    "cutting-edge": "",
    "state-of-the-art": "",
}


# The model emits typographic Unicode -- it wrote "Python‑based" and
# "cross‑modal" with non-breaking hyphens. Those reach the resume renderer
# and break the LaTeX build, exactly as they did in bullet rewriting. Normalise
# at this boundary so nothing downstream has to.
_TYPOGRAPHIC = {
    "‑": "-", "‐": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "…": "...", "•": "-", "−": "-",
    "﻿": "", "​": "",
}


def normalise_text(text: str) -> str:
    for bad, good in _TYPOGRAPHIC.items():
        text = text.replace(bad, good)
    return re.sub(r"\s{2,}", " ", text).strip()


def soften_claims(text: str, support: dict[str, bool]) -> tuple[str, list[str]]:
    """Remove overstatement the repository does not support.

    Returns (text, removed_phrases). A claim is kept only when its supporting
    condition is present -- "fault-tolerant" survives if the repo has tests,
    "production-grade" if it has deployment config.
    """
    removed: list[str] = []
    out = text
    for pattern, requires in _INFLATED:
        if requires and support.get(requires):
            continue
        for m in list(pattern.finditer(out)):
            phrase = m.group(0)
            removed.append(phrase)
            replacement = _SOFTENED.get(phrase.lower().replace(" ", "-"), "")
            if not replacement:
                replacement = _SOFTENED.get(phrase.lower(), "")
            out = out[:m.start()] + replacement + out[m.end():]
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;])", r"\1", out)
    return out.strip(), removed


# ------------------------------------------------------------------- schema

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "description", "technologies", "skills", "features",
                 "architecture", "resume_bullets", "complexity_note"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "technologies": {"type": "array", "items": {"type": "string"}},
        "skills": {"type": "array", "items": {"type": "string"}},
        "features": {"type": "array", "items": {"type": "string"}},
        "architecture": {"type": "array", "items": {"type": "string"}},
        "resume_bullets": {"type": "array", "items": {"type": "string"}},
        "complexity_note": {"type": "string"},
    },
}

_SYSTEM = """You write factual project summaries for a STUDENT's resume, from \
repository evidence only.

Absolute rules:
- Use ONLY what the evidence states. Never invent a technology, feature, \
metric, dataset size, user count or performance figure. If the evidence does \
not show it, it does not exist.
- Tokens like <N0> are opaque placeholders for numbers. Reproduce them exactly \
and never write a digit yourself.
- This is a student or personal project. Do NOT call it production-grade, \
enterprise, highly scalable, fault-tolerant, high-availability, \
industry-standard or mission-critical. Describe what it does, plainly.
- Write so it is defensible in an interview: an interviewer will ask the \
candidate to explain every phrase you use.

resume_bullets: exactly three, each one line, starting with a past-tense verb.
  1. WHAT was built - the system and its purpose.
  2. HOW it was built - the technologies and design actually present.
  3. RESULT - what it does or achieves.

Metrics: if the brief lists MEASURABLE CLAIMS, you SHOULD use one of those \
placeholders in bullet 3 - they are real figures from the repository and they \
make the bullet concrete. Reproduce the placeholder token exactly as written. \
If the brief says no measurable claims were found, write all three bullets \
with no numbers at all. Never write a digit yourself.

If the evidence says something is planned, partial or a hobby project, say so \
or leave it out - never describe a planned feature as built.

description: two or three sentences, plain language, what the project is and \
why it exists.
complexity_note: one sentence on how advanced this genuinely is, honestly.

Return only the JSON object."""


@dataclass
class ExtractedProject:
    repo: str
    name: str = ""
    github_url: str = ""
    description: str = ""
    technologies: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    architecture: list[str] = field(default_factory=list)
    resume_bullets: list[str] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    evidence_refs: list[dict] = field(default_factory=list)
    complexity_note: str = ""
    softened: list[str] = field(default_factory=list)
    stripped_numbers: list[str] = field(default_factory=list)
    needs_attention: bool = False
    warnings: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0

    def relevance_text(self) -> str:
        """Denormalised blob the existing vocabulary matcher consumes."""
        return " ".join([self.name, self.description,
                         *self.technologies, *self.skills, *self.features,
                         *self.architecture, *self.resume_bullets])


def build_brief(ev: dict) -> str:
    """Compact, factual brief. Small on purpose: the free tier allows 8,000
    tokens per minute, and a 24,000-character bundle would consume most of it."""
    deps = parse_dependencies(ev.get("files") or [])
    lines: list[str] = [
        "REPOSITORY: %s" % ev.get("repo", ""),
        "FILES: %d, %.1f MB" % (ev.get("file_count", 0),
                                (ev.get("total_bytes") or 0) / 1e6),
    ]
    langs = ev.get("languages") or {}
    if langs:
        lines.append("SOURCE BY EXTENSION: %s"
                     % ", ".join("%s %dKB" % (k, v // 1024)
                                 for k, v in list(langs.items())[:6]))
    lines.append("LAYOUT: %s" % "; ".join((ev.get("dir_summary") or [])[:8]))
    lines.append("HAS TESTS: %s | DOCKER: %s | CI: %s"
                 % (ev.get("has_tests"), ev.get("has_docker"), ev.get("has_ci")))

    if deps:
        lines.append("")
        lines.append("DECLARED DEPENDENCIES (parsed from manifests - these are "
                     "facts, use them for technologies):")
        for path, names in deps.items():
            lines.append("  %s: %s" % (path, ", ".join(names[:22])))

    nums = ev.get("numbers_found") or []
    lines.append("")
    if nums:
        lines.append("MEASURABLE CLAIMS FOUND IN THE REPO (the ONLY numbers you "
                     "may refer to):")
        for n in nums[:12]:
            lines.append("  %s %s  (from %s: %s)"
                         % (n["value"], n["unit"], n["source"],
                            n["evidence"][:110]))
    else:
        lines.append("MEASURABLE CLAIMS FOUND IN THE REPO: none. "
                     "Write all three bullets WITHOUT any numbers.")

    readme = (ev.get("readme") or "").strip()
    if readme:
        lines += ["", "README (excerpt):", readme[:5000]]

    code_files = [f for f in (ev.get("files") or [])
                  if f.get("kind") in ("entry", "source", "doc")
                  and not f.get("path", "").lower().startswith("readme")]
    if code_files:
        lines += ["", "SELECTED FILES:"]
        budget = 4500
        for f in code_files[:5]:
            body = (f.get("content") or "")[:1200]
            if budget <= 0:
                break
            lines.append("--- %s (%s) ---" % (f["path"], f["kind"]))
            lines.append(body)
            budget -= len(body)

    return "\n".join(lines)


def _support_flags(ev: dict) -> dict[str, bool]:
    nums = ev.get("numbers_found") or []
    big = any(n["unit"] in ("million", "billion") or
              (n["value"].replace(",", "").isdigit() and
               int(n["value"].replace(",", "")) >= 100_000)
              for n in nums)
    return {
        "has_tests": bool(ev.get("has_tests")),
        "has_deploy": bool(ev.get("has_docker") or ev.get("has_ci")),
        "has_big_metric": big,
    }


_DIGIT = re.compile(r"\d[\d,.]*")


def _verify_metrics(bullets: list[str], ev: dict) -> tuple[
        list[str], list[dict], list[str], list[str]]:
    """Keep only numbers that trace to a harvested claim.

    Anything else is removed from the bullet rather than merely flagged: a
    metric with no provenance must not reach a resume at all.
    """
    allowed = {n["value"].replace(",", ""): n for n in (ev.get("numbers_found") or [])}
    used: list[dict] = []
    stripped: list[str] = []
    broken: list[str] = []
    out: list[str] = []

    for b in bullets:
        text = b
        for m in list(_DIGIT.finditer(b)):
            raw = m.group(0).rstrip(".").replace(",", "")
            if raw in allowed:
                n = allowed[raw]
                if n not in used:
                    used.append(n)
                continue
            stripped.append(m.group(0))
            # Remove the number and any unit word immediately after it.
            text = re.sub(
                r"\b%s\s*\+?\s*[A-Za-z%%/]*\s*" % re.escape(m.group(0)),
                "", text, count=1)
        text = re.sub(r"\s{2,}", " ", text).strip()
        text = re.sub(r"\s+([,.;])", r"\1", text)

        # Removing a number mid-sentence leaves a hole: "average latency of
        # per order" is what survived one strip. Repair the common shapes, and
        # if the sentence is still broken, say so rather than shipping it.
        if b != text:
            text = re.sub(
                r"\b(of|by|to|at|over|under|around|about|reaching|up to)\s+"
                r"(?=(per|each|\.|,|$))", "", text, flags=re.I)
            text = re.sub(r"\s+(of|by|to|at|over|with an?)\s*\.", ".", text,
                          flags=re.I)
            text = re.sub(r"\s{2,}", " ", text).strip()
            text = re.sub(r"\s+([,.;])", r"\1", text)
            if re.search(r"\b(of|by|to|at|over|under|with an?)\s*(\.|,|$)",
                         text, re.I):
                broken.append(text)
        out.append(text)
    return out, used, stripped, broken


def extract(ev: dict, llm: LLM, cfg: dict | None = None,
            retry_wait: float = 35.0) -> ExtractedProject:
    repo = ev.get("repo", "")
    p = ExtractedProject(repo=repo, github_url=ev.get("github_url", ""))

    if not (ev.get("readme") or ev.get("files")):
        p.warnings.append("no usable evidence - skipped")
        return p

    brief = build_brief(ev)
    try:
        res = llm.rewrite_masked(_SYSTEM, brief, json_schema=_SCHEMA,
                                 effort="medium")
    except HallucinatedNumber as first:
        # The model wrote a literal digit instead of carrying a placeholder
        # through. Rather than lose the project entirely, retry once with
        # numbers forbidden outright: a bullet with no metric is a perfectly
        # good bullet, and it is certainly better than no entry at all.
        p.warnings.append(
            "first attempt blocked by the number guard (%s); retried with "
            "numbers disallowed" % str(first)[:90])
        # Both calls are ~4,000 tokens, and the free tier allows 8,000 per
        # minute. Firing the retry immediately guarantees a 429 on the second
        # call -- which is exactly what happened before this wait existed.
        time.sleep(retry_wait)
        try:
            res = llm.rewrite_masked(
                _SYSTEM + "\n\nOVERRIDE: write NO numbers, digits or "
                          "placeholders anywhere in your response. Describe "
                          "every outcome qualitatively.",
                brief, json_schema=_SCHEMA, effort="medium")
        except (HallucinatedNumber, LLMError) as second:
            p.warnings.append("retry also failed: %s" % str(second)[:90])
            return p
    except LLMError as e:
        p.warnings.append("llm error: %s" % e)
        return p

    p.tokens_in, p.tokens_out = res.prompt_tokens, res.completion_tokens
    try:
        data = json.loads(res.text)
    except json.JSONDecodeError as e:
        p.warnings.append("model returned non-JSON: %s" % e)
        return p

    p.name = normalise_text(data.get("name") or repo.split("/")[-1])
    p.description = normalise_text(data.get("description") or "")
    p.technologies = [normalise_text(t) for t in (data.get("technologies") or []) if t][:20]
    p.skills = [normalise_text(x) for x in (data.get("skills") or []) if x][:15]
    p.features = [normalise_text(f) for f in (data.get("features") or []) if f][:8]
    p.architecture = [normalise_text(a) for a in (data.get("architecture") or []) if a][:6]
    p.complexity_note = normalise_text(data.get("complexity_note") or "")
    bullets = [normalise_text(b) for b in (data.get("resume_bullets") or []) if b.strip()]

    # 1. metrics must trace to evidence
    bullets, used, stripped, broken = _verify_metrics(bullets, ev)
    p.metrics = used
    p.stripped_numbers = stripped
    if stripped:
        p.warnings.append("removed %d untraceable number(s): %s"
                          % (len(stripped), ", ".join(sorted(set(stripped))[:6])))
    if broken:
        p.needs_attention = True
        p.warnings.append(
            "GRAMMAR: removing a number left an incomplete sentence - "
            "edit before approving: %s" % broken[0][:110])

    # 2. claim strength ceiling
    support = _support_flags(ev)
    softened_all: list[str] = []
    cleaned: list[str] = []
    for b in bullets:
        text, removed = soften_claims(b, support)
        softened_all += removed
        cleaned.append(text)
    p.description, desc_removed = soften_claims(p.description, support)
    softened_all += desc_removed
    p.softened = sorted(set(softened_all))
    if p.softened:
        p.warnings.append("softened overstated phrasing: %s"
                          % ", ".join(p.softened[:6]))

    p.resume_bullets = cleaned[:3]
    if len(p.resume_bullets) < 3:
        p.warnings.append("model returned %d bullets, expected 3"
                          % len(p.resume_bullets))

    p.evidence_refs = [{"kind": f.get("kind"), "path": f.get("path")}
                       for f in (ev.get("files") or [])][:12]
    if ev.get("readme"):
        p.evidence_refs.insert(0, {"kind": "readme", "path": "README.md"})
    return p
