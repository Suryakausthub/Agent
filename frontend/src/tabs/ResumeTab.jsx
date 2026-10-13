import { useEffect, useState } from "react";
import { api, downloadFile } from "../api";
import { Alert, Badge, EmptyState, Icon } from "../components/ui";

export default function ResumeTab({ job, run, busy, notify }) {
  const [gate, setGate] = useState(null);
  const [onlyChanged, setOnlyChanged] = useState(true);
  const [previewLoading, setPreviewLoading] = useState(true);
  const d = job.diff_json;
  const t = job.tailor_json;

  useEffect(() => {
    if (!d) return;
    api.diff(job.id).then((r) => setGate(r.gate)).catch(() => setGate(null));
  }, [job.id, job.updated_at, job.approved_at, d]);

  useEffect(() => setPreviewLoading(true), [job.updated_at, job.approved_at]);

  if (!d) {
    return (
      <EmptyState icon="file" title="No tailored resume yet">
        Press <strong>Tailor resume</strong> to pick your strongest projects for
        this job and rewrite your bullets around it.
      </EmptyState>
    );
  }

  const high = d.high || [];
  const canRelease = !!gate?.can_release;
  const approved = !!job.approved_at;
  const lines = (d.lines || []).filter((l) => !onlyChanged || l.changed || l.violations?.length);
  const projects = t?.selection?.projects || [];

  async function download(kind) {
    try {
      const name = kind === "pdf"
        ? await downloadFile(api.pdfUrl(job.id), "application/pdf")
        : await downloadFile(api.texUrl(job.id), "text/plain");
      notify(`Downloaded ${name}`);
    } catch (e) {
      notify(e.message, "bad");
    }
  }

  async function copyTex() {
    try {
      const tex = await fetch(api.texUrl(job.id)).then((r) => {
        if (!r.ok) throw new Error("Approve the resume first");
        return r.text();
      });
      await navigator.clipboard.writeText(tex);
      notify("LaTeX copied - paste it into Overleaf");
    } catch (e) {
      notify(e.message, "bad");
    }
  }

  return (
    <div className="split">
      <div className="stack">
        {/* status + actions */}
        {high.length ? (
          <Alert tone="bad" title={`Blocked - ${high.length} unsupported claim${high.length > 1 ? "s" : ""}`}>
            A rewritten line mentions something that isn't in your resume or
            projects. Re-tailor, or fix the source, before this can be approved.
          </Alert>
        ) : approved ? (
          <Alert tone="ok" title="Approved - your resume is ready"
            action={
              <button className="btn primary" onClick={() => download("pdf")}>
                <Icon name="download" size={16} /> Download PDF
              </button>
            }>
            {t?.pages ? `${t.pages} page${t.pages > 1 ? "s" : ""}${t.measured ? "" : " (estimated)"}` : ""}
            {" · every claim traced to your real experience"}
          </Alert>
        ) : (
          <Alert tone="warn" title="Review the changes, then approve"
            action={
              <button className="btn primary" disabled={!!busy}
                onClick={() => run("approve", () => api.approve(job.id), "Approved - PDF ready to download")}>
                <Icon name="check" size={16} /> Approve
              </button>
            }>
            All automated checks passed ({d.checked} lines verified
            {t?.pages ? `, ${t.pages} page${t.pages > 1 ? "s" : ""}${t.measured ? " measured" : " estimated"}` : ""}).
            Downloads unlock once you approve.
          </Alert>
        )}

        <div className="row">
          <button className={"btn " + (canRelease ? "primary" : "")} disabled={!canRelease}
            onClick={() => download("pdf")}>
            <Icon name="download" size={16} /> Download PDF
          </button>
          <button className="btn" disabled={!canRelease} onClick={() => download("tex")}>
            <Icon name="file" size={16} /> Download .tex
          </button>
          <button className="btn" disabled={!canRelease} onClick={copyTex}>
            <Icon name="copy" size={15} /> Copy LaTeX
          </button>
          {!canRelease && <span className="xs subtle">{gate?.reason}</span>}
        </div>

        {high.length > 0 && (
          <div className="card">
            <div className="card-head"><h3>Unsupported claims</h3></div>
            <table className="table">
              <tbody>
                {high.map((v, i) => (
                  <tr key={i}>
                    <td style={{ width: 150 }}><Badge tone="bad">{v.kind.replace(/_/g, " ")}</Badge></td>
                    <td><div className="strong sm">{v.value}</div><div className="xs muted">{v.detail}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {projects.length > 0 && (
          <div className="card">
            <div className="card-head">
              <h3>Projects on this resume</h3>
              <span className="xs muted">
                {t.selection.method === "model"
                  ? `picked by the model from all ${t.selection.considered || "your"} projects`
                  : t.selection.method === "main.tex only"
                  ? "only your main.tex projects were considered"
                  : "ranked by skill overlap (model unavailable)"}
              </span>
            </div>
            {t.selection.method !== "model" && (
              <div className="card-body" style={{ paddingBottom: 0 }}>
                <Alert tone="warn" title="Your GitHub projects weren't considered for this resume">
                  {t.selection.method === "main.tex only"
                    ? "This version was tailored before your GitHub library was switched on."
                    : "The model call failed, so projects were ranked by keyword overlap only."}{" "}
                  Press <strong>Re-tailor</strong> to choose from all of your projects.
                </Alert>
              </div>
            )}
            <div className="card-body" style={{ paddingTop: 4, paddingBottom: 4 }}>
              {projects.map((p) => (
                <div key={p.id} className="proj">
                  <div className="avatar sm" style={p.origin === "bank" ? undefined : { background: "var(--surface-3)", color: "var(--muted)" }}>
                    <Icon name={p.origin === "bank" ? "github" : "file"} size={14} />
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span className="strong sm">{p.title}</span>
                      <Badge tone={p.origin === "bank" ? "accent" : ""}>
                        {p.origin === "bank" ? "GitHub" : "main.tex"}
                      </Badge>
                      <span className="xs subtle">{p.bullets.length} bullet{p.bullets.length === 1 ? "" : "s"}</span>
                    </div>
                    {p.reason && <div className="xs muted" style={{ marginTop: 3 }}>{p.reason}</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="card">
          <div className="card-head">
            <h3>Changes</h3>
            <div className="segmented">
              <button className={onlyChanged ? "on" : ""} onClick={() => setOnlyChanged(true)}>Changed</button>
              <button className={!onlyChanged ? "on" : ""} onClick={() => setOnlyChanged(false)}>All lines</button>
            </div>
          </div>
          {lines.length === 0 && <div className="card-body sm muted">No lines changed.</div>}
          {lines.map((l) => {
            const bad = (l.violations || []).some((v) => v.severity === "high");
            return (
              <div key={l.bullet_id} className={"bullet " + (bad ? "flag" : "")}>
                {l.changed && <div className="was">{l.source}</div>}
                <div className="now">{l.tailored}</div>
                <div className="src">
                  <Badge tone={l.changed ? "accent" : ""}>{l.changed ? "rewritten" : "unchanged"}</Badge>
                  <span>
                    {l.bullet_id.startsWith("bank.") ? "from your GitHub project" : `main.tex line ${l.source_line}`}
                  </span>
                  {(l.violations || []).map((v, i) => (
                    <span key={i} style={{ color: v.severity === "high" ? "var(--bad)" : "var(--subtle)" }}>
                      · {v.kind.replace(/_/g, " ")}: {v.value}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {(t?.trim_actions?.length > 0 || t?.rejected?.length > 0) && (
          <div className="card card-pad">
            <details className="more">
              <summary>
                Behind the scenes - {t.trim_actions?.length || 0} trim step(s), {t.rejected?.length || 0} rejected rewrite(s)
              </summary>
              <div style={{ marginTop: 12 }} className="stack">
                {t.trim_actions?.length > 0 && (
                  <div>
                    <div className="xs strong muted" style={{ marginBottom: 6 }}>Trimmed to fit one page</div>
                    {t.trim_actions.map((a, i) => (
                      <div key={i} className="sm">· {a.step.replace(/_/g, " ")}: {a.target}</div>
                    ))}
                  </div>
                )}
                {t.rejected?.length > 0 && (
                  <div>
                    <div className="xs strong muted" style={{ marginBottom: 6 }}>Rewrites rejected (your original wording kept)</div>
                    {t.rejected.map(([id, why, text], i) => (
                      <div key={i} className="sm" style={{ marginBottom: 6 }}>
                        <Badge tone="warn">{why}</Badge> <span className="muted">{text}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </details>
          </div>
        )}
      </div>

      <div className="card sticky">
        <div className="card-head">
          <h3>Preview</h3>
          {approved ? <Badge tone="ok">Final</Badge> : <Badge tone="warn">Draft watermark until approved</Badge>}
        </div>
        {previewLoading && <div className="card-body xs muted">Building PDF preview…</div>}
        <iframe
          key={`${job.updated_at}-${job.approved_at}`}
          className="pdf-frame"
          title="Resume preview"
          src={api.previewUrl(job.id, `${job.updated_at}-${job.approved_at || ""}`) + "#toolbar=0&view=FitH"}
          onLoad={() => setPreviewLoading(false)}
        />
      </div>
    </div>
  );
}
