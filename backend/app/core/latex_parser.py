"""Parse a LaTeX resume built on the resumeItem/resumeSubheading macro family.

Deliberately NOT a general LaTeX parser. It understands exactly the macro
vocabulary this template uses, and it tracks brace nesting properly so that
nested \\textbf{...} / \\href{...}{...} inside a bullet do not terminate the
argument early.

Structural note: in this template a single itemize environment opened by
\\resumeSubHeadingListStart can span BOTH the Experience and Projects sections,
with \\section{Projects} emitted inside it. So section boundaries and
environment nesting are tracked independently; entries are assigned to a
section by position, not by assuming each section opens its own list.
"""

from __future__ import annotations

import re

from .model import Bullet, Entry, ListItem, Resume, SkillLine, Span

SECTION_RE = re.compile(r"\\section\{((?:[^{}]|\{[^{}]*\})*)\}")
BEGIN_DOC_RE = re.compile(r"\\begin\{document\}")
CENTER_RE = re.compile(r"\\begin\{center\}(.*?)\\end\{center\}", re.S)


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _read_group(src: str, i: int) -> tuple[str, int]:
    """Read a balanced brace group starting at the opening brace at index i.

    Returns (inner_text, index_after_closing_brace). Honours backslash escapes
    so an escaped brace does not affect the depth count.
    """
    if i >= len(src) or src[i] != "{":
        raise ValueError("expected open brace at %d, found %r" % (i, src[i:i + 12]))
    depth = 0
    j = i
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:j], j + 1
        j += 1
    raise ValueError("unbalanced brace opened at %d" % i)


def _read_args(src: str, i: int, n: int) -> tuple[list[str], int]:
    """Read n consecutive brace groups, skipping whitespace between them."""
    args: list[str] = []
    j = i
    for _ in range(n):
        while j < len(src) and src[j].isspace():
            j += 1
        arg, j = _read_group(src, j)
        args.append(arg)
    return args, j


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _sections(src: str) -> list[tuple[str, int, int]]:
    """Return [(name, content_start, content_end)] over the document body."""
    marks = [(m.group(1), m.start(), m.end()) for m in SECTION_RE.finditer(src)]
    out = []
    for idx, (name, start, end) in enumerate(marks):
        stop = marks[idx + 1][1] if idx + 1 < len(marks) else len(src)
        out.append((_strip(name), end, stop))
    return out


def _parse_skills(src: str, lo: int, hi: int) -> list[SkillLine]:
    """Skills lines look like: item, bold Category colon, then comma list."""
    lines: list[SkillLine] = []
    pat = re.compile(r"\\item\s*\\textbf\{([^}]*)\}")
    for n, m in enumerate(pat.finditer(src, lo, hi)):
        nxt = src.find(r"\item", m.end())
        endi = src.find(r"\end{itemize}", m.end())
        cands = [x for x in (nxt, endi) if x != -1 and x < hi]
        stop = min(cands) if cands else hi
        body = _strip(src[m.end():stop])
        category = _strip(m.group(1)).rstrip(":")
        items = [_strip(p) for p in body.split(",") if _strip(p)]
        lines.append(SkillLine(
            id="skill.%d" % n, category=category, items=items,
            span=Span(m.start(), stop), line=_line_of(src, m.start()), order=n,
        ))
    return lines


def _parse_bullets(src: str, lo: int, hi: int, prefix: str) -> list[Bullet]:
    bullets: list[Bullet] = []
    pat = re.compile(r"\\resumeItem\s*\{")
    i, n = lo, 0
    while True:
        m = pat.search(src, i, hi)
        if not m:
            break
        inner, after = _read_group(src, m.end() - 1)
        bullets.append(Bullet(
            id="%s.b%d" % (prefix, n), text=_strip(inner),
            span=Span(m.start(), after), line=_line_of(src, m.start()),
        ))
        i, n = after, n + 1
    return bullets


def _split_tech(meta: str) -> list[str]:
    """Project headings carry a trailing comma-separated tech list."""
    if "---" not in meta:
        return []
    tail = meta.split("---", 1)[1]
    return [_strip(t) for t in tail.split(",") if _strip(t)]


def _clean(s: str) -> str:
    """Strip common formatting macros to get at the plain text."""
    s = re.sub(r"\\(textbf|textit|small|scshape|href)\s*", "", s)
    s = s.replace("{", "").replace("}", "")
    return _strip(s)


def _parse_entries(src: str, lo: int, hi: int, kind: str) -> list[Entry]:
    """Parse resumeSubheading (4 args) and resumeProjectHeading (2 args)."""
    pat = re.compile(r"\\(resumeSubheading|resumeProjectHeading)\s*(?=\{)")
    entries: list[Entry] = []
    marks = list(pat.finditer(src, lo, hi))
    for n, m in enumerate(marks):
        eid = "%s.%d" % (kind, n)
        org = right_top = right_bottom = ""

        if m.group(1) == "resumeSubheading":
            args, after = _read_args(src, m.end(), 4)
            title, right_top, org, right_bottom = (_clean(a) for a in args)
            tech: list[str] = []
            if kind == "education":
                # arg2 is CGPA here, arg4 is the year range
                date = right_bottom
                meta = ("%s | %s" % (org, right_top)).strip(" |")
            else:
                date = right_top
                meta = ("%s | %s" % (org, right_bottom)).strip(" |")
        else:
            args, after = _read_args(src, m.end(), 2)
            raw, date = args[0], _clean(args[1])
            title = _clean(raw.split("---")[0])
            tech = _split_tech(raw)
            meta = ", ".join(tech)

        stop = marks[n + 1].start() if n + 1 < len(marks) else hi
        bullets = _parse_bullets(src, after, stop, eid)

        # Safe deletion boundary: just past this entry's own list terminator,
        # never as far as the section end (see Entry.remove_end).
        remove_end = stop
        if bullets:
            tail = src.find(r"\resumeItemListEnd", bullets[-1].span.end)
            if tail != -1 and tail < stop:
                remove_end = tail + len(r"\resumeItemListEnd")
                while remove_end < stop and src[remove_end] in " \t":
                    remove_end += 1
                if remove_end < stop and src[remove_end] == "\n":
                    remove_end += 1

        entries.append(Entry(
            id=eid, kind=kind, title=title, meta=meta,
            date=date, bullets=bullets, tech=tech,
            span=Span(m.start(), stop), line=_line_of(src, m.start()),
            org=org, right_top=right_top, right_bottom=right_bottom,
            remove_end=remove_end,
        ))
    return entries


def _parse_plain_items(src: str, lo: int, hi: int, prefix: str) -> list[ListItem]:
    items: list[ListItem] = []
    marks = list(re.finditer(r"\\item\s", src[lo:hi]))
    for n, m in enumerate(marks):
        start = lo + m.start()
        body_start = lo + m.end()
        if n + 1 < len(marks):
            stop = lo + marks[n + 1].start()
        else:
            endi = src.find(r"\end{itemize}", body_start)
            stop = endi if endi != -1 and endi < hi else hi
        raw = _strip(src[body_start:stop])
        items.append(ListItem(
            id="%s.%d" % (prefix, n), text=raw, plain=_clean(raw),
            span=Span(start, stop), line=_line_of(src, start),
        ))
    return items


def parse(src: str) -> Resume:
    r = Resume(source=src)

    body_start = 0
    m = BEGIN_DOC_RE.search(src)
    if m:
        body_start = m.end()

    cm = CENTER_RE.search(src, body_start)
    if cm:
        r.contact = Span(cm.start(), cm.end())

    seen = _sections(src)
    r.section_order = [name for name, _, _ in seen]

    for name, lo, hi in seen:
        key = name.lower()
        if key.startswith("summary"):
            chunk = re.sub(r"\\small\s*", "", src[lo:hi])
            r.summary = _strip(chunk)
            r.summary_span = Span(lo, hi)
        elif "skill" in key:
            r.skills = _parse_skills(src, lo, hi)
        elif key.startswith("experience"):
            r.experience = _parse_entries(src, lo, hi, "experience")
        elif key.startswith("project"):
            r.projects = _parse_entries(src, lo, hi, "project")
        elif key.startswith("education"):
            r.education = _parse_entries(src, lo, hi, "education")
        elif "certification" in key or "achievement" in key:
            r.certifications = _parse_plain_items(src, lo, hi, "cert")
        elif "publication" in key or "research" in key:
            r.publications = _parse_plain_items(src, lo, hi, "pub")

    _validate(r)
    return r


def _validate(r: Resume) -> None:
    """Cheap structural checks, feeding spec section 7 (Resume checks)."""
    src = r.source
    if src.count(r"\begin{itemize}") != src.count(r"\end{itemize}"):
        r.warnings.append("unbalanced itemize environments")
    starts = src.count(r"\resumeItemListStart")
    ends = src.count(r"\resumeItemListEnd")
    if starts != ends:
        r.warnings.append("resumeItemListStart/End mismatch (%d/%d)" % (starts, ends))
    for m in re.finditer(r"(?<!\\)&", src):
        ln = _line_of(src, m.start())
        seg = src[max(0, m.start() - 200):m.start()]
        if r"\begin{tabular" not in seg:
            r.warnings.append("line %d: unescaped ampersand outside tabular" % ln)
    if not r.summary:
        r.warnings.append("no Summary section found")
    if not r.projects:
        r.warnings.append("no Projects parsed")
