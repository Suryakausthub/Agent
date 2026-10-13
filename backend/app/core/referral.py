"""Referral outreach generation (spec sections 4 and 16).

Outreach gets the SAME anti-hallucination treatment as the resume, because the
failure mode is worse: a fabricated shared connection or invented achievement
goes to a real person under the candidate's name and cannot be retracted.

  * Only achievements and skills present in the master resume may appear.
  * No invented personal connection. The model is never told the recipient's
    background, so it has nothing to fabricate a rapport from. If the user
    supplies a real detail, it is passed through verbatim as a quoted fact.
  * Numbers are masked, as everywhere else.
  * Output is whitelist-checked before it can be copied or sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .jd_extract import JobDescription
from .llm import LLM, HallucinatedNumber, LLMError
from .model import Resume
from .vocabulary import Vocabulary
from .whitelist import CheckResult, Whitelist

LINKEDIN = "linkedin"
EMAIL = "email"
FOLLOWUP = "followup"

_LIMITS = {LINKEDIN: 300, EMAIL: 1400, FOLLOWUP: 600}

_SYSTEM = """You write a referral request that THE CANDIDATE sends in their \
own voice, first person.

You are the candidate writing to someone who might refer them. Never write \
about the candidate in the third person, and never recommend them as if you \
were a referee. Write "I am", "I came across", "could you refer me" -- not \
"I recommend X" or "he built".

Structure, following this shape closely:

  Hi <recipient>,

  I came across the <role> opening at <company> (Job ID: <id>) in <location>,
  and I'm very interested in the opportunity.

  I'm a <degree/standing> with hands-on experience in <skills from the brief>.
  <One concrete line drawn from the evidence in the brief.>

  If you're comfortable doing so, could you please refer me for this position?
  I'd be happy to share my resume and any other details you need.

  Thank you for your time and consideration.

  <candidate name>
  <candidate email>

Absolute rules:
- Use ONLY facts given in the brief. Never invent a skill, project, metric, \
employer, university, degree or achievement.
- Never invent a connection to the recipient. Do not claim you follow their \
work, read their post or saw their talk unless given as a supplied fact.
- Tokens like <N0> are opaque placeholders. Reproduce them exactly and never \
write a digit yourself.
- Plain and warm, never salesy. No markdown, no bullet points, no emoji.
- Output the message only."""


@dataclass
class ReferralRequest:
    kind: str = LINKEDIN
    recipient: str = ""
    recipient_role: str = ""
    supplied_facts: str = ""       # verbatim, user-provided, never invented
    candidate_name: str = ""
    candidate_email: str = ""
    candidate_standing: str = ""   # e.g. "B.Tech CS, graduating 2026"
    job_url: str = ""


@dataclass
class ReferralMessage:
    kind: str
    subject: str = ""
    body: str = ""
    ok: bool = True
    violations: list = field(default_factory=list)
    evidence_used: list[str] = field(default_factory=list)
    reason: str = ""


_TYPOGRAPHIC = {
    "‑": "-", "‐": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "…": "...", "•": "-",
}


def _normalise_text(text: str) -> str:
    """Plain ASCII punctuation, so the message pastes cleanly anywhere."""
    for bad, good in _TYPOGRAPHIC.items():
        text = text.replace(bad, good)
    lines = [l.rstrip() for l in text.splitlines()]
    out: list[str] = []
    for l in lines:                       # collapse runs of blank lines
        if l or (out and out[-1]):
            out.append(l)
    return "\n".join(out).strip()


def _brief(resume: Resume, jd: JobDescription, vocab: Vocabulary,
           req: ReferralRequest) -> tuple[str, list[str]]:
    """Assemble ONLY the candidate facts relevant to this JD.

    This is the retrieval half of extract-then-rewrite: the model sees a small
    curated brief, not the whole resume and not the whole posting, so it has
    little raw material to embellish from.
    """
    jd_keys = jd.all_keys() | set(jd.ats_terms)

    ranked: list[tuple[int, str]] = []
    for e in resume.experience + resume.projects:
        text = " ".join([e.title, e.meta, *e.tech,
                         *(b.text for b in e.bullets)])
        overlap = set(vocab.extract(text)) & jd_keys
        if overlap:
            ranked.append((len(overlap),
                           "%s (%s): %s" % (e.title, e.meta or e.date,
                                            e.bullets[0].text if e.bullets else "")))
    ranked.sort(key=lambda t: -t[0])
    evidence = [txt for _, txt in ranked[:3]]

    # Name concrete technologies, not abstractions. "Java, Spring Boot, Kafka,
    # PostgreSQL" is what a referrer can act on; "Code Quality, Scalability,
    # System Design" reads as filler and says nothing specific. Concrete
    # categories first, and concepts only to pad a short list.
    held = set(vocab.extract(resume.evidence_text())) & jd_keys
    concrete = sorted({vocab.canonical(k) for k in held
                       if vocab.category(k) in
                       ("language", "framework", "database", "tool", "platform")})
    abstract = sorted({vocab.canonical(k) for k in held
                       if vocab.category(k) not in
                       ("language", "framework", "database", "tool", "platform")})
    skills = concrete[:8] or abstract[:6]
    if len(skills) < 4:
        skills = skills + [a for a in abstract if a not in skills][:4 - len(skills)]

    lines = [
        "YOU ARE: %s" % (req.candidate_name or "the candidate"),
    ]
    if req.candidate_email:
        lines.append("YOUR EMAIL: %s" % req.candidate_email)
    if req.candidate_standing:
        lines.append("YOUR STANDING: %s" % req.candidate_standing)

    lines += [
        "",
        "WRITING TO: %s%s" % (
            req.recipient or "the recipient",
            " (%s)" % req.recipient_role if req.recipient_role else ""),
        "",
        "THE ROLE: %s at %s" % (jd.title or "the role", jd.company or "the company"),
    ]
    if jd.location:
        lines.append("LOCATION: %s" % jd.location)
    if jd.job_id:
        lines.append("JOB ID: %s" % jd.job_id)
    if req.job_url:
        lines.append("JOB LINK: %s" % req.job_url)

    lines += [
        "",
        "YOUR SKILLS RELEVANT TO THIS ROLE: %s"
        % (", ".join(skills[:12]) or "see evidence below"),
        "",
        "YOUR EVIDENCE (draw at most one line from this, keeping it true):",
    ]
    lines += ["- %s" % e for e in evidence] or ["- (nothing closely relevant)"]

    if req.supplied_facts.strip():
        lines += ["", "TRUE FACTS ABOUT THE RECIPIENT that you supplied and "
                      "may reference:", req.supplied_facts.strip()]

    if req.kind == FOLLOWUP:
        lines += ["", "THIS IS A FOLLOW-UP to an earlier message that got no "
                      "reply. Two or three sentences. Do not repeat the full "
                      "pitch; briefly reference the earlier note and ask again "
                      "politely."]
    elif req.kind == EMAIL:
        lines += ["", "WRITE AN EMAIL. First line must be exactly "
                      "'Subject: <subject>' -- a specific subject naming the "
                      "role and that this is a referral request. Then a blank "
                      "line, then the message. End with your name and email "
                      "on separate lines. Include the job link if given."]
    else:
        # LinkedIn's connection-note limit is hard, and the model overshoots it
        # badly when only given a number, so spell out the shape instead.
        lines += ["", "WRITE A LINKEDIN DM. HARD LIMIT %d CHARACTERS -- roughly "
                      "four short sentences. Use this shape and nothing more:"
                      % _LIMITS[LINKEDIN],
                  "",
                  "  Hi <name>, I'm <standing> and I came across the <role> "
                  "role at <company>.",
                  "  I've worked with <three or four technologies>"
                  "<one short clause of evidence>.",
                  "  Would you be open to referring me? Happy to share my "
                  "resume.",
                  "  Thanks, <your first name>",
                  "",
                  "No job ID, no job link, no email signature, no location. "
                  "Count your characters and cut adjectives before facts."]

    return "\n".join(lines), evidence


def generate(resume: Resume, jd: JobDescription, vocab: Vocabulary,
             wl: Whitelist, llm: LLM, cfg: dict,
             req: ReferralRequest) -> ReferralMessage:
    brief, evidence = _brief(resume, jd, vocab, req)

    try:
        res = llm.rewrite_masked(_SYSTEM, brief,
                                 effort=cfg["llm"].get("reasoning_effort", "low"))
    except HallucinatedNumber as e:
        return ReferralMessage(kind=req.kind, ok=False,
                               reason="number guard: %s" % e)
    except LLMError as e:
        return ReferralMessage(kind=req.kind, ok=False, reason=str(e))

    # The model emits typographic Unicode (non-breaking hyphens, smart quotes)
    # that looks fine here and pastes badly into LinkedIn and mail clients.
    text = _normalise_text(res.text.strip())

    subject = ""
    if req.kind == EMAIL and text.lower().startswith("subject:"):
        head, _, rest = text.partition("\n")
        subject = head.split(":", 1)[1].strip()
        text = rest.strip()

    # Same verification as a resume bullet, with two adjustments for outreach:
    # the employer's own name is context rather than a claimed skill, and
    # conversational wording is expected, so only terms and numbers are checked.
    context = set(vocab.extract("%s %s %s" % (jd.company, jd.title, req.recipient_role)))
    check = CheckResult()
    wl.check_text(text, brief, "referral.%s" % req.kind, check,
                  allow_terms=context, check_tokens=False)

    msg = ReferralMessage(
        kind=req.kind, subject=subject, body=text,
        ok=check.ok, violations=check.high + check.low,
        evidence_used=evidence,
        reason="ok" if check.ok else "%d unsupported claim(s)" % len(check.high),
    )
    limit = _LIMITS.get(req.kind, 1400)
    if len(text) > limit:
        msg.reason += " (over %d characters)" % limit
    return msg
