import { Avatar, Badge, EmptyState, Icon, band, pretty, timeAgo } from "../components/ui";

const STAGE = (j) =>
  j.approved_at
    ? ["Resume ready", "ok"]
    : j.tailored && j.gate_ok === 0
    ? ["Blocked", "bad"]
    : j.tailored
    ? ["Needs review", "warn"]
    : j.match_score != null
    ? ["Analysed", "accent"]
    : ["Added", ""];

export default function Home({ jobs, onOpen, onNew }) {
  const scored = jobs.filter((j) => j.match_score != null);
  const avg = scored.length
    ? Math.round(scored.reduce((a, j) => a + j.match_score, 0) / scored.length)
    : null;
  const ready = jobs.filter((j) => j.approved_at).length;
  const applied = jobs.filter((j) =>
    ["applied", "assessment", "interview", "final_round", "offer"].includes(j.status)
  ).length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Applications</h1>
          <p>Every job you are tailoring a resume for.</p>
        </div>
        <button className="btn primary" onClick={onNew}>
          <Icon name="plus" size={16} stroke={2.2} /> New application
        </button>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 24 }}>
        <Stat k="Applications" v={jobs.length} />
        <Stat k="Average match" v={avg == null ? "—" : `${avg}%`} />
        <Stat k="Resumes ready" v={ready} />
        <Stat k="Applied" v={applied} />
      </div>

      <div className="card">
        {jobs.length === 0 ? (
          <EmptyState
            icon="briefcase"
            title="No applications yet"
            action={
              <button className="btn primary" onClick={onNew}>
                <Icon name="plus" size={16} /> Add your first job
              </button>
            }
          >
            Paste a job description and Job Agent scores your match, then
            tailors your resume from your real experience and GitHub projects.
          </EmptyState>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Company & role</th>
                <th style={{ width: 110 }}>Match</th>
                <th style={{ width: 150 }}>Resume</th>
                <th style={{ width: 140 }}>Status</th>
                <th style={{ width: 110 }}>Updated</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const [label, tone] = STAGE(j);
                return (
                  <tr key={j.id} className="click" onClick={() => onOpen(j.id)}>
                    <td>
                      <div className="row" style={{ gap: 12, flexWrap: "nowrap" }}>
                        <Avatar name={j.company} />
                        <div style={{ minWidth: 0 }}>
                          <div className="strong ellip">{j.company || "Untitled"}</div>
                          <div className="xs muted ellip">
                            {j.title || "—"}
                            {j.location ? ` · ${j.location}` : ""}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      {j.match_score != null ? (
                        <Badge tone={band(j.match_score)}>{j.match_score}%</Badge>
                      ) : (
                        <span className="subtle">—</span>
                      )}
                    </td>
                    <td><Badge tone={tone} dot>{label}</Badge></td>
                    <td className="sm muted">{pretty(j.status)}</td>
                    <td className="xs subtle">{timeAgo(j.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Stat({ k, v }) {
  return (
    <div className="card stat-card">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}
