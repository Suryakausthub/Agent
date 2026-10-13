import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Alert, Badge, EmptyState, Icon } from "../components/ui";

// Library view of the Project Knowledge Base. Projects are used on resumes
// automatically; the only decisions here are "don't use this one" and, for
// projects whose authorship looks uncertain, "yes, this is mine - use it".

function state(p) {
  const u = p.usage || {};
  if (u.in_use) return ["In use", "ok"];
  if (p.review_status === "rejected") return ["Not used", ""];
  if (u.flagged) return ["Confirm it's yours", "warn"];
  return ["No content", ""];
}

const FILTERS = [
  ["all", "All"],
  ["in_use", "In use"],
  ["confirm", "Needs confirmation"],
  ["off", "Not used"],
];

export default function Library({ notify }) {
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(null);

  const load = useCallback(async () => {
    try {
      setData(await api.listProjects());
    } catch (e) {
      notify(e.message, "bad");
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  const projects = useMemo(() => {
    if (!data) return [];
    return data.projects
      .filter((p) => p.classification !== "exclude" && p.project_json)
      .map((p) => ({ ...p, _state: state(p) }));
  }, [data]);

  if (!data) return <div className="page muted">Loading library…</div>;

  const counts = {
    in_use: projects.filter((p) => p.usage?.in_use).length,
    confirm: projects.filter((p) => !p.usage?.in_use && p.usage?.flagged && p.review_status !== "rejected").length,
  };
  counts.off = projects.length - counts.in_use - counts.confirm;

  const shown = projects.filter((p) => {
    const inUse = p.usage?.in_use;
    const confirm = !inUse && p.usage?.flagged && p.review_status !== "rejected";
    if (filter === "in_use" && !inUse) return false;
    if (filter === "confirm" && !confirm) return false;
    if (filter === "off" && (inUse || confirm)) return false;
    if (!q.trim()) return true;
    const hay = JSON.stringify([p.repo_name, p.project_json?.name, p.project_json?.tech_stack, p.project_json?.keywords]).toLowerCase();
    return hay.includes(q.trim().toLowerCase());
  });

  async function setReview(p, status, msg) {
    try {
      await api.reviewProject(p.id, status);
      await load();
      notify(msg);
    } catch (e) {
      notify(e.message, "bad");
    }
  }

  const current = open && projects.find((p) => p.id === open);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Project library</h1>
          <p>
            Your GitHub projects, analysed once. For each job the best ones are
            picked automatically, with up to 3 bullets each.
          </p>
        </div>
      </div>

      <div className="grid cols-3" style={{ marginBottom: 20 }}>
        <div className="card stat-card"><div className="k">In use on resumes</div><div className="v">{counts.in_use}</div></div>
        <div className="card stat-card"><div className="k">Need your confirmation</div><div className="v">{counts.confirm}</div></div>
        <div className="card stat-card"><div className="k">Not used</div><div className="v">{counts.off}</div></div>
      </div>

      {counts.confirm > 0 && (
        <div style={{ marginBottom: 20 }}>
          <Alert tone="warn" title={`${counts.confirm} project${counts.confirm > 1 ? "s look" : " looks"} like they may not be your own code`}>
            A fork, a downloaded archive, or a go.mod owned by someone else. They stay
            off your resume until you confirm with "Use anyway".
          </Alert>
        </div>
      )}

      <div className="between" style={{ marginBottom: 16 }}>
        <div className="segmented">
          {FILTERS.map(([k, label]) => (
            <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{label}</button>
          ))}
        </div>
        <div style={{ position: "relative", width: 280, maxWidth: "100%" }}>
          <Icon name="search" size={15} style={{ position: "absolute", left: 11, top: 11, color: "var(--subtle)" }} />
          <input className="input" style={{ paddingLeft: 34 }} placeholder="Search name or technology"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="card"><EmptyState icon="search" title="Nothing matches" /></div>
      ) : (
        <div className="lib-grid">
          {shown.map((p) => {
            const j = p.project_json;
            const [label, tone] = p._state;
            const tech = Object.values(j.tech_stack || {}).flat();
            return (
              <div key={p.id} className="card lib-card" onClick={() => setOpen(p.id)}>
                <div className="between" style={{ alignItems: "flex-start" }}>
                  <div style={{ minWidth: 0 }}>
                    <div className="strong ellip">{niceName(j.name || p.repo_name)}</div>
                    <div className="xs subtle ellip">{p.repo_name}</div>
                  </div>
                  <Badge tone={tone} dot>{label}</Badge>
                </div>
                <div className="desc">{j.summary || j.description || (j.gaps || [])[0]}</div>
                <div className="row" style={{ gap: 5 }}>
                  {tech.slice(0, 5).map((t) => <span key={t} className="chip">{t}</span>)}
                  {tech.length > 5 && <span className="xs subtle">+{tech.length - 5}</span>}
                </div>
                <div className="between" style={{ marginTop: "auto" }}>
                  <span className="xs subtle">{(j.bullets || []).length} candidate bullets</span>
                  <Actions p={p} setReview={setReview} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {current && (
        <Drawer p={current} onClose={() => setOpen(null)} setReview={setReview}
          onSaved={async () => { await load(); notify("Saved"); }} notify={notify} />
      )}
    </div>
  );
}

function Actions({ p, setReview, big = false }) {
  const cls = "btn " + (big ? "" : "sm");
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  const hasContent = (p.project_json?.bullets || []).length > 0;
  if (!hasContent) return null;
  if (p.review_status === "rejected") {
    return <button className={cls} onClick={stop(() => setReview(p, "needs_review", "Back in use"))}>Use again</button>;
  }
  if (!p.usage?.in_use && p.usage?.flagged) {
    return (
      <div className="row" style={{ gap: 6 }}>
        <button className={cls} onClick={stop(() => setReview(p, "rejected", "Won't be used"))}>Don't use</button>
        <button className={cls + " primary"} onClick={stop(() => setReview(p, "approved", "Confirmed - now in use"))}>Use anyway</button>
      </div>
    );
  }
  return <button className={cls + " ghost"} onClick={stop(() => setReview(p, "rejected", "Won't be used on resumes"))}>Don't use</button>;
}

function niceName(n) {
  return n.includes(" ") ? n : n.replace(/[-_]+/g, " ").trim();
}

function Drawer({ p, onClose, setReview, onSaved, notify }) {
  const j = p.project_json;
  const [editing, setEditing] = useState(false);
  const [bullets, setBullets] = useState((j.bullets || []).map((b) => b.text));
  const stack = Object.entries(j.tech_stack || {}).filter(([, v]) => v.length);
  const [label, tone] = p._state;

  async function save() {
    try {
      await api.editProject(p.id, { bullets });
      setEditing(false);
      await onSaved();
    } catch (e) {
      notify(e.message, "bad");
    }
  }

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer">
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 8 }}>
              <h2 style={{ margin: 0 }}>{niceName(j.name || p.repo_name)}</h2>
              <Badge tone={tone} dot>{label}</Badge>
            </div>
            <div className="xs muted" style={{ marginTop: 4 }}>
              {p.usage?.reason} ·{" "}
              <a href={p.github_url} target="_blank" rel="noreferrer">
                {p.repo_name} <Icon name="external" size={11} />
              </a>
            </div>
          </div>
          <button className="btn ghost sm" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>

        <div className="drawer-body">
          {j.description && <div className="section"><p>{j.description}</p></div>}

          {(j.problem || j.architecture_overview) && (
            <div className="section grid cols-2">
              {j.problem && <div><h3 className="xs subtle">PROBLEM</h3><div className="sm">{j.problem}</div></div>}
              {j.architecture_overview && <div><h3 className="xs subtle">ARCHITECTURE</h3><div className="sm">{j.architecture_overview}</div></div>}
            </div>
          )}

          <div className="section">
            <div className="between" style={{ marginBottom: 10 }}>
              <h3 style={{ margin: 0 }}>Candidate bullets ({(j.bullets || []).length})</h3>
              {!editing ? (
                <button className="btn sm" onClick={() => setEditing(true)}><Icon name="edit" size={14} /> Edit</button>
              ) : (
                <div className="row" style={{ gap: 6 }}>
                  <button className="btn sm" onClick={() => { setEditing(false); setBullets((j.bullets || []).map((b) => b.text)); }}>Cancel</button>
                  <button className="btn sm primary" onClick={save}>Save</button>
                </div>
              )}
            </div>
            <div className="xs muted" style={{ marginBottom: 8 }}>At most 3 of these are chosen for any one job.</div>
            {!editing ? (
              (j.bullets || []).map((b, i) => (
                <div key={b.id} className="cand">
                  <span className="n">{i + 1}</span>
                  <div style={{ flex: 1 }}>{b.text}</div>
                  <span className="badge" style={{ alignSelf: "flex-start" }}>{b.focus.replace(/_/g, " ")}</span>
                </div>
              ))
            ) : (
              bullets.map((b, i) => (
                <textarea key={i} className="textarea" style={{ minHeight: 56, marginBottom: 8 }} value={b}
                  onChange={(e) => { const n = [...bullets]; n[i] = e.target.value; setBullets(n); }} />
              ))
            )}
          </div>

          {stack.length > 0 && (
            <div className="section">
              <h3>Tech stack · verified in the repository</h3>
              <dl className="kv">
                {stack.map(([cat, items]) => (
                  <div key={cat} style={{ display: "contents" }}>
                    <dt style={{ textTransform: "capitalize" }}>{cat.replace(/_/g, " ")}</dt>
                    <dd className="row" style={{ gap: 5 }}>{items.map((t) => <span key={t} className="chip">{t}</span>)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {j.components?.length > 0 && (
            <div className="section">
              <h3>Components</h3>
              <ul className="list sm">
                {j.components.map((c, i) => <li key={i}><strong>{c.name}</strong> - {c.responsibility}</li>)}
              </ul>
            </div>
          )}

          {j.implementation?.length > 0 && (
            <div className="section">
              <h3>What is implemented</h3>
              {j.implementation.map((it, i) => (
                <div key={i} className="cand">
                  <Badge tone={it.status === "implemented" ? "ok" : it.status === "planned" ? "bad" : "warn"}>{it.status}</Badge>
                  <div className="sm" style={{ flex: 1 }}>{it.detail}</div>
                </div>
              ))}
            </div>
          )}

          {j.gaps?.length > 0 && (
            <div className="section">
              <h3>Gaps - what the repository doesn't show</h3>
              <ul className="list sm muted">{j.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
            </div>
          )}

          {j.ownership_note && (
            <div className="section">
              <h3>Ownership</h3>
              <div className="sm">{j.ownership_note}</div>
            </div>
          )}

          {(j.rejected_bullets?.length > 0 || j.warnings?.length > 0) && (
            <div className="section">
              <details className="more">
                <summary>Quality checks - {j.rejected_bullets?.length || 0} bullet(s) rejected while building</summary>
                <ul className="list xs muted" style={{ marginTop: 10 }}>
                  {(j.warnings || []).map((w, i) => <li key={"w" + i}>{w}</li>)}
                  {(j.rejected_bullets || []).map((r, i) => <li key={"r" + i}>"{r.text}" - {r.why}</li>)}
                </ul>
              </details>
            </div>
          )}
        </div>

        <div className="drawer-foot">
          <Actions p={p} setReview={setReview} big />
        </div>
      </aside>
    </>
  );
}
