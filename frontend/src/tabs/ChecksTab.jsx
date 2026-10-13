import { useEffect, useState } from "react";
import { api } from "../api";
import { Alert, Badge, Icon } from "../components/ui";

export default function ChecksTab({ job }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.checks(job.id).then(setData).catch((e) => setErr(e.message));
  }, [job.id, job.updated_at]);

  if (err) return <Alert tone="bad">{err}</Alert>;
  if (!data) return <div className="muted sm">Loading checks…</div>;

  const all = data.groups.flatMap((g) => g.items);
  const n = (s) => all.filter((i) => i.status === s).length;

  return (
    <div className="stack">
      <Alert
        tone={data.can_release ? "ok" : n("fail") ? "bad" : "warn"}
        title={data.can_release ? "Everything checks out - downloads are unlocked" : `Not ready yet: ${data.reason}`}
      >
        {n("ok")} passed · {n("warn")} to review · {n("fail")} failed. Every check is
        plain code, not a model's opinion.
      </Alert>

      <div className="grid cols-2">
        {data.groups.map((g) => (
          <div key={g.name} className="card">
            <div className="card-head"><h3>{g.name}</h3></div>
            <div>
              {g.items.map((it, i) => (
                <div key={i} className="row" style={{
                  gap: 12, flexWrap: "nowrap", alignItems: "flex-start", padding: "12px 20px",
                  borderBottom: i < g.items.length - 1 ? "1px solid var(--border)" : 0,
                }}>
                  <span style={{ marginTop: 2, color: it.status === "ok" ? "var(--ok)" : it.status === "warn" ? "var(--warn)" : "var(--bad)" }}>
                    <Icon name={it.status === "ok" ? "check" : it.status === "warn" ? "alert" : "x"} size={16} stroke={2.4} />
                  </span>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="sm strong">{it.label}</div>
                    {it.detail && <div className="xs muted" style={{ wordBreak: "break-word" }}>{it.detail}</div>}
                  </div>
                  <Badge tone={it.status === "ok" ? "ok" : it.status === "warn" ? "warn" : "bad"}>{it.status}</Badge>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
