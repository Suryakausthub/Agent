# Job Agent — Complete Features, Functionalities & Guardrails

> **Note on the LLM being used:** This spec assumes a **basic-tier LLM API key** (not a top-end model). It is less reliable at following complex instructions, more prone to hallucination, and cannot be fully trusted to self-verify its own output. Every guardrail below is written to work with that constraint — favoring deterministic code checks, small/simple prompts, and human approval over relying on the model's own judgment.

---

## 1. 📝 New Application

The main input workspace where the user creates a new job application.

**Job Description Input**
- Paste a job URL
- "Fetch JD" button — automatically fetches job description from the URL
- Alternative: paste the full job description manually
- Extract: Company, Job title, Location, Job ID, Employment type, Experience level, Job category, Technical skills, Soft skills, Preferred qualifications, ATS keywords

**Resume Input**
- Upload `.tex` LaTeX resume
- Paste LaTeX resume code
- Load Default resume
- Save as Default — maintain a reusable master resume

**Candidate Information** (optional, used to personalize outputs)
- Name
- Referral contact name
- LinkedIn URL
- Notes/preferences
- Areas to emphasize (Backend, AI/ML, DevOps, Java, etc.)

**Agent Execution**
- Run Agent — processes JD + resume + candidate information, generates the complete analysis pipeline

---

## 2. 📋 Job Summary

Automatically converts a raw JD into structured information.

- **Company** — company name
- **Role** — exact job title
- **Location** — city / remote / hybrid / unspecified
- **Level** — Intern, Entry, Junior, Mid, Senior, etc.
- **Type** — Full-time, Part-time, Internship, Contract
- **Job ID** — extracted if available
- **Category** — Backend/SDE, Frontend, DevOps, Data, AI/ML, QA/SDET, etc.
- **Technical Skills** — e.g. Java, Docker, CI/CD, Git, REST, Kubernetes
- **Soft Skills** — e.g. Problem-solving, Communication, Leadership, Ownership
- **Preferred Qualifications** — extracted separately from mandatory requirements

---

## 3. 📊 Score Dashboard

Evaluates how well the candidate matches the job.

- **Overall Match** — e.g. `Overall Match: 82%`
- **Technical Match** — compares JD requirements against resume skills (✓ / ⚠ / ✗ per skill)
- **Experience Match** — whether projects/internships align with the role
- **Project Relevance** — which existing projects best demonstrate the required skills
- **Qualification Match** — degree, graduation year, experience, location, internship requirements, other qualifications
- **Missing Skills** — clearly split into Strong / Partial / Missing
- **Match Explanation** — a short explanation of *why* the score is what it is, not just a number

> **Guardrail:** Scores must be computed from deterministic keyword/skill overlap (code), not "asked" from the LLM as a raw number. The LLM can generate the *explanation text*, but the *percentage* itself should come from a rules-based calculation so it's reproducible and can't drift or be hallucinated. See Section 15.

---

## 4. 💬 Referral Messages

Generates personalized referral outreach.

**LinkedIn DM** — recipient name, company, job title, candidate background, relevant skills, relevant achievements, referral request, professional sign-off.

**Email** — subject, greeting, job reference, candidate introduction, relevant experience, relevant projects, referral request, resume mention, sign-off.

**Follow-Up** — generated after a configurable delay (e.g. 3–5 days).

**Referral workflow**
```
Find employee → Generate personalized message → Send LinkedIn / Email
   → Wait 3–5 days → Follow up → Track response
```

> **Guardrail:** See Section 16 — referral messages follow the exact same anti-hallucination rules as the resume. No invented shared connections, no fabricated "I saw your work on X."

---

## 5. 🔑 ATS Keywords

Extracts keywords from the JD relevant to applicant tracking systems.

- **Technical** — Java, Spring Boot, Docker, Kubernetes, REST, Git, CI/CD
- **Tools** — Jenkins, GitHub, Kafka, PostgreSQL, Redis
- **Domain** — Testing, Automation, Backend, Distributed Systems

**Keyword analysis** — for each JD keyword, check Found in resume? → Yes / Partial / Missing (table format).

> **Guardrail:** This matching is a string/synonym lookup done in code, not an LLM judgment call — it must be 100% consistent every run.

---

## 6. ✏️ Resume Tailoring

Takes Job Description + Master Resume → produces a Job-specific Resume.

**Tailoring capabilities**
- Reorder skills
- Reorder projects
- Rewrite bullet points
- Emphasize relevant technologies
- Add relevant ATS terminology (only if already true of the candidate)
- Reduce irrelevant content
- Match JD terminology
- Preserve factual accuracy
- Preserve LaTeX formatting
- Maintain one-page constraint

**The non-negotiable constraint:**
> Never invent experience, technologies, achievements, or metrics that aren't supported by the candidate's actual background.

See Section 14–17 for exactly how this is enforced given a basic-tier LLM.

---

## 7. ✅ Checks & Validation

Validates everything before the user applies.

**Resume checks** — LaTeX syntax, formatting, missing sections, broken links, duplicate bullets, inconsistent dates, inconsistent technology names, spelling/grammar.

**JD alignment** — JD requirement → Resume evidence → Matched / Partial / Missing.

**ATS checks** — keyword coverage, keyword placement, skills section coverage, job-title alignment, relevant project coverage.

**Factual checks** — prevents the tailoring engine from introducing unsupported claims. Example: JD says Selenium, resume doesn't mention Selenium → **don't add Selenium.**

**Page-length check** — see Section 13.

---

## 8. 🎯 Extras & Actions

Final action center.

- **Application** — Apply now, Open job posting, Copy job URL
- **Referral** — Generate referral message, Copy LinkedIn message, Send email, Generate follow-up
- **Resume** — Download tailored resume, Copy LaTeX, Save tailored version, Restore master resume
- **Tracking** — Mark as applied, Mark referral requested, Mark referral received, Mark interview, Mark rejected, Mark offer

> **Guardrail:** "Send email," "Send LinkedIn message," and "Apply now" are all hard-gated behind a passed validation state (Section 7). See Section 17.

---

## 9. 🗂️ Project Bank

The candidate's reusable evidence database — store projects once instead of re-deriving them every run.

Example entry:
```
Distributed Event Processing Platform
Technologies: Kafka, Python, PostgreSQL, Docker
Skills demonstrated: Distributed systems, Event processing, Backend, Databases, Containerization
```

**Project-to-JD matching** — if JD says "Java + Docker + REST + distributed systems," the agent searches the Project Bank and finds the most relevant projects (code-based tag matching, not free-form LLM search).

---

## 10. 📁 Application Tracker

Centralized job pipeline.

```
Saved → Referral Requested → Referral Received → Applied → OA/Assessment
   → Interview → Final Round → Offer
```
Also supports: Rejected, Withdrawn, No Response.

**Job record fields** — Company, Role, Job URL, Job ID, Location, Date found, Date applied, Referral contact, Referral status, Application status, Resume version, Match score, Notes, Follow-up date.

---

## 11. 📧 Email Integration

- Connect Gmail
- Select sender account
- Enter recipient
- Generate subject
- Generate message
- Send email directly

**Follow-up automation**
```
Referral email sent → wait 3 days → no response? → generate/send follow-up
```

---

## 12. 🔍 Job URL Intelligence

```
Job URL → Fetch page → Extract JD → Clean HTML → Remove navigation/ads
   → Identify job description → Parse requirements → Create structured job
```
Should handle different job boards where technically possible.

---

## 13. 🧩 Guardrail — One-Page Resume Constraint

Hard constraint, enforced in Checks & Validation — **not** a formatting suggestion.

| Rule | Detail |
|---|---|
| Actually measure, don't estimate | Compile/render the LaTeX and check the real page count in code. A basic LLM cannot reliably judge whether text "fits" — never ask it. |
| Trim loop on overflow | If render > 1 page, automatically trigger a trim pass and re-render. Repeat until it fits or a max-attempts limit is hit (then flag for manual edit). |
| Trim by relevance, not order | Cut the lowest JD-relevance bullets/projects first, using the same code-based relevance score used for Project Bank matching — never left to LLM judgment. |
| No cosmetic cheating | Never shrink font size, margins, or line spacing below defined minimums to force a page-count pass. |
| Protected sections | Contact info, education, and the top-N most relevant projects are never removed under space pressure. |

---

## 14. 🧩 Guardrail — Content Must Map to the JD

- Every project/skill in the tailored resume must clear a minimum relevance score against JD-extracted requirements (code-computed) — no filler carried over just because it was in the master resume.
- Cap project count (e.g. top 3–4 most relevant).
- If a JD requirement has no supporting evidence anywhere in the candidate's profile, it must surface in "Missing" — never get silently papered over.

---

## 15. 🧩 Guardrail — Anti-Hallucination (Critical, Basic-LLM-Aware)

A basic-tier model **cannot be trusted to reliably follow "don't hallucinate" as a plain instruction.** The system must be designed so the model physically cannot introduce unsupported content, rather than just being told not to.

1. **Extract-then-rewrite, never generate-from-scratch.**
   Split tailoring into two small, narrow steps instead of one big open-ended prompt (a basic model does much better on small, constrained tasks):
   - Step A (code, not LLM): pull the exact candidate bullets/skills/projects relevant to this JD from the Master Resume + Project Bank.
   - Step B (LLM): rephrase/reorder *only* that retrieved text for tone and emphasis. The prompt should explicitly say "only use words/facts present in the text below — do not add any technology, tool, or number not already present."

2. **Programmatic whitelist check (not an LLM check).**
   Build a whitelist of every technology/tool/skill term from the Master Resume + Project Bank in code. After the LLM outputs tailored text, run a simple text-scan for tech-sounding terms and flag any word not in the whitelist. Don't rely on the LLM to self-report — a basic model is unreliable at that.

3. **Never let the LLM produce numbers.**
   Any metric ("reduced latency by 40%") must be copied verbatim by code from the source, never generated by the model in the rewrite step. Numbers are the highest-risk hallucination and the easiest to catch/block mechanically.

4. **Verification pass = simple diff, not a second "smart" LLM judgment.**
   Rather than asking the basic LLM to "fact-check itself" (unreliable), do a deterministic diff: every noun/tech-term/number in the output must trace back to the source text. Anything that doesn't match is auto-flagged, not silently trusted.

5. **Traceability by design.**
   Every tailored bullet keeps a pointer back to the exact master-resume/project-bank line it came from. This makes verification mechanical ("source exists? yes/no") instead of a judgment call the LLM has to make.

6. **Keep prompts short, specific, and single-purpose.**
   Basic models degrade fast on long, multi-instruction prompts. Prefer many small calls (one bullet at a time, one section at a time) over one giant "tailor the whole resume" prompt.

7. **Use temperature = 0 (or lowest available) for all factual/tailoring calls.**
   Reduces random invention. Save any higher temperature only for optional tone/style variation, never for content selection.

8. **Apply the exact same rules to referral messages and emails.**
   No invented shared connections, no fabricated "I saw your work on X," no achievements not present in the candidate's profile — enforced via the same whitelist + diff check, not LLM self-restraint.

9. **Human-in-the-loop is the final backstop, always.**
   Given a basic model, no automated check should be treated as sufficient on its own. See Section 17 — nothing goes out without a visible diff and explicit user approval.

10. **Maintain a small regression eval set.**
    Keep 5–10 JD + resume pairs with known ground truth (e.g., "JD wants Selenium, resume never mentions it — must appear in Missing, must never appear in tailored output"). Re-run this set whenever prompts change, since a basic model's behavior can shift unpredictably with small prompt edits.

---

## 16. 🧩 Guardrail — Referral Messages & Emails

Same rules as Section 15, applied to outreach text:
- Only reference achievements/skills that exist in the Project Bank / Master Resume (whitelist-checked).
- No fabricated personal connection to the recipient ("I saw your talk on...") unless the user explicitly supplied that detail.
- Contact-frequency guardrail: don't message the same referral contact repeatedly across different applications within a short window (code-enforced cooldown, not LLM judgment).

---

## 17. 🧩 Guardrail — Human Approval Gate

Because a basic LLM's output can't be fully trusted automatically:
- **Diff view required** — tailored resume vs. master resume, bullet-by-bullet, shown before download.
- **Audit trail** — every tailored bullet linked to its source line.
- **Hard action gate** — "Apply," "Send email," "Send LinkedIn message" are disabled until (a) the automated whitelist/diff checks pass, **and** (b) the user has explicitly reviewed and approved the output. Automated checks alone never unlock these actions.

---

## 18. 🧠 Personalization Engine

Maintains reusable candidate context so information isn't re-entered every time:
- Education, Skills, Projects, Internships, Achievements, Competitive programming
- Preferred roles, Preferred locations
- Resume, LinkedIn, Email

---

## 19. 🔄 Application-Specific Resume Versions

Master Resume stays untouched; each application gets its own linked, reproducible tailored version:
```
Master Resume
  ├── Headout — SDET
  ├── Amazon — SDE
  ├── Microsoft — Backend
  └── Company X — DevOps
```

---

## 20. 📈 Application Analytics

```
Applications: 42   Referrals: 18   Interviews: 7   Offers: 1
```
Breakdowns by: Company, Role, Referral vs. direct application, Resume version, Match score, Application source, Date/week/month.

---

## 21. 🔔 Reminders & Automation

- Referral sent → remind after 3 days
- Applied → remind after 7 days
- Assessment deadline → reminder before deadline

---

## 22. 🗂️ Trust & Control Layer (supports Sections 15–17)

- **Diff view** — every tailored bullet shown against its source.
- **Audit trail** — traceable source line per bullet, stored with the resume version.
- **Data export/delete** — candidate data, resume versions, sent-message history.
- **Multi-resume tracking** — which tailored version actually gets responses, tied into Application Analytics (Section 20).

---

## 23. 🧩 Suggested Navigation

```
📝 New Application
📋 Job Summary
📊 Score Dashboard
💬 Referral Messages
🔑 ATS Keywords
✏️ Resume Tailoring
✅ Checks & Validation
🎯 Extras & Actions
──────────────
🗂️ Project Bank
📁 Tracker
```
Top section = current job/application. Bottom section = reusable candidate data + application history.

---

## 24. 🤖 Agent Pipeline (Updated for Basic-LLM Guardrails)

```
                 JOB URL / JD
                       │
                       ▼
                JD Extraction (code: HTML parse)
                       │
                       ▼
                JD Normalization
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
        Requirements         ATS Keywords
             │                   │
             └─────────┬─────────┘
                       ▼
              Resume Parser (code)
                       │
                       ▼
                 Project Bank
                       │
                       ▼
           Match Analysis (code-scored)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
      Score         Referral       Resume
     Dashboard    Message (LLM,   Tailoring
   (code-scored)   whitelist-     (extract→
                    checked)      rewrite, LLM
                                  constrained to
                                  source text)
        │              │              │
        └──────────────┼──────────────┘
                       ▼
        Validation (code diff + whitelist
           + page-count check)
                       │
                       ▼
        Human Approval Gate (mandatory)
                       │
                       ▼
                 Action Center
                       │
              ┌────────┴────────┐
              ▼                 ▼
           Apply            Referral
              │                 │
              └────────┬────────┘
                       ▼
                    Tracker
```

---

## 25. End-to-End User Journey

```
1. Paste job URL
2. Fetch JD
3. Agent extracts requirements (code)
4. Compare against candidate profile (code-scored)
5. Find relevant projects (code-matched)
6. Calculate match score (code)
7. Identify ATS keywords (code)
8. Tailor resume (constrained LLM rewrite of retrieved source text only)
9. Run whitelist + diff + page-count validation (code)
10. Show diff view for human approval
11. Find referral contact
12. Generate personalized referral message (same whitelist rules)
13. Human approves outreach
14. Send email / LinkedIn message
15. Apply
16. Track application
17. Follow up automatically (within contact-frequency cooldown)
18. Track interview / offer / rejection
```

---

## In One Sentence

Job Agent is a referral-first job application copilot that takes a job description and candidate profile, analyzes the match, identifies ATS requirements, selects relevant projects, tailors a one-page resume strictly from the candidate's real background, generates referral outreach, validates every factual claim against source data before anything is sent, and tracks the entire application lifecycle — built with the explicit assumption that the underlying LLM is basic-tier and cannot be trusted to self-police hallucination, so every safety-critical check is deterministic code plus mandatory human approval rather than model judgment.
