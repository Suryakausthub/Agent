"""Deterministic repository classification: keep / exclude / review.

No model call. Every decision comes from repository metadata and cheap
signals, and every decision carries a stated reason -- nothing is silently
discarded.

Two rules govern the design:

  * EXCLUDE only on unambiguous evidence. A fork is a fork; a repository with
    zero bytes has nothing in it; "leetcode" is not a portfolio project. When
    a signal is merely suggestive, the verdict is REVIEW.

  * "Not checked" is not "absent". README presence and commit share are only
    available when a token allowed enrichment. Where those are None the
    classifier must not infer anything from their absence, or an unenriched
    run would quietly exclude real projects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .github_fetch import Repo

KEEP = "keep"
EXCLUDE = "exclude"
REVIEW = "review"

# --- name patterns -----------------------------------------------------------

# Competitive programming / DSA practice dumps.
_DSA = re.compile(
    r"^(leetcode|codeforces|codechef|hackerrank|hackerearth|atcoder|topcoder|"
    r"geeksforgeeks?|gfg|striver[-_]?sheet|takeuforward|dsa|"
    r"binary[-_]?search|competitive|cp[-_]?practice|interview[-_]?prep|"
    r"vscode[-_]?for[-_]?dsa|programs?)$",
    re.I)
_DSA_LOOSE = re.compile(
    r"\b(leetcode|codeforces|codechef|hackerrank|striver|takeuforward|"
    r"geeksforgeeks?|dsa[-_]?sheet|competitive[-_]?programming)\b", re.I)

# Course labs and assignments.
_LAB = re.compile(
    r"(^|[-_ ])(lab|labsheet|lab[-_]?sheet|assignment|practical|tutorial|"
    r"exercise|worksheet|week[-_]?\d+|sem[-_]?\d+)([-_ ]|\d|$)", re.I)

# Hacktoberfest and similar PR-farming repos.
_HACKTOBER = re.compile(r"hac?k?toberfest|hacktober", re.I)

# Downloaded archives: GitHub zipballs unpack as "<name>-master"/"-develop"/"-main".
_ARCHIVE_SUFFIX = re.compile(r"-(master|develop|main|v?\d+\.\d+(\.\d+)?)$", re.I)

# Scaffolding / throwaway.
_THROWAWAY = re.compile(
    r"^(myfirstrepo|test|tests?|demo|sample|temp|tmp|scratch|untitled|"
    r"new[-_]?repo|hello[-_]?world|index|practice|learning|notes?|"
    r"all[-_].*repositories)$", re.I)

# Words that suggest a real, named piece of work.
_PROJECT_WORDS = re.compile(
    r"(system|platform|engine|dashboard|tracker|api|service|app|bot|"
    r"simulator|analy(s|z)er|detector|shield|manager|portal|marketplace|"
    r"recommend|pipeline|scope|verse|mate|cart|connect|booking)", re.I)

# Sizes, in KB.
_EMPTY_KB = 2            # nothing meaningful can live below this
_TINY_KB = 20            # below this, needs other evidence to count
_HUGE_KB = 25_000        # above this, almost certainly vendored content

_MIN_OWNER_SHARE = 0.5   # below this the repo is likely someone else's work


@dataclass
class Verdict:
    repo: Repo
    classification: str
    reason: str
    signals: list[str] = field(default_factory=list)
    duplicate_of: str = ""

    @property
    def is_candidate(self) -> bool:
        return self.classification in (KEEP, REVIEW)


def _norm_name(name: str) -> str:
    """Canonical form for duplicate detection."""
    n = name.lower()
    n = _ARCHIVE_SUFFIX.sub("", n)
    n = re.sub(r"[^a-z0-9]+", "", n)
    return n


def _dup_key(name: str) -> str:
    """Looser key that collapses obvious near-duplicates.

    'ML_PROJECT'/'ML-PROJECT' and 'software-engineering'/'software-engineering-'
    collapse; unrelated names do not.
    """
    n = _norm_name(name)
    n = re.sub(r"(new|old|final|copy|v\d+)$", "", n)
    n = re.sub(r"s$", "", n)
    return n


def classify_one(repo: Repo, owner: str) -> Verdict:
    name = repo.name
    sig: list[str] = []

    # ---------------------------------------------------- unambiguous excludes

    if repo.fork:
        return Verdict(repo, EXCLUDE, "fork of another repository", ["fork"])

    if _HACKTOBER.search(name) or _HACKTOBER.search(repo.description or ""):
        return Verdict(repo, EXCLUDE, "Hacktoberfest participation repository",
                       ["hacktoberfest"])

    if repo.size_kb <= _EMPTY_KB:
        return Verdict(repo, EXCLUDE, "empty repository (%d KB)" % repo.size_kb,
                       ["empty"])

    if _DSA.match(name) or _DSA_LOOSE.search(name):
        return Verdict(repo, EXCLUDE,
                       "competitive programming / DSA practice", ["dsa"])

    if _LAB.search(name):
        return Verdict(repo, EXCLUDE, "course lab or assignment", ["lab"])

    if _THROWAWAY.match(name):
        return Verdict(repo, EXCLUDE, "scaffolding or throwaway repository",
                       ["throwaway"])

    # ------------------------------------------------------ vendored content

    # A zipball suffix plus real bulk is the strongest available signal that
    # the content was downloaded rather than written.
    archive_suffix = bool(_ARCHIVE_SUFFIX.search(name))
    if archive_suffix and repo.size_kb > 1_000:
        return Verdict(repo, EXCLUDE,
                       "looks like a downloaded archive (name ends in an "
                       "archive suffix, %d KB)" % repo.size_kb,
                       ["archive-suffix", "large"])

    if repo.commits_by_owner is not None and repo.commits_total:
        share = repo.commits_by_owner / repo.commits_total
        sig.append("owner-commits=%d/%d" % (repo.commits_by_owner,
                                            repo.commits_total))
        if share < _MIN_OWNER_SHARE:
            return Verdict(repo, REVIEW,
                           "most commits are not yours (%.0f%% by %s) - "
                           "confirm this is your work"
                           % (share * 100, owner),
                           sig + ["low-authorship"])

    if repo.size_kb > _HUGE_KB:
        return Verdict(repo, REVIEW,
                       "very large (%d KB) - may contain vendored or "
                       "third-party code" % repo.size_kb,
                       sig + ["huge"])

    # ------------------------------------------------------------- positives

    if repo.language:
        sig.append("lang=%s" % repo.language)
    if repo.topics:
        sig.append("topics=%d" % len(repo.topics))
    if repo.description:
        sig.append("has-description")
    if repo.has_readme:
        sig.append("readme=%dB" % (repo.readme_bytes or 0))
    if archive_suffix:
        sig.append("archive-suffix")

    named_like_project = bool(_PROJECT_WORDS.search(name)) or "-" in name or "_" in name

    # An archive suffix is never enough on its own to exclude a small repo,
    # but it must never be ignored either: "datacatalog-connectors-bi-master"
    # is a Google Cloud project that would otherwise pass as a KEEP purely
    # because it is named like one and has a language.
    if archive_suffix:
        return Verdict(repo, REVIEW,
                       "name ends in an archive suffix (-master/-develop), "
                       "which is how downloaded zips unpack - confirm this is "
                       "your own work", sig + ["archive-suffix"])

    # Enrichment unavailable: judge on metadata alone and lean to review.
    if repo.has_readme is None:
        if repo.language and repo.size_kb >= _TINY_KB and named_like_project:
            return Verdict(repo, KEEP,
                           "named like a project, %s, %d KB (README not "
                           "checked - no GitHub token)"
                           % (repo.language, repo.size_kb), sig)
        return Verdict(repo, REVIEW,
                       "insufficient signal without a GitHub token "
                       "(%s, %d KB)" % (repo.language or "no language",
                                        repo.size_kb), sig)

    # Enrichment available.
    if not repo.language and not repo.has_readme:
        return Verdict(repo, REVIEW,
                       "no detected language and no README - unclear what "
                       "this contains", sig)

    if repo.size_kb < _TINY_KB and not repo.has_readme:
        return Verdict(repo, REVIEW,
                       "very small (%d KB) with no README" % repo.size_kb, sig)

    if repo.has_readme and repo.language:
        return Verdict(repo, KEEP,
                       "README (%d B) and %s source, %d KB"
                       % (repo.readme_bytes or 0, repo.language, repo.size_kb),
                       sig)

    if repo.language and repo.size_kb >= _TINY_KB:
        return Verdict(repo, KEEP,
                       "%s source, %d KB, no README (evidence will come from "
                       "code)" % (repo.language, repo.size_kb), sig)

    return Verdict(repo, REVIEW, "does not match a confident rule either way",
                   sig)


def classify_all(repos: list[Repo], owner: str) -> list[Verdict]:
    """Classify, then demote duplicates to review.

    Duplicates are only detected among survivors: there is no value in
    reporting that two excluded lab sheets resemble each other.
    """
    verdicts = [classify_one(r, owner) for r in repos]

    seen: dict[str, Verdict] = {}
    for v in verdicts:
        if not v.is_candidate:
            continue
        key = _dup_key(v.repo.name)
        if key not in seen:
            seen[key] = v
            continue

        # Keep the richer of the two, send the other to review.
        winner, loser = seen[key], v
        if _rank(v.repo) > _rank(winner.repo):
            winner, loser = v, winner
            seen[key] = v
        loser.classification = REVIEW
        loser.duplicate_of = winner.repo.name
        loser.reason = ("near-duplicate of %s (kept the larger/more complete "
                        "one) - confirm which to use" % winner.repo.name)
        loser.signals.append("duplicate")
    return verdicts


def _rank(r: Repo) -> tuple:
    """Which of two similar repos is the better candidate."""
    return (1 if r.has_readme else 0, r.size_kb, 1 if r.language else 0,
            r.pushed_at)


def summarise(verdicts: list[Verdict]) -> dict[str, int]:
    out = {KEEP: 0, REVIEW: 0, EXCLUDE: 0}
    for v in verdicts:
        out[v.classification] = out.get(v.classification, 0) + 1
    out["total"] = len(verdicts)
    return out
