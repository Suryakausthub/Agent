"""PDF compilation and page counting (spec section 13).

The spec is emphatic that page count must be MEASURED, never estimated by the
model. Tectonic is the real backend, but its package fetch is unreliable on
this machine's network, so it is deferred behind this interface and a stub
stands in meanwhile.

The stub is honest about what it is. It estimates from a line-budget model of
the actual template geometry -- letterpaper, 0.5in margins, 11pt -- and every
result it returns is marked `measured=False`. Callers must treat an unmeasured
page count as advisory: the trim loop will run on it, but the human approval
gate (spec 17) must not be satisfied by a stub result alone.

Tectonic is now wired in (tools/tectonic/tectonic.exe, compile.backend:
tectonic). Its package fetch works on this network; packages are cached after
the first run. If it cannot run, compile.fallback_to_stub drops back to the
calibrated estimate, still marked measured=False.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zlib
from dataclasses import dataclass, field


@dataclass
class CompileResult:
    pages: int
    measured: bool                  # True only when a PDF was really produced
    ok: bool = True
    pdf_path: str = ""
    log: str = ""
    errors: list[str] = field(default_factory=list)
    backend: str = ""

    @property
    def advisory(self) -> bool:
        return not self.measured


class CompileError(RuntimeError):
    pass


# ----------------------------------------------------------------- stub model

# These two coefficients were originally derived from paper geometry and were
# simply wrong: they scored the master resume at 2 pages when Overleaf renders
# it as 1. Absolute geometry is not worth guessing at.
#
# Instead the budget is CALIBRATED against a document whose real page count is
# known -- the master resume, confirmed as one page. The stub then answers a
# question it can actually answer: "is this tailored version taller than the
# master?" That is relative, robust to coefficient error, and it is the
# question the trim loop needs anyway, since the master is the thing already
# known to fit.
#
# _LINES_PER_PAGE is a fallback for when no calibration document is supplied.
_LINES_PER_PAGE = 76
_CHARS_PER_LINE = 108           # only affects relative wrap cost, not the verdict

# Tailoring rewrites bullets to similar lengths, so a small overshoot is noise
# rather than a real overflow. Anything beyond this is treated as growth.
_CALIBRATION_MARGIN = 1.02

# Vertical cost of structural elements, in line-equivalents.
_COST = {
    "section": 2.2,             # rule + spacing above and below
    "subheading": 2.1,          # two-row tabular
    "project_heading": 1.2,
    "item": 0.15,               # list item overhead beyond its wrapped text
    "center_block": 3.0,
}


def _strip_latex(text: str) -> str:
    text = re.sub(r"%.*", "", text)
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def calibrate(master_tex: str, known_pages: int = 1) -> float:
    """Lines-per-page derived from a document whose real page count is known.

    The master resume renders as exactly one page in Overleaf, so its measured
    line-equivalents ARE one page's worth. Everything downstream is judged
    against that, rather than against invented paper geometry.
    """
    lines = line_equivalents(master_tex)
    return (lines / max(1, known_pages)) * _CALIBRATION_MARGIN


def line_equivalents(tex: str) -> float:
    """Measured height of the document in line-equivalents. No page verdict."""
    body = tex
    m = re.search(r"\\begin\{document\}", tex)
    if m:
        body = tex[m.end():]

    lines = 0.0
    lines += _COST["center_block"] * len(re.findall(r"\\begin\{center\}", body))
    lines += _COST["section"] * len(re.findall(r"\\section\{", body))
    lines += _COST["subheading"] * len(re.findall(r"\\resumeSubheading", body))
    lines += _COST["project_heading"] * len(re.findall(r"\\resumeProjectHeading", body))

    n_items = len(re.findall(r"\\resumeItem\{", body)) + len(re.findall(r"\\item\s", body))
    lines += _COST["item"] * n_items

    # Wrapped text height of every piece of visible prose.
    for m in re.finditer(r"\\resumeItem\{", body):
        depth, i = 0, m.end() - 1
        while i < len(body):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        inner = _strip_latex(body[m.end():i])
        lines += max(1, -(-len(inner) // _CHARS_PER_LINE))

    # Plain \item entries (skills, certifications, publications) and the summary
    for m in re.finditer(r"\\item\s([^\n]*(?:\n(?!\s*\\item|\s*\\end)[^\n]*)*)", body):
        inner = _strip_latex(m.group(1))
        if inner:
            lines += max(1, -(-len(inner) // _CHARS_PER_LINE))

    summary = re.search(r"\\section\{Summary\}(.*?)(?=\\section\{)", body, re.S)
    if summary:
        inner = _strip_latex(summary.group(1))
        lines += max(0, -(-len(inner) // _CHARS_PER_LINE))

    return lines


def estimate_pages(tex: str, lines_per_page: float | None = None) -> tuple[int, float]:
    """Return (pages, fractional_lines). Advisory only."""
    lines = line_equivalents(tex)
    budget = lines_per_page or _LINES_PER_PAGE
    pages = max(1, int(-(-lines // budget)))
    return pages, lines


def _compile_stub(tex: str, lines_per_page: float | None = None) -> CompileResult:
    pages, lines = estimate_pages(tex, lines_per_page)
    return CompileResult(
        pages=pages, measured=False, ok=True, backend="stub",
        log="estimated %.1f line-equivalents / %.1f per page (NOT MEASURED)"
            % (lines, lines_per_page or _LINES_PER_PAGE),
    )


# ------------------------------------------------------------------- tectonic


# /Count also appears in outline (bookmark) dictionaries, so only a /Count
# inside a /Type /Pages dictionary is a page count.
_PAGE_RE = re.compile(rb"<<(?:(?!>>).)*?/Type\s*/Pages\b(?:(?!>>).)*?>>", re.S)
_COUNT_RE = re.compile(rb"/Count\s+(\d+)")


_LOG_PAGES_RE = re.compile(r"Output written on \S+ \((\d+) pages?")
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def _pdf_page_count(path: str) -> int:
    """Page count from the PDF's page tree.

    xdvipdfmx (Tectonic) stores the page tree inside compressed object
    streams, where a plain byte scan finds nothing -- it returned 0 for a
    one-page resume. Compressed streams are inflated and searched too.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    blobs = [data]
    for m in _STREAM_RE.finditer(data):
        try:
            blobs.append(zlib.decompress(m.group(1)))
        except zlib.error:
            continue
    counts = [int(c.group(1)) for blob in blobs
              for d in _PAGE_RE.finditer(blob)
              for c in _COUNT_RE.finditer(d.group(0))]
    if counts:
        return max(counts)
    return data.count(b"/Type /Page") - data.count(b"/Type /Pages")


def _log_page_count(workdir: str) -> int:
    """XeTeX's own report: 'Output written on resume.xdv (1 page, ...)'."""
    try:
        with open(os.path.join(workdir, "resume.log"), encoding="utf-8",
                  errors="replace") as fh:
            m = _LOG_PAGES_RE.search(fh.read())
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


# Tectonic runs XeTeX; this template targets pdfLaTeX (Overleaf). Only the
# pdfTeX-specific glyph-to-Unicode mapping differs, and XeTeX emits Unicode
# text natively, so these lines are dropped from the COPY that is compiled.
# main.tex keeps them -- Overleaf still needs them.
_PDFTEX_ONLY = re.compile(r"^[ \t]*\\input\{glyphtounicode\}[ \t]*$|\\pdfgentounicode\s*=\s*1",
                          re.M)


# Characters the default Latin Modern font lacks; XeTeX would silently drop
# them ("CodeChef 4★" rendered as "CodeChef 4").
_GLYPHS = {"★": r"$\star$", "☆": r"$\star$"}


def xetex_compat(tex: str) -> str:
    tex = _PDFTEX_ONLY.sub("", tex)
    for ch, rep in _GLYPHS.items():
        tex = tex.replace(ch, rep)
    return tex


def _resolve_exe(cfg: dict) -> str | None:
    """Configured path (relative to the project root), else PATH."""
    path = cfg.get("tectonic_path") or ""
    if path and not os.path.isabs(path):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                            "..", "..", ".."))
        path = os.path.join(root, path)
    return path if path and os.path.exists(path) else shutil.which("tectonic")


def _compile_tectonic(tex: str, cfg: dict, outdir: str | None = None) -> CompileResult:
    exe = _resolve_exe(cfg)
    if not exe or not os.path.exists(exe):
        raise CompileError(
            "tectonic not found - set compile.tectonic_path in config.yaml")

    workdir = outdir or tempfile.mkdtemp(prefix="jobagent-tex-")
    os.makedirs(workdir, exist_ok=True)
    tex_path = os.path.join(workdir, "resume.tex")
    with open(tex_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(xetex_compat(tex))

    cmd = [exe, "-X", "compile", tex_path, "--outdir", workdir, "--keep-logs"]
    bundle = cfg.get("bundle_path")
    if bundle:
        # A pre-seeded local bundle removes the runtime network dependency that
        # makes the default fetch-on-demand path unusable here.
        cmd += ["--bundle", bundle]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=cfg.get("timeout_s", 120))
    pdf = os.path.join(workdir, "resume.pdf")
    log = (proc.stdout or "") + (proc.stderr or "")

    if not os.path.exists(pdf):
        errs = [l for l in log.splitlines() if l.lower().startswith("error")]
        return CompileResult(pages=0, measured=False, ok=False, log=log,
                             errors=errs or ["no PDF produced"],
                             backend="tectonic")

    pages = _log_page_count(workdir) or _pdf_page_count(pdf)
    if pages <= 0:
        return CompileResult(pages=0, measured=False, ok=False, log=log,
                             errors=["PDF produced but its page count could "
                                     "not be read"], backend="tectonic")
    return CompileResult(pages=pages, measured=True, ok=True,
                         pdf_path=pdf, log=log, backend="tectonic")


_FETCH_FAILED = re.compile(
    r"(failed to (download|fetch|open)|error sending request|dns error|"
    r"connection (refused|reset|timed out)|network)", re.I)


def compile_tex(tex: str, cfg: dict, outdir: str | None = None,
                lines_per_page: float | None = None) -> CompileResult:
    backend = (cfg or {}).get("backend", "stub")
    if backend == "tectonic":
        unavailable = False
        try:
            res = _compile_tectonic(tex, cfg, outdir)
            # Tectonic ran. Only a failed package DOWNLOAD counts as the tool
            # being unavailable; a LaTeX error means the document is broken,
            # and must surface as a failure, never be papered over with an
            # estimate.
            unavailable = (not res.ok and bool(_FETCH_FAILED.search(res.log)))
        except (CompileError, OSError, subprocess.TimeoutExpired) as e:
            res = CompileResult(pages=0, measured=False, ok=False,
                                errors=[str(e)], backend="tectonic")
            unavailable = True
        if res.ok or not unavailable or not cfg.get("fallback_to_stub", False):
            return res
        # Tectonic could not run (missing binary, a package it cannot fetch
        # offline, a timeout). Fall back to the calibrated estimate rather
        # than blocking the whole run -- marked measured=False, so every
        # surface still labels the page count as an estimate.
        est = _compile_stub(tex, lines_per_page)
        est.log = "tectonic failed (%s); fell back to the estimate. %s" % (
            "; ".join(res.errors[:2]) or "unknown error", est.log)
        est.errors = ["tectonic unavailable - page count is estimated: %s"
                      % ("; ".join(res.errors[:1]) or "unknown error")]
        return est
    if backend == "stub":
        return _compile_stub(tex, lines_per_page)
    raise CompileError("unknown compile backend %r" % backend)


# ------------------------------------------------------------------ PDF files

_PDF_LOCK = threading.Lock()

_WATERMARK = (r"\usepackage{draftwatermark}\SetWatermarkText{DRAFT}"
              r"\SetWatermarkLightness{0.92}\SetWatermarkScale{1.1}" "\n")


def render_pdf(tex: str, cfg: dict, cache_dir: str,
               draft: bool = False) -> str:
    """Compile `tex` to a PDF file and return its path.

    Always uses Tectonic, whatever compile.backend says: a PDF is the thing
    being produced, not a page-count estimate. Results are cached by content
    hash, so re-opening a preview or downloading twice costs nothing.

    `draft=True` stamps a light DRAFT watermark -- used for previews of a
    resume that has not been approved, so the clean file only exists once
    the approval gate has been passed.
    """
    if draft:
        i = tex.find(r"\begin{document}")
        if i < 0:
            raise CompileError("no \\begin{document} in the tailored resume")
        tex = tex[:i] + _WATERMARK + tex[i:]
    digest = hashlib.sha256(tex.encode("utf-8")).hexdigest()[:20]
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "%s%s.pdf" % (digest, "-draft" if draft else ""))

    # The preview and the download can ask for the same PDF at once; build it
    # once and let the other request reuse the cached file.
    with _PDF_LOCK:
        if os.path.exists(path):
            return path
        workdir = tempfile.mkdtemp(prefix="jobagent-pdf-")
        res = _compile_tectonic(tex, dict(cfg, backend="tectonic"), workdir)
        if not res.ok or not res.pdf_path:
            raise CompileError("PDF build failed: %s"
                               % ("; ".join(res.errors[:3]) or "no PDF produced"))
        tmp = path + ".tmp"
        shutil.copyfile(res.pdf_path, tmp)
        os.replace(tmp, path)
        shutil.rmtree(workdir, ignore_errors=True)
        return path
