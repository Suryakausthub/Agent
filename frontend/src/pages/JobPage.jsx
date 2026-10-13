import { useState } from "react";
import { api, downloadFile, STATUSES } from "../api";
import ProgressLine from "../components/ProgressLine";
import { Avatar, Badge, Icon, band, pretty } from "../components/ui";
import OverviewTab from "../tabs/OverviewTab";
import ResumeTab from "../tabs/ResumeTab";
import KeywordsTab from "../tabs/KeywordsTab";
import OutreachTab from "../tabs/OutreachTab";
import ChecksTab from "../tabs/ChecksTab";
import ActivityTab from "../tabs/ActivityTab";

const TABS = [
  ["overview", "Overview", "chart", "score_json"],
  ["resume", "Resume", "file", "diff_json"],
  ["keywords", "Keywords", "key", "ats_json"],
  ["outreach", "Outreach", "message", "jd_json"],
  ["checks", "Checks", "shield", "jd_json"],
  ["activity", "Activity", "activity", null],
];

export default function JobPage({ job, busy, run, notify, onBack, onDeleted }) {
  const [tab, setTab] = useState(job.diff_json ? "resume" : "overview");
  const [editing, setEditing] = useState(false);
  const working = busy === "analyse" || busy === "tailor";

  // The single "what should I do next" action.
  const next = !job.score_json
    ? { label: "Analyse job", icon: "sparkles", key: "analyse",
        act: () => run("analyse", () => api.analyse(job.id), "Analysis complete") }
    : !job.diff_json
    ? { label: "Tailor resume", icon: "sparkles", key: "tailor",
        act: async () => { await run("tailor", () => api.tailor(job.id), "Resume tailored - review it below"); setTab("resume"); } }
    : !job.approved_at
    ? { label: "Review & approve", icon: "eye", key: "review", act: () => setTab("resume") }
    : { label: "Download PDF", icon: "download", key: "pdf",
        act: () => downloadFile(api.pdfUrl(job.id), "application/pdf")
          .then((n) => notify(`Downloaded ${n}`))
          .catch((e) => notify(e.message, "bad")) };

  return (
    <div className="page">
      <div className="back" onClick={onBack}>
        <Icon name="chevronLeft" size={15} /> Applications
      </div>

      <div className="job-head">
        <div className="job-title">
          <Avatar name={job.company} size="lg" />
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 10 }}>
              <h1 className="ellip">{job.company || "Untitled"}</h1>
              {job.match_score != null && (
                <Badge tone={band(job.match_score)}>{job.match_score}% match</Badge>
              )}
            </div>
            <div className="sub row" style={{ gap: 6, flexWrap: "nowrap" }}>
              <span className="ellip">
                {job.title || "Role not set"}
                {job.location ? ` · ${job.location}` : ""}
                {job.external_id ? ` · ${job.external_id}` : ""}
              </span>
              <button className="btn ghost sm" title="Edit company, role and location"
                onClick={() => setEditing((e) => !e)} style={{ height: 24, padding: "0 6px" }}>
                <Icon name="edit" size={13} />
              </button>
            </div>
          </div>
        </div>

        <div className="row">
          <select
            className="select"
            value={job.status}
            title="Application status"
            onChange={(e) => run("status", () => api.setStatus(job.id, e.target.value))}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>{pretty(s)}</option>
            ))}
          </select>
          {job.score_json && (
            <button
              className="btn"
              disabled={!!busy}
              title="Re-run tailoring"
              onClick={() => run("tailor", () => api.tailor(job.id), "Resume re-tailored")}
            >
              <Icon name="refresh" size={15} /> {job.diff_json ? "Re-tailor" : "Tailor"}
            </button>
          )}
          <button className="btn primary" disabled={!!busy} onClick={next.act}>
            <Icon name={next.icon} size={16} />
            {busy === next.key ? "Working…" : next.label}
          </button>
        </div>
      </div>

      {(editing || (job.score_json && !working && (!job.title || !job.company))) && (
        <DetailsForm job={job} run={run} onDone={() => setEditing(false)}
          asked={!editing} />
      )}

      <ProgressLine job={job} busy={busy} />

      <div className="tabs">
        {TABS.map(([id, label, icon, need]) => {
          const ok = !need || !!job[need];
          return (
            <div
              key={id}
              className={"tab " + (tab === id ? "on " : "") + (ok ? "" : "off")}
              title={ok ? label : working ? "Available when this run finishes" : "Not available yet"}
              onClick={() => ok && setTab(id)}
            >
              <Icon name={icon} size={15} /> {label}
            </div>
          );
        })}
      </div>

      {tab === "overview" && <OverviewTab job={job} />}
      {tab === "resume" && <ResumeTab job={job} run={run} busy={busy} notify={notify} />}
      {tab === "keywords" && <KeywordsTab job={job} />}
      {tab === "outreach" && <OutreachTab job={job} run={run} busy={busy} notify={notify} />}
      {tab === "checks" && <ChecksTab job={job} />}
      {tab === "activity" && <ActivityTab job={job} run={run} busy={busy} onDeleted={onDeleted} />}
    </div>
  );
}

/**
 * Company / role / location. Shown unprompted when the analysis couldn't find
 * them - extraction only keeps a title that actually appears in the posting,
 * so a description pasted without its heading has none - and on demand via
 * the pencil next to the title.
 */
function DetailsForm({ job, run, onDone, asked }) {
  const [company, setCompany] = useState(job.company || "");
  const [title, setTitle] = useState(job.title || "");
  const [location, setLocation] = useState(job.location || "");
  const missing = [!job.title && "role", !job.company && "company"].filter(Boolean);

  async function save() {
    await run("details", () => api.setDetails(job.id, { company, title, location }), "Details saved");
    onDone();
  }

  return (
    <div className={"alert " + (asked ? "warn" : "")} style={{ marginBottom: 20, display: "block" }}>
      <div className="strong" style={{ marginBottom: 4 }}>
        {asked
          ? `The ${missing.join(" and ")} isn't in the description you pasted - add it`
          : "Edit job details"}
      </div>
      {asked && (
        <div className="xs muted" style={{ marginBottom: 12 }}>
          It's used in your applications list, the PDF file name and your referral messages.
        </div>
      )}
      <div className="grid cols-3" style={{ gap: 10, marginTop: asked ? 0 : 10 }}>
        <div>
          <label className="label">Role</label>
          <input className="input" value={title} placeholder="e.g. Software Engineer"
            onChange={(e) => setTitle(e.target.value)} autoFocus={!job.title} />
        </div>
        <div>
          <label className="label">Company</label>
          <input className="input" value={company} onChange={(e) => setCompany(e.target.value)} />
        </div>
        <div>
          <label className="label">Location <span className="subtle">(optional)</span></label>
          <input className="input" value={location} onChange={(e) => setLocation(e.target.value)} />
        </div>
      </div>
      <div className="row" style={{ marginTop: 12, justifyContent: "flex-end" }}>
        {!asked && <button className="btn sm" onClick={onDone}>Cancel</button>}
        <button className="btn primary sm" disabled={!title.trim() || !company.trim()} onClick={save}>
          Save
        </button>
      </div>
    </div>
  );
}
