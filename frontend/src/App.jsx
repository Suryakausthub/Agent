import { useCallback, useEffect, useState } from "react";
import { api, REQUIRED_API } from "./api";
import { Alert, Avatar, Icon, band } from "./components/ui";
import Home from "./pages/Home";
import NewApplication from "./pages/NewApplication";
import JobPage from "./pages/JobPage";
import Library from "./pages/Library";

export default function App() {
  const [health, setHealth] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [page, setPage] = useState("home"); // home | new | job | library
  const [sel, setSel] = useState(null);
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState(null);

  const notify = useCallback((text, tone = "") => {
    setToast({ text, tone });
    setTimeout(() => setToast(null), tone === "bad" ? 7000 : 3000);
  }, []);

  const refreshJobs = useCallback(async () => {
    try {
      setJobs((await api.listJobs()).jobs);
    } catch (e) {
      notify(e.message, "bad");
    }
  }, [notify]);

  const loadJob = useCallback(async (id) => {
    if (!id) return setJob(null);
    try {
      setJob(await api.getJob(id));
    } catch (e) {
      notify(e.message, "bad");
    }
  }, [notify]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(false));
    refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    loadJob(sel);
  }, [sel, loadJob]);

  // Runs a backend action for the open job, then refreshes everything.
  async function run(label, fn, done) {
    setBusy(label);
    try {
      const r = await fn();
      await refreshJobs();
      await loadJob(sel);
      if (done) notify(done);
      return r;
    } catch (e) {
      notify(e.message, "bad");
    } finally {
      setBusy("");
    }
  }

  function openJob(id) {
    setSel(id);
    setPage("job");
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Icon name="briefcase" size={16} stroke={2.2} />
          </div>
          <div>
            <div className="brand-name">Job Agent</div>
            <div className="brand-sub">Tailored, truthful resumes</div>
          </div>
        </div>

        <div className="side-new">
          <button className="btn primary block" onClick={() => setPage("new")}>
            <Icon name="plus" size={16} stroke={2.2} /> New application
          </button>
        </div>

        <nav className="side-nav">
          <div className={"side-item " + (page === "home" ? "on" : "")} onClick={() => setPage("home")}>
            <Icon name="briefcase" /> Applications
            <span className="count">{jobs.length}</span>
          </div>
          <div className={"side-item " + (page === "library" ? "on" : "")} onClick={() => setPage("library")}>
            <Icon name="folder" /> Project library
          </div>
        </nav>

        <div className="side-label">Recent</div>
        <div className="side-recent">
          {jobs.slice(0, 12).map((j) => (
            <div
              key={j.id}
              className={"recent " + (page === "job" && sel === j.id ? "on" : "")}
              onClick={() => openJob(j.id)}
            >
              <Avatar name={j.company} size="sm" />
              <div className="meta">
                <div className="co ellip">{j.company || "Untitled"}</div>
                <div className="ti ellip">{j.title || "—"}</div>
              </div>
              {j.match_score != null && (
                <span className={"badge " + band(j.match_score)}>{j.match_score}%</span>
              )}
            </div>
          ))}
          {jobs.length === 0 && <div className="xs subtle" style={{ padding: "4px 10px" }}>No applications yet.</div>}
        </div>

        <div className="side-foot">
          <span className={"dot " + (health ? "ok" : health === false ? "bad" : "")} />
          <span className="ellip">
            {health
              ? `${health.model.split("/").pop()} · PDF ${health.compile_backend === "tectonic" ? "ready" : "estimated"}`
              : health === false
              ? "Server unreachable"
              : "Connecting…"}
          </span>
        </div>
      </aside>

      <main className="main">
        {health && (health.api_version || 0) < REQUIRED_API && (
          <div style={{ padding: "16px 36px 0" }}>
            <Alert tone="bad" title="Job Agent was updated - restart it to finish">
              The server still running is the old version, so PDF downloads and
              GitHub project selection won't work. Close the window running
              run.cmd and start it again.
            </Alert>
          </div>
        )}
        {page === "home" && (
          <Home jobs={jobs} onOpen={openJob} onNew={() => setPage("new")} />
        )}
        {page === "new" && (
          <NewApplication
            notify={notify}
            onCreated={async (id) => {
              await refreshJobs();
              setSel(id);
              setPage("job");
              // Analyse straight away - there is nothing else to do first.
              setBusy("analyse");
              try {
                await api.analyse(id);
              } catch (e) {
                notify(e.message, "bad");
              } finally {
                setBusy("");
                await refreshJobs();
                await loadJob(id);
              }
            }}
          />
        )}
        {page === "library" && <Library notify={notify} />}
        {page === "job" && job && (
          <JobPage
            key={job.id}
            job={job}
            busy={busy}
            run={run}
            notify={notify}
            onBack={() => setPage("home")}
            onDeleted={async () => {
              setSel(null);
              setJob(null);
              setPage("home");
              await refreshJobs();
            }}
          />
        )}
        {page === "job" && !job && <div className="page muted">Loading…</div>}
      </main>

      {toast && (
        <div className={"toast " + toast.tone}>
          <span>{toast.text}</span>
          <button className="btn ghost sm" style={{ color: "inherit" }} onClick={() => setToast(null)}>
            <Icon name="x" size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
