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
            <div className="sub ellip">
              {job.title || "Role not detected yet"}
              {job.location ? ` · ${job.location}` : ""}
              {job.external_id ? ` · ${job.external_id}` : ""}
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
