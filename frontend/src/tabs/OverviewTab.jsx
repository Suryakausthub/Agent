import { EmptyState, Meter, ScoreRing, band } from "../components/ui";

const PARTS = [
  ["required_skills", "Required skills", "required_score"],
  ["preferred_skills", "Preferred skills", "preferred_score"],
  ["project_relevance", "Project relevance", "project_score"],
  ["qualifications", "Qualifications", "qualification_score"],
];

export default function OverviewTab({ job }) {
  const s = job.score_json;
  const jd = job.jd_json;
  if (!s || !jd) {
    return <EmptyState icon="chart" title="Not analysed yet">Press Analyse job to score your match.</EmptyState>;
  }
  const scoreable = jd.requirements.filter((r) => r.keys.length);
  const unscored = jd.requirements.filter((r) => !r.keys.length);

  return (
    <div className="stack">
      <div className="grid cols-2">
        <div className="card card-pad">
          <div className="row" style={{ gap: 24, flexWrap: "nowrap", alignItems: "center" }}>
            <ScoreRing value={s.overall} />
            <div className="stack" style={{ gap: 12, flex: 1, minWidth: 0 }}>
              {PARTS.map(([wk, label, sk]) => {
                const raw = s[sk] ?? 0;
                const w = s.weights?.[wk] ?? 0;
                return (
                  <div key={wk}>
                    <div className="between xs" style={{ marginBottom: 5 }}>
                      <span className="strong">{label}</span>
                      <span className="muted">{Math.round(raw * 100)}% · weight {Math.round(w * 100)}%</span>
                    </div>
                    <Meter value={raw} tone={band(raw * 100)} />
                  </div>
                );
              })}
            </div>
          </div>
          <div className="xs subtle" style={{ marginTop: 14 }}>
            Calculated in code from skill overlap - the same job and resume always give the same score.
          </div>
        </div>

        <div className="card card-pad stack" style={{ gap: 14 }}>
          <Skills title="You have" items={s.strong} tone="ok" />
          <Skills title="Partly / implied" items={s.partial} tone="warn" />
          <Skills title="Missing" items={s.missing} tone="bad" />
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <div className="card-head"><h3>Role details</h3></div>
          <div className="card-body">
            <dl className="kv">
              {[
                ["Company", jd.company], ["Role", jd.title], ["Location", jd.location],
                ["Type", jd.employment_type], ["Level", jd.level],
                ["Experience", jd.qualifications?.min_experience_years],
                ["Degree", jd.qualifications?.degree],
              ].filter(([, v]) => v).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt><dd>{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>

        <div className="card">
          <div className="card-head"><h3>Your projects vs this job</h3></div>
          <div className="card-body">
            {s.projects.slice(0, 5).map((p) => (
              <div key={p.entry_id} style={{ marginBottom: 12 }}>
                <div className="between sm" style={{ marginBottom: 4 }}>
                  <span className="ellip">
                    {p.title}{" "}
                    {p.entry_id.startsWith("bank.") && <span className="badge accent" style={{ marginLeft: 4 }}>GitHub</span>}
                  </span>
                  <span className="muted nowrap">{Math.round(p.score * 100)}%</span>
                </div>
                <Meter value={p.score} />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Requirements</h3>
          <span className="xs muted">{scoreable.length} scored{unscored.length ? ` · ${unscored.length} not skill-based` : ""}</span>
        </div>
        <table className="table">
          <tbody>
            {s.skills.map((v, i) => (
              <tr key={i}>
                <td style={{ width: 110 }}>
                  <span className={"badge " + (v.status === "Strong" ? "ok" : v.status === "Partial" ? "warn" : "bad")}>{v.status}</span>
                </td>
                <td className="sm">
                  {v.options.join(" / ")}
                  {v.implied && <span className="subtle"> · implied by your experience</span>}
                </td>
                <td className="xs muted" style={{ width: 90 }}>{v.kind}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {unscored.length > 0 && (
          <div className="card-body" style={{ borderTop: "1px solid var(--border)" }}>
            <details className="more">
              <summary>Requirements without a named skill (not scored)</summary>
              <ul className="list sm muted" style={{ marginTop: 10 }}>
                {unscored.map((r, i) => <li key={i}>{r.text}</li>)}
              </ul>
            </details>
          </div>
        )}
      </div>

      {s.qualifications?.length > 0 && (
        <div className="card">
          <div className="card-head"><h3>Qualifications</h3></div>
          <table className="table">
            <thead><tr><th>Check</th><th>Wanted</th><th>You</th><th style={{ width: 110 }}>Verdict</th></tr></thead>
            <tbody>
              {s.qualifications.map((q, i) => (
                <tr key={i}>
                  <td className="strong sm">{q.name}</td>
                  <td className="sm muted">{q.expected}</td>
                  <td className="sm">{q.found}</td>
                  <td><span className={"badge " + (q.status === "Match" ? "ok" : "warn")}>{q.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Skills({ title, items, tone }) {
  return (
    <div>
      <div className="xs strong muted" style={{ marginBottom: 7 }}>
        {title} <span className="subtle">({items?.length || 0})</span>
      </div>
      <div className="row" style={{ gap: 6 }}>
        {items?.length ? items.map((t, i) => <span key={i} className={"chip " + tone}>{t}</span>)
          : <span className="xs subtle">none</span>}
      </div>
    </div>
  );
}
