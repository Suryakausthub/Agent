import { useState } from "react";
import { api } from "../api";
import { Alert, Icon } from "../components/ui";

export default function NewApplication({ onCreated, notify }) {
  const [mode, setMode] = useState("paste");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function submit() {
    setBusy(true);
    setNotice("");
    try {
      const r = await api.createJob(mode === "paste" ? { raw_jd: text } : { url });
      await onCreated(r.job_id);
    } catch (e) {
      // Most boards render in JavaScript and cannot be fetched; steer to paste.
      if (mode === "url") {
        setMode("paste");
        setNotice(e.message);
      } else {
        notify(e.message, "bad");
      }
    } finally {
      setBusy(false);
    }
  }

  const words = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>New application</h1>
          <p>Add the job description. Analysis starts automatically.</p>
        </div>
      </div>

      <div className="card card-pad stack">
        <div className="segmented" style={{ alignSelf: "flex-start" }}>
          <button className={mode === "paste" ? "on" : ""} onClick={() => setMode("paste")}>
            Paste description
          </button>
          <button className={mode === "url" ? "on" : ""} onClick={() => setMode("url")}>
            Job link
          </button>
        </div>

        {notice && (
          <Alert tone="warn" title="Couldn't fetch that link - paste the description instead">
            {notice}
          </Alert>
        )}

        {mode === "paste" ? (
          <div>
            <label className="label">Job description</label>
            <textarea
              className="textarea"
              style={{ minHeight: 320 }}
              placeholder="Paste the full job description - title, responsibilities, requirements…"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <div className="hint">
              {words} words
              {words > 0 && words < 80 && " · that looks short - include the requirements section"}
            </div>
          </div>
        ) : (
          <div>
            <label className="label">Job posting link</label>
            <input
              className="input"
              placeholder="https://jobs.lever.co/company/…  ·  job-boards.greenhouse.io/…  ·  jobs.ashbyhq.com/…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <div className="hint">
              Works with <strong>Workday</strong>, <strong>Greenhouse</strong>,{" "}
              <strong>Lever</strong> and <strong>Ashby</strong> - including
              company career pages powered by them (links with{" "}
              <code>gh_jid=</code> or <code>ashby_jid=</code>). LinkedIn and
              most other sites can't be fetched - paste those instead.
            </div>
          </div>
        )}

        <div className="between">
          <span className="xs subtle">
            Next: your match score, keyword coverage, then a tailored resume.
          </span>
          <button
            className="btn primary lg"
            disabled={busy || (mode === "paste" ? text.trim().length < 50 : !url.startsWith("http"))}
            onClick={submit}
          >
            {busy ? "Starting…" : <>Analyse job <Icon name="chevronRight" size={16} /></>}
          </button>
        </div>
      </div>
    </div>
  );
}
