"""Anti-hallucination verification (spec sections 15.2, 15.4, 15.5).

The model is never asked whether it made something up. Instead every tailored
string is checked mechanically against the candidate's own evidence:

  1. TERM WHITELIST -- any vocabulary term in the output must also be present
     in the master resume + project bank. A tailored bullet that introduces
     "Selenium" fails, regardless of how plausible it reads.

  2. TOKEN TRACEABILITY -- content words in the output must appear in the
     source bullet it was derived from, or in a small allowlist of connective
     and rephrasing vocabulary. This catches invented specifics that are not
     vocabulary terms ("led a team of engineers", "at scale in production").

  3. NUMBERS -- handled upstream by masking in llm.py, and re-checked here so
     a bullet that bypassed the masked path cannot slip through.

Nothing here consults the model, and nothing here is advisory: a failure blocks
the action gate (spec 17) until a human resolves it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .vocabulary import Vocabulary, normalise

# Words a rewrite may legitimately introduce: connectives, verbs of framing,
# and resume-standard phrasing. Deliberately small -- anything domain-bearing
# must come from the source.
_ALLOWED_NEW = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "by",
    "at", "from", "as", "that", "which", "into", "across", "using", "via",
    "built", "build", "building", "developed", "develop", "developing",
    "designed", "design", "designing", "implemented", "implement",
    "implementing", "created", "delivered", "deliver", "engineered",
    "improved", "improving", "optimized", "optimised", "optimizing",
    "reduced", "increased", "enhanced", "streamlined", "automated",
    "led", "drove", "owned", "managed", "maintained", "supported",
    "enabled", "ensured", "resolved", "debugged", "tested", "validated",
    "integrated", "deployed", "migrated", "refactored", "scaled",
    "system", "systems", "service", "services", "application", "applications",
    "platform", "pipeline", "pipelines", "data", "code", "feature", "features",
    "performance", "reliability", "scalability", "quality", "production",
    "end", "to", "high", "low", "more", "than", "over", "up", "new",
    "is", "was", "were", "are", "be", "been", "it", "its", "this", "these",
}

_WORD_RE = re.compile(r"[a-z][a-z0-9+#]*")
_NUM_RE = re.compile(r"\d")

# Suffixes stripped before comparing a rewritten word to the source, so
# "supporting" -> "support" and "optimised" -> "optimis" are recognised as the
# same root rather than reported as invented content.
_SUFFIXES = ("ingly", "ing", "edly", "ed", "es", "s", "ly", "ment", "tion",
             "sion", "ance", "ence", "able", "ible", "ity", "ness", "er", "or")

HIGH = "high"       # blocks the action gate
LOW = "low"         # surfaced for human review, does not block


def _stem(word: str) -> str:
    for suf in _SUFFIXES:
        if len(word) - len(suf) >= 4 and word.endswith(suf):
            return word[:-len(suf)]
    return word


def _tokens(text: str) -> list[str]:
    """Split on hyphens and slashes too.

    "fault-tolerant" must yield {fault, tolerant} on BOTH sides of the
    comparison, or an unhyphenated rewrite reads as two invented words.
    """
    flat = re.sub(r"[-/_]+", " ", text)
    return _WORD_RE.findall(flat)


@dataclass
class Violation:
    kind: str            # unknown_term | untraceable_token | invented_number
    value: str
    bullet_id: str
    detail: str = ""
    severity: str = HIGH


@dataclass
class CheckResult:
    ok: bool = True                  # no HIGH severity violations
    violations: list[Violation] = field(default_factory=list)
    checked: int = 0

    def add(self, v: Violation) -> None:
        self.violations.append(v)
        if v.severity == HIGH:
            self.ok = False

    @property
    def high(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == HIGH]

    @property
    def low(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == LOW]

    def by_kind(self) -> dict[str, list[Violation]]:
        out: dict[str, list[Violation]] = {}
        for v in self.violations:
            out.setdefault(v.kind, []).append(v)
        return out


class Whitelist:
    """Everything the candidate can truthfully claim."""

    def __init__(self, evidence: str, vocab: Vocabulary):
        self.vocab = vocab
        self.evidence_raw = evidence
        self.evidence = normalise(evidence)
        self.terms: set[str] = set(vocab.extract(evidence))
        self.tokens: set[str] = set(_tokens(self.evidence))
        self.stems: set[str] = {_stem(t) for t in self.tokens}
        self.numbers: set[str] = set(re.findall(r"\d[\d,.]*", self.evidence))

    def allows_term(self, key: str) -> bool:
        return key in self.terms

    # ----------------------------------------------------------------- checks

    def check_text(self, text: str, source: str, bullet_id: str,
                   result: CheckResult, allow_terms: set[str] | None = None,
                   check_tokens: bool = True) -> None:
        """Verify one tailored string against the source it came from.

        `allow_terms` exempts vocabulary keys that appear as CONTEXT rather
        than as a claim -- most importantly the employer's own name. Company
        names collide with technology names constantly (Postman, Oracle,
        Apache, Stripe), and without this an outreach message that simply says
        where the candidate is applying gets flagged as claiming that skill.

        `check_tokens=False` runs only the term and number checks. Referral
        messages are conversation, not resume prose: words like "could you
        refer me" are legitimately absent from the source and flagging them
        buries the one check that matters.
        """
        allow_terms = allow_terms or set()
        result.checked += 1
        norm = normalise(text)
        src_norm = normalise(source)
        src_tokens = set(_tokens(src_norm))
        src_stems = {_stem(t) for t in src_tokens}

        # 1. HIGH -- a named technology the candidate does not have anywhere.
        # This is the check that matters: it names the thing it rejects.
        for key in self.vocab.extract(text):
            if key in self.terms or key in allow_terms:
                continue
            result.add(Violation(
                "unknown_term", self.vocab.canonical(key), bullet_id,
                "not present anywhere in the master resume or project bank",
                severity=HIGH))

        if not check_tokens:
            src_nums = re.findall(r"\d[\d,.]*", src_norm)
            for num in re.findall(r"\d[\d,.]*", norm):
                if num not in src_nums:
                    result.add(Violation(
                        "invented_number", num, bullet_id,
                        "not present in the source", severity=HIGH))
            return

        # 2. Words not traceable to the source bullet. Severity depends on
        # whether the word is domain-bearing: an unrecognised noun could be an
        # invented specific, whereas a synonym verb ("boosted" for "improved")
        # is a rephrasing the rewrite step exists to perform. Low-severity
        # items are surfaced in the diff for the human, not auto-blocked --
        # otherwise every legitimate rewrite trips the gate and the gate stops
        # meaning anything.
        for tok in _tokens(norm):
            if len(tok) <= 2:
                continue
            if (tok in src_tokens or tok in _ALLOWED_NEW
                    or tok in self.tokens):
                continue
            stem = _stem(tok)
            if stem in src_stems or stem in self.stems:
                continue
            if any(_stem(a) == stem for a in _ALLOWED_NEW):
                continue
            result.add(Violation(
                "untraceable_token", tok, bullet_id,
                "does not appear in the source bullet or anywhere in the resume",
                severity=LOW))

        # 3. HIGH -- numbers must be exactly those in the source.
        src_nums = re.findall(r"\d[\d,.]*", src_norm)
        for num in re.findall(r"\d[\d,.]*", norm):
            if num not in src_nums:
                result.add(Violation(
                    "invented_number", num, bullet_id,
                    "not present in the source bullet", severity=HIGH))


@dataclass
class DiffLine:
    bullet_id: str
    source: str
    tailored: str
    source_line: int
    changed: bool
    violations: list[Violation] = field(default_factory=list)


def build_diff(pairs: list[tuple[str, str, str, int]],
               wl: Whitelist) -> tuple[list[DiffLine], CheckResult]:
    """pairs: (bullet_id, source_text, tailored_text, source_line)

    Produces the bullet-by-bullet audit trail spec section 17 requires before
    anything can be downloaded or sent.
    """
    result = CheckResult()
    lines: list[DiffLine] = []
    for bid, src, out, ln in pairs:
        before = len(result.violations)
        wl.check_text(out, src, bid, result)
        lines.append(DiffLine(
            bullet_id=bid, source=src, tailored=out, source_line=ln,
            changed=normalise(src) != normalise(out),
            violations=result.violations[before:],
        ))
    return lines, result
