import { useProgress } from "../useProgress";
import { Icon } from "./ui";

/**
 * One line that always answers "where is my resume?".
 *
 *  - While a run is in progress it shows the live stage and a percentage.
 *    Stages are weighted by how long they really take; rewriting is one model
 *    call per bullet, so it fills in bullet by bullet ("bullet 7 of 15").
 *  - When idle it shows the resume's journey: Added -> Analysed -> Tailored
 *    -> Approved, with how far along this application is.
 */

const ANALYSE = [
  ["extract", "Reading the job description", 0.55],
  ["resolve", "Matching skills", 0.05],
  ["score", "Scoring your match", 0.15],
  ["ats", "Checking ATS keywords", 0.25],
];
const TAILOR = [
  ["select", "Choosing projects & bullets", 0.1],
  ["rewrite", "Rewriting bullets", 0.58],
  ["summary", "Tuning your summary", 0.08],
  ["validate", "Verifying every claim", 0.07],
  ["fit", "Building the PDF & fitting one page", 0.17],
];

const JOURNEY = [
  ["Job added", () => true],
  ["Analysed", (j) => !!j.score_json],
  ["Tailored", (j) => !!j.diff_json],
  ["Approved", (j) => !!j.approved_at],
];

function liveState(events, busy) {
  const by = Object.fromEntries(events.map((e) => [e.stage, e]));
  const isTailor = TAILOR.some(([id]) => by[id]) || busy === "tailor";
  const plan = isTailor ? TAILOR : ANALYSE;
  let pct = 0;
  let current = null;
  let failed = null;
  for (const [id, label, w] of plan) {
    const e = by[id];
    if (!e) continue;
    if (e.status === "running") {
      let frac = 0.25;
      if (id === "rewrite" && e.data?.total) {
        // The batch call is the slow part; checking each returned line is fast.
        frac = e.data.outcome === "batch" ? 0.4 : 0.6 + 0.4 * (e.data.index / e.data.total);
      }
      pct += w * Math.max(0, Math.min(1, frac));
      current = { label, detail: e.detail, data: e.data, id };
    } else {
      pct += w;
      if (e.status === "fail") failed = { label, detail: e.detail };
    }
  }
  const done = by.done && !current;
  return {
    op: isTailor ? "Tailoring your resume" : "Analysing the job",
    pct: done ? 100 : Math.round(pct * 100),
    current,
    failed,
    done,
    doneEvent: by.done,
  };
}

export default function ProgressLine({ job, busy }) {
  const { events, running } = useProgress(job.id);
  const live = liveState(events, busy);
  const active = (busy === "analyse" || busy === "tailor" || running) && !live.done;

  if (active) {
    const c = live.current;
    const bullet =
      c?.id === "rewrite" && c.data?.total
        ? c.data.outcome === "batch"
          ? `rewriting all ${c.data.total} bullets together`
          : `checking bullet ${c.data.index} of ${c.data.total}`
        : null;
    return (
      <div className="card progress">
        <div className="progress-top">
          <div className="progress-stage">
            <span className="dot run" />
            <span className="name">{c ? c.label : live.op}</span>
            <span className="detail ellip">
              {bullet || (c ? c.detail : "starting…")}
            </span>
          </div>
          <div className="progress-pct">{live.pct}%</div>
        </div>
        <div className="progress-track">
          <div className="progress-fill live" style={{ width: `${Math.max(3, live.pct)}%` }} />
        </div>
        <div className="xs subtle" style={{ marginTop: 8 }}>{live.op}</div>
      </div>
    );
  }

  // Idle: how far along is this resume?
  const reached = JOURNEY.filter(([, test]) => test(job)).length;
  const pct = Math.round((reached / JOURNEY.length) * 100);
  const blocked = job.diff_json && job.gate_ok === 0;
  const nextIdx = reached < JOURNEY.length ? reached : -1;
  const label =
    reached === JOURNEY.length
      ? "Resume ready - download the PDF"
      : blocked
      ? "Tailored, but blocked - an unsupported claim needs fixing"
      : ["", "Next: analyse the job", "Next: tailor your resume", "Next: review & approve"][reached];

  return (
    <div className="card progress">
      <div className="progress-top">
        <div className="progress-stage">
          {reached === JOURNEY.length ? (
            <Icon name="check" size={16} style={{ color: "var(--ok)" }} />
          ) : (
            <span className={"dot " + (blocked ? "bad" : "")} />
          )}
          <span className="name">Resume {pct}% done</span>
          <span className="detail ellip">{label}</span>
        </div>
        <div className="progress-pct">{pct}%</div>
      </div>
      <div className="progress-track">
        <div
          className={"progress-fill " + (pct === 100 ? "done" : blocked ? "blocked" : "")}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="milestones" style={{ gridTemplateColumns: `repeat(${JOURNEY.length}, 1fr)` }}>
        {JOURNEY.map(([name], i) => (
          <div key={name} className={"milestone " + (i < reached ? "done" : i === nextIdx ? "now" : "")}>
            <span className="tick">{i < reached && <Icon name="check" size={10} stroke={3} />}</span>
            {name}
          </div>
        ))}
      </div>
      {live.failed && (
        <div className="xs" style={{ marginTop: 8, color: "var(--bad)" }}>
          Last run failed at "{live.failed.label}": {live.failed.detail}
        </div>
      )}
    </div>
  );
}
