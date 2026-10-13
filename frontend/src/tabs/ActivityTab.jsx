import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Icon, pretty } from "../components/ui";

const TRACK = [
  ["applied", "Applied"],
  ["referral_requested", "Referral requested"],
  ["referral_received", "Referral received"],
  ["assessment", "Assessment"],
  ["interview", "Interview"],
  ["final_round", "Final round"],
  ["offer", "Offer"],
  ["rejected", "Rejected"],
];

export default function ActivityTab({ job, run, busy, onDeleted }) {
  const [trail, setTrail] = useState([]);
  const [notes, setNotes] = useState(job.notes || "");
  const [saved, setSaved] = useState(true);

  useEffect(() => {
    api.audit(job.id).then((r) => setTrail(r.trail)).catch(() => {});
  }, [job.id, job.updated_at]);

  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0, 1fr) 380px" }}>
      <div className="stack">
        <div className="card card-pad">
          <h3>Where is this application?</h3>
          <div className="row" style={{ gap: 6 }}>
            {TRACK.map(([s, label]) => (
              <button key={s} className={"btn sm " + (job.status === s ? "primary" : "")}
                disabled={!!busy} onClick={() => run("status", () => api.setStatus(job.id, s))}>
                {label}
              </button>
            ))}
          </div>
          {job.source_url && (
            <div className="xs muted" style={{ marginTop: 12 }}>
              <a href={job.source_url} target="_blank" rel="noreferrer">
                Open the original posting <Icon name="external" size={12} />
              </a>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head"><h3>History</h3></div>
          <table className="table">
            <tbody>
              {trail.slice().reverse().map((e, i) => (
                <tr key={i}>
                  <td className="xs subtle nowrap" style={{ width: 150 }}>
                    {e.ts.replace("T", " ").slice(0, 16)}
                  </td>
                  <td style={{ width: 150 }}>
                    <Badge tone={e.event === "approved" || e.event === "downloaded" ? "ok"
                      : e.event.includes("invalid") ? "bad" : ""}>{pretty(e.event)}</Badge>
                  </td>
                  <td className="sm muted">{e.detail}</td>
                </tr>
              ))}
              {trail.length === 0 && (
                <tr><td className="sm muted">Nothing recorded yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="stack">
        <div className="card card-pad">
          <h3>Notes</h3>
          <textarea className="textarea" style={{ minHeight: 150 }} value={notes}
            placeholder="Referral contact, follow-up dates, interview feedback…"
            onChange={(e) => { setNotes(e.target.value); setSaved(false); }} />
          <div className="between" style={{ marginTop: 10 }}>
            <span className="xs subtle">{saved ? "Saved" : "Unsaved changes"}</span>
            <button className="btn sm" disabled={saved}
              onClick={async () => { await api.setNotes(job.id, notes); setSaved(true); }}>
              Save notes
            </button>
          </div>
        </div>

        <div className="card card-pad">
          <h3>Delete application</h3>
          <div className="xs muted" style={{ marginBottom: 12 }}>
            Removes this job, its analysis, tailored resume and history.
          </div>
          <button className="btn danger sm" disabled={!!busy} onClick={async () => {
            if (!confirm("Delete this application permanently?")) return;
            await api.deleteJob(job.id);
            await onDeleted();
          }}>
            <Icon name="trash" size={14} /> Delete
          </button>
        </div>
      </div>
    </div>
  );
}
