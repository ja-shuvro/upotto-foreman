/** Fetch helpers for Foreman Flask API */
const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

async function req(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { ...JSON_HEADERS, ...(opts.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || data.message || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  status: () => req("/api/status"),
  startRun: () => req("/api/run/start", { method: "POST", body: "{}" }),
  stopRun: () => req("/api/run/stop", { method: "POST", body: "{}" }),
  setProject: (path) => req("/api/project", { method: "POST", body: JSON.stringify({ path }) }),
  docs: () => req("/api/docs"),
  docContent: (name) => req(`/api/docs/content?name=${encodeURIComponent(name)}`),
  saveDoc: (name, content) =>
    req("/api/docs", { method: "POST", body: JSON.stringify({ name, content }) }),
  logs: () => req("/api/logs?lines=200"),
  timeline: () => req("/api/timeline"),
  chat: (message) =>
    req("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, source: "webview" }),
    }),
  chatHistory: () => req("/api/chat/history"),
  approve: (approved, note = "") =>
    req("/api/approve", {
      method: "POST",
      body: JSON.stringify({ approved, note, source: "webview" }),
    }),
  settings: () => req("/api/settings"),
  saveSettings: (payload) =>
    req("/api/settings", { method: "POST", body: JSON.stringify(payload) }),
  startup: (enabled) =>
    req("/api/startup", { method: "POST", body: JSON.stringify({ enabled }) }),
};
