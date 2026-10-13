# Job Agent

A referral-first job application copilot. Takes a job description and your
master LaTeX resume, scores the match deterministically, tailors the resume
strictly from your real background, drafts referral outreach, and blocks
anything from leaving until every claim traces back to evidence you actually
have.

---

## Running it

```cmd
run.cmd
```

One command, one server, one port. Double-click it or run it from a terminal
in this folder. It builds the UI if needed, frees the port if a previous run
is still holding it, starts the server, and opens the browser at:

**http://localhost:8000**

Ctrl+C in that terminal stops it. Pass a different port if 8000 is taken:
`run.cmd 8123`

> **Use `run.cmd`, not `run.ps1`.** PowerShell's default execution policy
> (`Restricted`) blocks unsigned `.ps1` scripts. Batch files are exempt, so
> this needs no system changes.

FastAPI serves both the API and the built React UI from the same process, so
there is no second server and no proxy. API docs: http://localhost:8000/docs

### Working on the frontend

Only needed if you are editing the UI and want hot reload. Two terminals:

```cmd
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
```
```cmd
cd frontend
npm run dev
```

Then use **http://localhost:5173** (Vite proxies `/api` to port 8000). Note
Vite binds to `localhost` only, not `127.0.0.1`.

Run `npm run build` when done, so `run.cmd` picks up your changes.

### "Port already in use"

`run.cmd` clears it automatically. To do it by hand:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  Select-Object -Expand OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```

---

## First-time setup

Already done in this checkout, but if you move it to another machine:

```cmd
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
cd frontend && npm install && cd ..
copy .env.example .env
:: then put your Groq key in .env
```

Requires Python 3.12+ (from python.org, **not** the Microsoft Store build —
the Store version causes venv and PATH problems) and Node 18+.

Two things are deliberately **not in the repository**:

- **`main.tex`** — your master resume (it contains your phone number). Put
  your own LaTeX resume at the project root as `main.tex`.
- **Tectonic** (~50 MB) — download the Windows build and unzip it to
  `tools\tectonic\tectonic.exe`:

  ```powershell
  New-Item -ItemType Directory -Force tools\tectonic | Out-Null
  Invoke-WebRequest "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.15.0/tectonic-0.15.0-x86_64-pc-windows-msvc.zip" -OutFile tools\tectonic\t.zip
  Expand-Archive tools\tectonic\t.zip tools\tectonic; Remove-Item tools\tectonic\t.zip
  ```

  Without it the app still runs, but page counts are estimated and PDF
  download is unavailable.

Your local data — `.env`, `job_agent.db`, generated PDFs and caches — is
ignored by git as well.

---

## Using it

1. **New application** — paste the job description (or a Workday link) and
   press **Analyse job**. Analysis starts straight away: match score, skills
   you have / are missing, ATS keyword coverage.
2. **Tailor resume** — picks your strongest projects (from `main.tex` and your
   GitHub library, at most 3 bullets each), rewrites bullets around the job,
   verifies every claim, and builds a real one-page PDF.
3. **Resume tab** — every change shown against its source, next to a live PDF
   preview (watermarked DRAFT until you approve). **Approve**, then
   **Download PDF** (or the `.tex` / Copy LaTeX for Overleaf).
4. **Outreach tab** — referral message for LinkedIn, email or a follow-up.

The progress line at the top of every application always shows where the
resume is: while running, the live stage and a percentage (tailoring counts
up bullet by bullet); otherwise how far along it is — Added, Analysed,
Tailored, Approved.

### Nothing leaves without two gates

Download and send are refused unless **both** the automated checks passed
**and** you approved the diff. Approval is bound to a content hash, so
re-tailoring automatically revokes it — you can never approve one version and
download another.

---

## Configuration

`backend/data/config.yaml`:

| Key | What it does |
|---|---|
| `llm.model` | `openai/gpt-oss-120b` on Groq. `verify_models` checks at startup that both it and the fallback are reachable on your key. |
| `scoring.weights` | 50% required skills / 20% preferred / 20% project relevance / 10% qualifications. Must sum to 1.0. |
| `resume.protected_sections` | Never trimmed: Education, Certifications & Achievements, Research Publications. |
| `resume.trim_order` | What gets cut first when the page overflows. |
| `candidate` | Your name, email and standing, used to sign referral messages. |
| `compile.backend` | `tectonic` (default: real PDF, measured page count) or `stub` (estimate). `fallback_to_stub` uses the estimate, labelled as such, if Tectonic cannot run. |

`backend/data/vocabulary.yaml` — 111 skill terms with aliases and implication
rules, hand-editable. Regenerate the starter with
`.venv\Scripts\python backend\scripts\gen_vocabulary.py` (this overwrites
manual edits; put lasting additions in `backend/app/core/vocab_seed.py`).

Your master resume is `main.tex` in this folder. Edit it on Overleaf as usual —
the agent parses it, never rewrites it, and splices tailored output into a copy.

---

## Project Knowledge Base

Your GitHub projects are analysed **once** and stored as structured records
(SQLite, `projects.project_json`), so tailoring never rescans repositories.

```cmd
:: build records for every KEEP/REVIEW repo that lacks one (resumable)
.venv\Scripts\python backend\scripts\build_knowledge_base.py
:: later: pick up repos you pushed to since their record was built
.venv\Scripts\python backend\scripts\build_knowledge_base.py --sync
```

Each record holds the description and problem, architecture and components,
a categorised tech stack, implementation details marked
implemented / partial / planned, key contributions, **ten candidate resume
bullets** (each with a focus area and the files that support it), keywords,
and the **gaps** the evidence could not settle. Code checks every record
against the repository: technologies absent from the repo are dropped, bullets
naming one are rejected, numbers survive only if they trace to a measurable
claim in the repo, and near-duplicate bullets are removed.

Every analysed project is **used automatically**. In the **Project library**
you can mark any project *Don't use*. Projects whose authorship looks
uncertain (a fork, a downloaded `-master` archive, a `go.mod` owned by someone
else) stay off your resume until you press *Use anyway*
(`project_bank.exclude_flagged`). Set `project_bank.require_approval: true` to
go back to approving every project by hand. Hand edits are never overwritten
by a rebuild unless you pass `--force`.

Per job, the selector ranks your `main.tex` projects and approved
knowledge-base projects together, then one model call picks the projects and
**at most three bullets per project** (each adding a different strength). The
model returns ids only — it cannot write text — and code validates every id,
falling back to a code ranking if the call fails. Chosen bullets then go
through the normal rewrite → whitelist → diff → approval path.

---

## Known limitations

- **Page count is measured with Tectonic** (`tools/tectonic/tectonic.exe`),
  which compiles with XeTeX rather than Overleaf's pdfLaTeX. The compiled copy
  drops the pdfTeX-only `\input{glyphtounicode}` line and maps `★` to
  `$\star$`; `main.tex` itself is unchanged. Your master measures exactly one
  page, matching Overleaf, but line breaks can differ very slightly between the
  two engines. The first run downloads LaTeX packages; later runs are offline.
- **Job postings close.** `t_live`, `t_paste` and `t_stability` default to a
  Postman posting that has since closed; pass a current Workday URL to
  `t_live`. `t_paste` and `t_stability` skip only their fetched-vs-pasted
  comparison when the posting is gone.
- **LinkedIn cannot send.** No API exists for DMs and automating it risks your
  account. Messages are generated for you to copy and send.
- **Scoring is sensitive to JD text changes.** Extraction is deterministic for
  identical input, but the model segments prose into requirements differently
  when the text changes slightly, which moves the score. Extraction is
  therefore cached per application, so a given job's score never moves once
  computed. Use `?force=true` on `/analyse` to deliberately re-extract.

---

## Tests

```cmd
:: offline
.venv\Scripts\python backend\tests\t_parse.py      :: LaTeX parser vs main.tex
.venv\Scripts\python backend\tests\t_vocab.py      :: term extraction, implications
.venv\Scripts\python backend\tests\t_pagefit.py    :: page-budget calibration (estimate)
.venv\Scripts\python backend\tests\t_compile.py    :: Tectonic PDF, measured pages, fallback
.venv\Scripts\python backend\tests\t_kb.py         :: knowledge base checks, selection, render, score
:: real model calls (a fraction of a cent each)
.venv\Scripts\python backend\tests\t_llm.py        :: key works, number masking
.venv\Scripts\python backend\tests\t_score.py      :: JD extraction -> score
.venv\Scripts\python backend\tests\t_tailor.py     :: rewrite + claim check (incl. injected fake skills)
.venv\Scripts\python backend\tests\t_render.py     :: render + trim + re-parse
.venv\Scripts\python backend\tests\t_api.py        :: full API incl. approval gate
.venv\Scripts\python backend\tests\t_paste.py      :: pasted JD through the API
.venv\Scripts\python backend\tests\t_stability.py  :: identical input -> identical score
.venv\Scripts\python backend\tests\t_live.py <url> :: fetch a live Workday posting end to end
```

`t_api`, `t_paste` and `t_live` create jobs in `job_agent.db`; delete them
from the Tracker afterwards, or back the file up first. Groq's free tier allows
8,000 tokens a minute, so leave a minute between the model-backed tests.
