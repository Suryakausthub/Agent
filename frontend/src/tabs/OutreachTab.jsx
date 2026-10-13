import { useState } from "react";
import { api } from "../api";
import { Alert, Badge, EmptyState, Icon } from "../components/ui";

const KINDS = [
  ["linkedin", "LinkedIn DM", 300],
  ["email", "Email", 1400],
  ["followup", "Follow-up", 600],
];

export default function OutreachTab({ job, run, busy, notify }) {
  const stored = job.referral_json || {};
  const [kind, setKind] = useState("linkedin");
  const [recipient, setRecipient] = useState("");
  const [role, setRole] = useState("");
  const [facts, setFacts] = useState("");

  const msg = stored[kind];
  const limit = KINDS.find(([k]) => k === kind)[2];

  async function copy(text, what) {
    try {
      await navigator.clipboard.writeText(text);
      notify(`${what} copied`);
    } catch {
      notify("Couldn't access the clipboard", "bad");
    }
  }

  return (
    <div className="split" style={{ gridTemplateColumns: "360px minmax(0, 1fr)" }}>
      <div className="card card-pad stack">
        <div>
          <h2 style={{ marginBottom: 4 }}>Ask for a referral</h2>
          <div className="xs muted">
            Written in your voice from your real resume. Nothing about the
            recipient is invented - only what you type below is used.
          </div>
        </div>
        <div className="segmented" style={{ alignSelf: "flex-start" }}>
          {KINDS.map(([k, label]) => (
            <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>
              {label}{stored[k] ? " ✓" : ""}
            </button>
          ))}
        </div>
        <div>
          <label className="label">Their name</label>
          <input className="input" placeholder="e.g. Priya" value={recipient} onChange={(e) => setRecipient(e.target.value)} />
        </div>
        <div>
          <label className="label">Their role <span className="subtle">(optional)</span></label>
          <input className="input" placeholder="e.g. Engineering Manager" value={role} onChange={(e) => setRole(e.target.value)} />
        </div>
        <div>
          <label className="label">Something true about them <span className="subtle">(optional)</span></label>
          <textarea className="textarea" style={{ minHeight: 80 }}
            placeholder="A mutual contact, a talk they gave, where you met…"
            value={facts} onChange={(e) => setFacts(e.target.value)} />
        </div>
        <button className="btn primary" disabled={!!busy || !recipient.trim()}
          onClick={() => run("referral", () => api.referral(job.id, {
            kind, recipient, recipient_role: role, supplied_facts: facts,
          }), "Message written")}>
          <Icon name="sparkles" size={16} />
          {busy === "referral" ? "Writing…" : msg ? "Rewrite message" : "Write message"}
        </button>
      </div>

      <div className="stack">
        {!msg ? (
          <div className="card">
            <EmptyState icon="message" title="No message yet">
              Enter who you're writing to and press Write message.
            </EmptyState>
          </div>
        ) : (
          <div className="card">
            <div className="card-head">
              <div className="row" style={{ gap: 8 }}>
                <h3>{KINDS.find(([k]) => k === kind)[1]}</h3>
                <Badge tone={msg.body.length > limit ? "warn" : ""}>{msg.body.length} / {limit} chars</Badge>
                {!msg.ok && <Badge tone="bad">{msg.reason}</Badge>}
              </div>
              <div className="row">
                {msg.subject && (
                  <button className="btn sm" onClick={() => copy(msg.subject, "Subject")}>
                    <Icon name="copy" size={14} /> Subject
                  </button>
                )}
                <button className="btn sm" onClick={() => copy(msg.body, "Message")}>
                  <Icon name="copy" size={14} /> Copy
                </button>
                {kind === "email" ? (
                  <a className="btn primary sm" href={`mailto:?subject=${encodeURIComponent(msg.subject || "")}&body=${encodeURIComponent(msg.body)}`}>
                    <Icon name="external" size={14} /> Open in email
                  </a>
                ) : (
                  <a className="btn primary sm" href="https://www.linkedin.com/messaging/" target="_blank" rel="noreferrer"
                    onClick={() => copy(msg.body, "Message")}>
                    <Icon name="external" size={14} /> Copy & open LinkedIn
                  </a>
                )}
              </div>
            </div>
            {msg.subject && (
              <div className="card-body" style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="xs subtle">Subject</div>
                <div className="strong">{msg.subject}</div>
              </div>
            )}
            <div className="card-body msg-box">{msg.body}</div>
          </div>
        )}

        {msg?.violations?.length > 0 && (
          <Alert tone="warn" title="Flagged for your review">
            {msg.violations.map((v) => `${v.value} (${v.detail})`).join("; ")}
          </Alert>
        )}
        {kind === "linkedin" && msg && (
          <div className="xs subtle">
            LinkedIn has no API for messages, so you paste and send it yourself.
          </div>
        )}
      </div>
    </div>
  );
}
