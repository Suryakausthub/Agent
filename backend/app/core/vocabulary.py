"""Skills vocabulary: normalisation, alias resolution, and term extraction.

Every skill comparison in the system goes through here, so that JD text and
resume text are reduced to the same canonical keys. This is the shared
dependency behind ATS keyword matching (spec section 5), match scoring
(section 3) and the anti-hallucination whitelist (section 15.2) -- all three
must agree on what counts as "the same skill", or the score drifts.

Matching is deterministic and case-insensitive, longest-alias-first, on
word boundaries. No model call is involved anywhere in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

# LaTeX artefacts that survive parsing and would otherwise break matching.
_LATEX_FIXES = [
    (re.compile(r"\\&"), "&"),
    (re.compile(r"\\%"), "%"),
    (re.compile(r"\\\$"), "$"),
    (re.compile(r"\\#"), "#"),
    (re.compile(r"\\_"), "_"),
    (re.compile(r"\$\\sim\$"), "~"),
    (re.compile(r"\\(textbf|textit|small|scshape|href|item|resumeItem)\b"), " "),
    (re.compile(r"\\[a-zA-Z]+"), " "),
    (re.compile(r"[{}]"), " "),
    (re.compile(r"--+"), "-"),
]


def normalise(text: str) -> str:
    """Lowercase, de-LaTeX, collapse whitespace. Safe for matching only."""
    for pat, rep in _LATEX_FIXES:
        text = pat.sub(rep, text)
    text = text.replace("\u2605", " ").replace("\u2014", " ").replace("\u2013", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


@dataclass
class Term:
    key: str
    canonical: str
    category: str
    aliases: list[str] = field(default_factory=list)
    source: str = "seed"        # seed | resume | manual


class Vocabulary:
    def __init__(self, terms: dict[str, Term], implies: dict[str, list[tuple[str, float]]]):
        self.terms = terms
        self.implies = implies
        self._index: list[tuple[str, str]] = []
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Alias -> key, sorted longest-first so multi-word terms win."""
        pairs: list[tuple[str, str]] = []
        for key, t in self.terms.items():
            seen = set()
            for alias in [t.canonical, *t.aliases]:
                a = normalise(alias)
                if a and a not in seen:
                    seen.add(a)
                    pairs.append((a, key))
        pairs.sort(key=lambda p: (-len(p[0]), p[0]))
        self._index = pairs

    # ---------------------------------------------------------------- lookup

    def resolve(self, text: str) -> str | None:
        """Exact alias lookup for a single term."""
        n = normalise(text)
        for alias, key in self._index:
            if alias == n:
                return key
        return None

    def extract(self, text: str) -> dict[str, list[str]]:
        """Find every known term in free text.

        Returns {key: [surface forms found]}. Longest aliases are matched first
        and their spans consumed, so "spring boot" does not also register a
        bare "spring" hit at the same position.
        """
        n = normalise(text)
        claimed = [False] * len(n)
        found: dict[str, list[str]] = {}

        for alias, key in self._index:
            for m in re.finditer(r"(?<![a-z0-9+#.])%s(?![a-z0-9+#])" % re.escape(alias), n):
                if any(claimed[m.start():m.end()]):
                    continue
                for i in range(m.start(), m.end()):
                    claimed[i] = True
                found.setdefault(key, []).append(alias)
        return found

    def expand(self, keys: set[str]) -> dict[str, float]:
        """Apply implication rules.

        Direct possession scores 1.0. An implied term scores the implication
        strength, unless it is also held directly.
        """
        scored: dict[str, float] = {k: 1.0 for k in keys}
        for k in keys:
            for implied, strength in self.implies.get(k, []):
                if scored.get(implied, 0.0) < strength:
                    scored[implied] = strength
        return scored

    def canonical(self, key: str) -> str:
        t = self.terms.get(key)
        return t.canonical if t else key

    def category(self, key: str) -> str:
        t = self.terms.get(key)
        return t.category if t else "unknown"

    # ------------------------------------------------------------ persistence

    @classmethod
    def load(cls, path: str) -> "Vocabulary":
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        terms = {
            k: Term(key=k, canonical=v["canonical"], category=v.get("category", "unknown"),
                    aliases=v.get("aliases", []), source=v.get("source", "seed"))
            for k, v in (data.get("terms") or {}).items()
        }
        implies = {
            k: [(i["term"], float(i["strength"])) for i in v]
            for k, v in (data.get("implies") or {}).items()
        }
        return cls(terms, implies)

    def save(self, path: str) -> None:
        data = {
            "version": 1,
            "_note": ("Auto-generated starter, safe to hand-edit. "
                      "Aliases must be lowercase. Longest alias wins."),
            "terms": {
                k: {"canonical": t.canonical, "category": t.category,
                    "aliases": t.aliases, "source": t.source}
                for k, t in sorted(self.terms.items())
            },
            "implies": {
                k: [{"term": t, "strength": s} for t, s in v]
                for k, v in sorted(self.implies.items())
            },
        }
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=100)

    # -------------------------------------------------------------- bootstrap

    @classmethod
    def from_seed(cls) -> "Vocabulary":
        from .vocab_seed import IMPLIES, SEED
        terms = {
            k: Term(key=k, canonical=c, category=cat, aliases=al, source="seed")
            for k, (c, cat, al) in SEED.items()
        }
        return cls(terms, dict(IMPLIES))

    def add_from_resume(self, resume) -> list[str]:
        """Register skill-line entries the seed does not already cover.

        Only the Technical Skills section is harvested. Bullets are prose and
        would inject noise; the skills list is an explicit, curated claim.
        """
        added: list[str] = []
        aliased: list[tuple[str, str]] = []
        dirty = False

        for line in resume.skills:
            for item in line.items:
                clean = normalise(item)
                if not clean or self.resolve(clean):
                    continue

                # "Agile Development" already contains the known term "agile".
                # Register it as an alias of that term rather than minting a
                # near-duplicate key, which would split the score across two.
                hits = self.extract(clean)
                if len(hits) == 1:
                    key = next(iter(hits))
                    if clean not in self.terms[key].aliases:
                        self.terms[key].aliases.append(clean)
                        aliased.append((clean, key))
                        dirty = True
                    continue

                key = re.sub(r"[^a-z0-9]+", "_", clean).strip("_")
                if not key or key in self.terms:
                    continue
                self.terms[key] = Term(
                    key=key,
                    canonical=re.sub(r"\s+", " ", item).strip(),
                    category=_guess_category(line.category),
                    aliases=[clean],
                    source="resume",
                )
                added.append(key)
                dirty = True

        if dirty:
            self._rebuild_index()
        self.last_aliased = aliased
        return added


def _guess_category(skill_line_category: str) -> str:
    c = skill_line_category.lower()
    if "language" in c:
        return "language"
    if "database" in c:
        return "database"
    if "system" in c or "distributed" in c:
        return "concept"
    if "backend" in c or "framework" in c:
        return "framework"
    if "engineering" in c or "software" in c:
        return "practice"
    if "computer science" in c:
        return "concept"
    return "tool"
