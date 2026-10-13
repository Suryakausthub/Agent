const BASE = "/api";

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text; // /tex returns plain text
  }
  if (!res.ok) {
    const detail = (body && body.detail) || res.statusText;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

export const api = {
  health: () => req("/health"),
  stages: () => req("/stages"),
  master: () => req("/resume/master"),

  listJobs: () => req("/jobs"),
  getJob: (id) => req(`/jobs/${id}`),
  createJob: (payload) =>
    req("/jobs", { method: "POST", body: JSON.stringify(payload) }),
  deleteJob: (id) => req(`/jobs/${id}`, { method: "DELETE" }),

  analyse: (id, force = false) =>
    req(`/jobs/${id}/analyse${force ? "?force=true" : ""}`, { method: "POST" }),
  tailor: (id) => req(`/jobs/${id}/tailor`, { method: "POST" }),
  diff: (id) => req(`/jobs/${id}/diff`),
  approve: (id) => req(`/jobs/${id}/approve`, { method: "POST" }),
  audit: (id) => req(`/jobs/${id}/audit`),
  checks: (id) => req(`/jobs/${id}/checks`),
  referral: (id, body) =>
    req(`/jobs/${id}/referral`, { method: "POST", body: JSON.stringify(body) }),
  setStatus: (id, status) =>
    req(`/jobs/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  setNotes: (id, notes) =>
    req(`/jobs/${id}/notes`, {
      method: "POST",
      body: JSON.stringify({ notes }),
    }),

  texUrl: (id) => `${BASE}/jobs/${id}/tex`,
  pdfUrl: (id) => `${BASE}/jobs/${id}/pdf`,
  previewUrl: (id, v = "") => `${BASE}/jobs/${id}/preview.pdf?v=${encodeURIComponent(v)}`,

  // Project Bank / Knowledge Base. Ids are "owner/repo"; the backend route
  // takes the slash as part of the path, so encode each segment separately.
  listProjects: () => req("/projects?extracted_only=false"),
  getProject: (id) => req(`/projects/${projectPath(id)}`),
  reviewProject: (id, status) =>
    req(`/projects/${projectPath(id)}/review`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  editProject: (id, payload) =>
    req(`/projects/${projectPath(id)}/edit`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  classifyProject: (id, classification) =>
    req(`/projects/${projectPath(id)}/classify`, {
      method: "POST",
      body: JSON.stringify({ classification }),
    }),
};

// The UI version this build expects from the server (see API_VERSION in
// backend/app/main.py). A lower one means the server predates the UI.
export const REQUIRED_API = 3;

/**
 * Download a file, checking it really is what we asked for.
 *
 * A plain <a href> saves whatever comes back - an error page included - and
 * the user only finds out when the "PDF" won't open. Fetching first lets the
 * real reason ("awaiting approval", "restart the server") be shown instead.
 */
export async function downloadFile(url, expectType) {
  const res = await fetch(url);
  const type = res.headers.get("content-type") || "";
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  if (expectType && !type.includes(expectType)) {
    throw new Error("The server sent the wrong kind of file - restart Job Agent (run.cmd) and try again.");
  }
  const disp = res.headers.get("content-disposition") || "";
  const name = (disp.match(/filename="?([^";]+)"?/) || [])[1] || "resume";
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  return name;
}

function projectPath(id) {
  return id.split("/").map(encodeURIComponent).join("/");
}

export const STATUSES = [
  "saved", "scored", "tailored", "approved", "referral_requested",
  "referral_received", "applied", "assessment", "interview", "final_round",
  "offer", "rejected", "withdrawn", "no_response",
];
