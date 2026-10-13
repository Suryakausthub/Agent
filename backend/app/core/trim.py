"""Page-overflow trim loop (spec section 13).

Cut order comes from config, not from the model, and follows the policy chosen
for this resume:

    summary_condense -> skills_lowest_relevance -> project_lowest_relevance
    -> experience_bullet_lowest_relevance

Certifications & Achievements and Research Publications are PROTECTED, in
addition to contact and education. That is a deliberate departure from a pure
relevance ranking: ICPC standing and IEEE publications score near zero against
a "Java, Spring Boot, Docker" keyword match, so a naive relevance trimmer would
delete exactly the material that differentiates this candidate. Relevance
decides order *within* the trimmable sections only.

Cosmetic cheating is not available: font size, margins and leading are never
touched (spec 13). If the content will not fit, the loop stops and says so
rather than shrinking type to force a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compile import CompileResult, calibrate, compile_tex
from .model import Resume
from .render import RenderResult, render
from .tailor import TailorPlan, TailorResult


@dataclass
class TrimAction:
    step: str            # which rule fired
    target: str          # what was removed
    detail: str = ""
    pages_before: int = 0
    pages_after: int = 0


@dataclass
class TrimOutcome:
    tex: str
    pages: int
    fits: bool
    measured: bool
    attempts: int = 0
    actions: list[TrimAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    render: RenderResult | None = None
    compile_result: CompileResult | None = None

    @property
    def needs_manual_edit(self) -> bool:
        return not self.fits


def _condense_summary(resume: Resume, result: TailorResult,
                      out: TrimOutcome) -> bool:
    """Drop the trailing sentence of the summary.

    Deterministic: sentences are removed from the end, never rewritten, so no
    model call and no chance of introducing new claims under space pressure.
    """
    text = result.summary or resume.summary
    parts = [s.strip() for s in text.split(". ") if s.strip()]
    if len(parts) < 2:
        return False
    removed = parts[-1]
    result.summary = ". ".join(parts[:-1]).rstrip(".") + "."
    out.actions.append(TrimAction(
        "summary_condense", "last sentence", removed[:70]))
    return True


def _drop_skill_line(resume: Resume, plan: TailorPlan, out: TrimOutcome) -> bool:
    """Remove the lowest-relevance skill line (it is last in skill_order)."""
    if len(plan.skill_order) <= 3:
        return False
    victim_id = plan.skill_order[-1]
    line = next((s for s in resume.skills if s.id == victim_id), None)
    plan.skill_order = plan.skill_order[:-1]
    out.actions.append(TrimAction(
        "skills_lowest_relevance", line.category if line else victim_id,
        ", ".join(line.items[:4]) if line else ""))
    return True


def _drop_project(plan: TailorPlan, cfg: dict, out: TrimOutcome) -> bool:
    min_projects = cfg["resume"].get("min_projects", 2)
    if len(plan.projects_kept) <= min_projects:
        return False
    victim = plan.projects_kept[-1]        # already relevance-ordered
    plan.projects_kept = plan.projects_kept[:-1]
    plan.projects_dropped.append(victim)
    out.actions.append(TrimAction(
        "project_lowest_relevance", victim.title,
        "tech: %s" % ", ".join(victim.tech)))
    return True


def _drop_bullet(plan: TailorPlan, result: TailorResult, out: TrimOutcome) -> bool:
    """Remove the least JD-relevant experience bullet, keeping 2 per entry."""
    counts: dict[str, int] = {}
    for rb in plan.bullets:
        counts[rb.entry_id] = counts.get(rb.entry_id, 0) + 1

    candidates = [rb for rb in plan.bullets
                  if rb.entry_id.startswith("experience") and counts[rb.entry_id] > 2]
    if not candidates:
        return False
    victim = min(candidates, key=lambda rb: rb.score)
    plan.bullets = [rb for rb in plan.bullets if rb.bullet.id != victim.bullet.id]
    result.rewritten.pop(victim.bullet.id, None)
    out.actions.append(TrimAction(
        "experience_bullet_lowest_relevance", victim.bullet.id,
        victim.bullet.text[:70]))
    return True


_STEPS = {
    "summary_condense": lambda r, p, res, cfg, out: _condense_summary(r, res, out),
    "skills_lowest_relevance": lambda r, p, res, cfg, out: _drop_skill_line(r, p, out),
    "project_lowest_relevance": lambda r, p, res, cfg, out: _drop_project(p, cfg, out),
    "experience_bullet_lowest_relevance":
        lambda r, p, res, cfg, out: _drop_bullet(p, res, out),
}


def fit_to_pages(resume: Resume, plan: TailorPlan, result: TailorResult,
                 cfg: dict) -> TrimOutcome:
    max_pages = cfg["resume"].get("max_pages", 1)
    max_attempts = cfg["resume"].get("max_trim_attempts", 3)
    order = cfg["resume"].get("trim_order", list(_STEPS))

    # Calibrate the stub against the master resume, which is confirmed to
    # render as one page. This turns an unreliable absolute judgement into a
    # reliable relative one: the tailored version only fails if it is TALLER
    # than a document already known to fit.
    # Computed for Tectonic too: it is the fallback budget if Tectonic cannot
    # run (compile.fallback_to_stub).
    budget = calibrate(resume.source,
                       cfg["resume"].get("master_known_pages", 1))

    rendered = render(resume, plan, result)
    comp = compile_tex(rendered.tex, cfg.get("compile", {}), lines_per_page=budget)

    out = TrimOutcome(tex=rendered.tex, pages=comp.pages, fits=comp.pages <= max_pages,
                      measured=comp.measured, render=rendered, compile_result=comp)
    out.warnings.extend(rendered.warnings)

    if not comp.ok:
        out.fits = False
        out.warnings.append("compile failed: %s" % "; ".join(comp.errors[:3]))
        return out

    if out.fits:
        if not comp.measured:
            out.warnings.append(
                "page count is ESTIMATED, not measured - not sufficient for the "
                "approval gate on its own (spec 13)")
        return out

    # Each pass walks the configured cut order from the top and applies the
    # first rule that can still act, so cheap cuts are always preferred. The
    # budget counts render/compile cycles, NOT rules: an earlier version capped
    # this at 3 and the loop spent its whole budget removing two summary
    # sentences and one skill line, never reaching the project cut that would
    # actually free a page. Exhaustion of the rules is the real stopping
    # condition; the cap only guards against a rule that always reports success.
    while not out.fits and out.attempts < max_attempts:
        acted = False
        for name in order:
            rule = _STEPS.get(name)
            if rule is None:
                if name not in [w.split()[-1] for w in out.warnings]:
                    out.warnings.append("unknown trim rule %r in config" % name)
                continue
            before = out.pages
            if not rule(resume, plan, result, cfg, out):
                continue          # this rule is exhausted, try the next one
            acted = True
            rendered = render(resume, plan, result)
            comp = compile_tex(rendered.tex, cfg.get("compile", {}),
                               lines_per_page=budget)
            out.tex, out.pages = rendered.tex, comp.pages
            out.render, out.compile_result = rendered, comp
            out.measured = comp.measured
            out.fits = comp.pages <= max_pages
            out.warnings.extend(rendered.warnings)
            if out.actions:
                out.actions[-1].pages_before = before
                out.actions[-1].pages_after = comp.pages
            break
        out.attempts += 1
        if not acted:
            out.warnings.append(
                "no trim rule could act - everything left is protected or at "
                "its configured floor")
            break

    if not out.fits:
        out.warnings.append(
            "still %d page(s) after %d attempt(s); flagged for manual edit "
            "rather than shrinking margins or type (spec 13)"
            % (out.pages, out.attempts))
    if not out.measured:
        out.warnings.append(
            "page count is ESTIMATED, not measured - wire up Tectonic before "
            "trusting this for a real application")
    return out
