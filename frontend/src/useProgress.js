import { useEffect, useRef, useState } from "react";

/**
 * Follows a job's pipeline progress over Server-Sent Events.
 *
 * The backend replays whatever it already has before following live, so
 * mounting mid-run still shows the full picture rather than starting blank.
 */
export function useProgress(jobId) {
  const [events, setEvents] = useState([]);
  const [live, setLive] = useState(false);
  const esRef = useRef(null);

  useEffect(() => {
    setEvents([]);
    if (!jobId) return;

    const es = new EventSource(`/api/jobs/${jobId}/stream`);
    esRef.current = es;

    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        // The first stage of a run starts a fresh timeline; otherwise the
        // previous run's "done" would make a new run look finished.
        if (ev.status === "running" && (ev.stage === "extract" || ev.stage === "select")) {
          setEvents([ev]);
          return;
        }
        setEvents((prev) => {
          // One row per stage: a "running" row is replaced by its outcome
          // rather than stacking up.
          const i = prev.findIndex((p) => p.stage === ev.stage);
          if (i === -1) return [...prev, ev];
          const next = [...prev];
          next[i] = ev;
          return next;
        });
      } catch {
        /* keepalive comment lines are not JSON */
      }
    };

    return () => {
      es.close();
      esRef.current = null;
      setLive(false);
    };
  }, [jobId]);

  const running = events.some((e) => e.status === "running");
  return { events, live, running };
}
