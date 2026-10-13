import { useState } from "react";
import { Alert, Badge, EmptyState, Meter, ScoreRing } from "../components/ui";

export default function KeywordsTab({ job }) {
  const a = job.ats_json;
  const [filter, setFilter] = useState("all");
  if (!a) return <EmptyState icon="key" title="Not analysed yet" />;

  const order = { Found: 0, Partial: 1, Missing: 2 };
  const rows = [...a.rows]
    .filter((r) => filter === "all" || r.status === filter)
    .sort((x, y) => order[x.status] - order[y.status] || y.jd_hits - x.jd_hits);
  const count = (s) => a.rows.filter((r) => r.status === s).length;

  return (
    <div className="stack">
      <div className="grid cols-2">
        <div className="card card-pad row" style={{ gap: 24, flexWrap: "nowrap" }}>
          <ScoreRing value={Math.round(a.coverage * 100)} caption="coverage" size={116} />
          <div className="stack" style={{ gap: 10, flex: 1 }}>
            <div className="sm muted">
              Share of the job's keywords that appear in your resume exactly.
              Counting partial matches: <strong>{Math.round(a.weighted_coverage * 100)}%</strong>.
            </div>
            {Object.entries(a.by_bucket).map(([k, [found, total]]) => (
              <div key={k}>
                <div className="between xs" style={{ marginBottom: 4 }}>
                  <span className="strong" style={{ textTransform: "capitalize" }}>{k}</span>
                  <span className="muted">{found} / {total}</span>
                </div>
                <Meter value={total ? found / total : 0} />
              </div>
            ))}
          </div>
        </div>
        <div className="stack" style={{ gap: 12 }}>
          {a.placement_warnings?.length > 0 ? (
            a.placement_warnings.map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)
          ) : (
            <Alert tone="ok" title="Keywords are where parsers look">
              Your matched skills appear in the Skills section, which most ATS weight highest.
            </Alert>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Keywords</h3>
          <div className="segmented">
            {[["all", `All ${a.rows.length}`], ["Found", `Found ${count("Found")}`],
              ["Partial", `Partial ${count("Partial")}`], ["Missing", `Missing ${count("Missing")}`]].map(([k, l]) => (
              <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{l}</button>
            ))}
          </div>
        </div>
        <table className="table">
          <thead>
            <tr><th style={{ width: 100 }}>Status</th><th>Keyword</th><th style={{ width: 90 }}>In JD</th><th>Where it appears in your resume</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td><Badge tone={r.status === "Found" ? "ok" : r.status === "Partial" ? "warn" : "bad"}>{r.status}</Badge></td>
                <td className="strong sm">{r.canonical}</td>
                <td className="sm muted">{r.jd_hits}×</td>
                <td className="sm muted">
                  {r.locations?.length ? r.locations.join(", ") : r.implied_by ? `implied by ${r.implied_by}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
