import { api } from "./api.js";
import { renderMarkdownSafe } from "./markdown.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  view: "dashboard",
  status: null,
  chatLen: 0,
  confirm: null,
};

/* ——— Icons (inline SVG, Lucide-like) ——— */
const ICONS = {
  dashboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>`,
  project: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  docs: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg>`,
  approvals: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`,
  logs: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>`,
  chat: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a4 4 0 0 1-4 4H7l-4 4V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>`,
};

function toast(message, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    el.style.transition = "200ms";
    setTimeout(() => el.remove(), 220);
  }, 3800);
}

function showLoading(show, text = "Working…") {
  const ov = $("#loading");
  $("#loading-text").textContent = text;
  ov.classList.toggle("show", !!show);
}

function confirmModal({ title, body, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const back = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-body").textContent = body;
    const ok = $("#modal-ok");
    ok.textContent = confirmLabel;
    ok.className = danger ? "btn danger" : "btn primary";
    back.classList.add("show");
    const cleanup = (val) => {
      back.classList.remove("show");
      ok.onclick = null;
      $("#modal-cancel").onclick = null;
      resolve(val);
    };
    ok.onclick = () => cleanup(true);
    $("#modal-cancel").onclick = () => cleanup(false);
  });
}

function setView(name) {
  state.view = name;
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  const titles = {
    dashboard: ["Dashboard", "Live run status, cost, and plan"],
    project: ["Project", "Path, git, and spec docs checklist"],
    docs: ["Docs", "Architecture · Design · Memory · Phases · PRD · Rules"],
    approvals: ["Approvals", "Architectural change gate"],
    logs: ["Logs", "Timeline of agent actions"],
    chat: ["Chat", "Same agent as Telegram"],
    settings: ["Settings", "Keys, Telegram, Windows startup"],
  };
  const [t, s] = titles[name] || ["Upotto Foreman", ""];
  $("#page-title").textContent = t;
  $("#page-sub").textContent = s;
  if (name === "docs") loadDocs();
  if (name === "logs") loadLogs();
  if (name === "chat") loadChat(true);
  if (name === "settings") loadSettings();
  if (name === "approvals") renderApprovals(state.status);
  if (name === "project") renderProject(state.status);
}

function formatCost(snap) {
  const hist = snap?.cost_history || [];
  const last = hist[hist.length - 1];
  if (!last) return "$0.00";
  const v = last.total_cost_usd ?? last.total_cost;
  return typeof v === "number" ? `$${v.toFixed(4)}` : String(v ?? "$0");
}

function docsCount(snap) {
  const docs = snap?.docs || [];
  const n = docs.filter((d) => d.present).length;
  return `${n}/${docs.length || 6}`;
}

function updateStatusChip(snap) {
  const livePhase = snap?.live_phase;
  const st = (livePhase || snap?.status || "idle").toString();
  const stKey = st.toLowerCase().replace(/\s+/g, "_");
  const pulse = $("#status-pulse");
  const label = $("#status-label");
  pulse.className = `pulse ${stKey}`;
  label.textContent = st.replace(/_/g, " ");
  const running = !!snap?.running;
  $("#btn-start").disabled = running;
  $("#btn-stop").disabled = !running;

  if (livePhase) {
    setLivePhase(livePhase, snap.live_phase_detail || "");
  } else {
    setLivePhase(mapRunnerPhase(snap?.status), snap?.last_error || "");
  }
  if (typeof snap?.run_cost_usd === "number") {
    setLiveCost(snap.run_cost_usd);
  }
}

function mapRunnerPhase(status) {
  const s = (status || "idle").toLowerCase();
  if (s === "pending_approval") return "Awaiting Approval";
  if (s === "running") return "Planning";
  if (s === "error" || s === "failed") return "Error";
  return "Idle";
}

function setLivePhase(phase, detail = "") {
  const label = phase || "Idle";
  const key = label.toLowerCase().replace(/\s+/g, "_");
  $("#live-phase-label").textContent = label;
  $("#live-phase-detail").textContent = detail || (label === "Idle" ? "Waiting for a run" : "");
  $("#live-phase-dot").className = `pulse ${key}`;
}

function setLiveCost(usd) {
  const n = Number(usd) || 0;
  $("#live-cost-value").textContent = `$${n.toFixed(6)}`;
}

function renderDashboard(snap) {
  if (!snap) return;
  $("#stat-status").textContent = (snap.live_phase || snap.status || "idle").toString().toUpperCase();
  if (typeof snap.run_cost_usd === "number") {
    $("#stat-cost").textContent = `$${Number(snap.run_cost_usd).toFixed(4)}`;
  } else {
    $("#stat-cost").textContent = formatCost(snap);
  }
  $("#stat-docs").textContent = docsCount(snap);
  $("#stat-git").textContent = snap.git_ok ? "Ready" : "Missing";
  $("#stat-git").className = `stat-value ${snap.git_ok ? "ok-text" : "warn-text"}`;
  $("#dash-path").textContent = snap.project_dir || "—";
  $("#dash-summary").innerHTML = renderMarkdownSafe(snap.last_summary || "_No summary yet._");
  $("#dash-plan").innerHTML = renderMarkdownSafe(snap.current_plan || "_No plan yet._");
  const err = snap.last_error || "";
  const errBox = $("#dash-error");
  if (err) {
    errBox.style.display = "block";
    errBox.textContent = err;
  } else {
    errBox.style.display = "none";
  }
  const pending = snap.pending_approval;
  const banner = $("#approval-banner");
  if (pending && pending.status === "pending") {
    banner.style.display = "block";
    $("#banner-reason").textContent = pending.reason || "Approval required";
  } else {
    banner.style.display = "none";
  }
}

function renderProject(snap) {
  if (!snap) return;
  $("#project-path").value = snap.project_dir || "";
  $("#project-git").innerHTML = snap.git_ok
    ? `<span class="git-badge ok">.git found</span>`
    : `<span class="git-badge warn">Run git init in this folder</span>`;
  const list = $("#docs-checklist");
  list.innerHTML = (snap.docs || [])
    .map(
      (d) =>
        `<tr><td>${d.name}</td><td>${
          d.present ? '<span class="ok-text">present</span>' : '<span class="warn-text">missing</span>'
        }</td><td class="muted">${d.size}</td></tr>`
    )
    .join("");
}

function renderApprovals(snap) {
  const pending = snap?.pending_approval;
  const box = $("#approval-body");
  if (pending && pending.status === "pending") {
    box.innerHTML = `
      <div class="card approval-card">
        <h3>Pending approval</h3>
        <p class="reason">${escapeText(pending.reason || "")}</p>
        <p class="muted">Requested: ${escapeText(formatDateTime(pending.requested_at))}</p>
        <div class="md scroll-pane tall">${renderMarkdownSafe(pending.plan || "")}</div>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" id="btn-approve">Approve</button>
          <button class="btn danger" id="btn-reject">Reject</button>
        </div>
        <p class="muted" style="margin-top:12px">Also works via Telegram YES / NO. Uses local /api/approve (loopback).</p>
      </div>`;
    $("#btn-approve").onclick = () => doApprove(true);
    $("#btn-reject").onclick = () => doApprove(false);
  } else if (pending) {
    box.innerHTML = `<div class="card"><h3>Last decision</h3><p><strong>${escapeText(
      pending.status || ""
    )}</strong> (${escapeText(pending.source || "")})</p><p class="muted">${escapeText(
      pending.reason || ""
    )}</p></div>`;
  } else {
    box.innerHTML = `<div class="card"><p class="muted">No pending approval.</p></div>`;
  }
}

async function doApprove(approved) {
  const ok = await confirmModal({
    title: approved ? "Approve change?" : "Reject change?",
    body: approved
      ? "This marks the architectural change as approved so the Developer can proceed on the next run."
      : "This rejects the pending change. Agents will not implement it.",
    confirmLabel: approved ? "Approve" : "Reject",
    danger: !approved,
  });
  if (!ok) return;
  try {
    await api.approve(approved);
    toast(approved ? "Approved" : "Rejected", approved ? "ok" : "warn");
    await refresh();
  } catch (e) {
    toast(e.message || "Approve failed", "error");
  }
}

async function loadDocs() {
  const data = await api.docs();
  const nav = $("#doc-nav");
  nav.innerHTML = (data.docs || [])
    .map(
      (d) =>
        `<button class="btn ghost sm ${d.present ? "" : "missing"}" data-doc="${d.name}">${d.name}</button>`
    )
    .join("");
  nav.onclick = async (ev) => {
    const btn = ev.target.closest("[data-doc]");
    if (!btn) return;
    await showDoc(btn.dataset.doc);
  };
  const first = (data.docs || []).find((d) => d.present);
  if (first) await showDoc(first.name);
}

async function showDoc(name) {
  const body = $("#doc-body");
  body.innerHTML = `<div class="skeleton" style="height:120px"></div>`;
  try {
    const data = await api.docContent(name);
    body.innerHTML = `
      <details open>
        <summary>${name} <span class="muted">${data.present ? "ready" : "missing"}</span></summary>
        <div class="md">${
          data.present ? renderMarkdownSafe(data.content || "") : "<p class='muted'>File missing — upload from Project.</p>"
        }</div>
      </details>`;
  } catch (e) {
    body.innerHTML = `<p class="err-text">${escapeText(e.message)}</p>`;
  }
}

async function loadLogs() {
  const data = await api.timeline();
  const tl = $("#timeline");
  const items = data.items || [];
  if (!items.length) {
    tl.innerHTML = `<p class="muted">No history yet.</p>`;
    return;
  }
  tl.innerHTML = items
    .map(
      (it) => `
    <div class="tl-item">
      <div class="tl-rail"><div class="tl-dot"></div></div>
      <div>
        <div class="tl-time">${escapeText(formatDateTime(it.timestamp))} · ${escapeText(it.level || "INFO")}</div>
        <div class="tl-msg">${escapeText(it.message || "")}</div>
      </div>
    </div>`
    )
    .join("");
  const file = $("#log-file");
  file.textContent = data.file_tail || "(no file log)";
}

async function loadChat(force = false) {
  const data = await api.chatHistory();
  const hist = data.history || [];
  if (!force && hist.length === state.chatLen) return;
  state.chatLen = hist.length;
  const box = $("#chat-messages");
  if (!hist.length) {
    box.innerHTML = `<p class="muted" style="text-align:center;margin:40px">No messages yet. Chat here or from Telegram.</p>`;
    return;
  }
  box.innerHTML = hist
    .flatMap((t) => [
      bubble("user", t.question, t.source, t.timestamp),
      bubble("agent", t.answer, t.source, t.timestamp),
    ])
    .join("");
  box.scrollTop = box.scrollHeight;
}

function bubble(who, text, source, ts) {
  const src =
    source === "telegram"
      ? `<span class="tg">Telegram</span>`
      : source === "desktop" || source === "webview"
        ? "Desktop"
        : "";
  return `<div class="bubble-row ${who}">
    <div class="bubble-meta">${who === "user" ? "You" : "Agent"}${src ? " · " + src : ""} · ${escapeText(
      formatDateTime(ts)
    )}</div>
    <div class="bubble"><div class="md">${who === "agent" ? renderMarkdownSafe(text || "") : escapeText(text || "")}</div></div>
  </div>`;
}

async function loadSettings() {
  const s = await api.settings();
  const grid = $("#settings-grid");
  const keys = Object.keys(s).filter((k) => !k.startsWith("_"));
  grid.innerHTML = keys
    .map(
      (k) => `<div class="field"><label>${k}</label><input data-key="${k}" value="${escapeAttr(
        s[k] ?? ""
      )}" /></div>`
    )
    .join("");
  $("#startup-toggle").checked = !!s._startup_enabled;
}

/** Display format: "19 Dec, 2026 10:05 AM" (local time). */
function formatDateTime(value) {
  if (value == null || value === "") return "—";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = d.getDate();
  const month = months[d.getMonth()];
  const year = d.getFullYear();
  let hours = d.getHours();
  const minutes = String(d.getMinutes()).padStart(2, "0");
  const ampm = hours >= 12 ? "PM" : "AM";
  hours = hours % 12 || 12;
  return `${day} ${month}, ${year} ${hours}:${minutes} ${ampm}`;
}

function escapeText(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeText(s).replace(/"/g, "&quot;");
}

async function refresh() {
  try {
    const snap = await api.status();
    state.status = snap;
    updateStatusChip(snap);
    renderDashboard(snap);
    if (state.view === "project") renderProject(snap);
    if (state.view === "approvals") renderApprovals(snap);
    if (state.view === "chat") await loadChat();
  } catch (e) {
    console.error(e);
  }
}

function wire() {
  $$(".nav-btn").forEach((btn) => {
    btn.innerHTML = `${ICONS[btn.dataset.view] || ""}${btn.dataset.label}`;
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  $("#btn-start").onclick = async () => {
    const ok = await confirmModal({
      title: "Start agent run?",
      body: "This starts the docs-first bootstrap / daily loop. Long runs show live status — UI will not freeze.",
      confirmLabel: "Start run",
    });
    if (!ok) return;
    showLoading(true, "Starting run…");
    try {
      await api.startRun();
      toast("Run started", "ok");
      await refresh();
    } catch (e) {
      toast(e.data?.error || e.message || "Start failed", "error");
    } finally {
      showLoading(false);
    }
  };

  $("#btn-stop").onclick = async () => {
    const ok = await confirmModal({
      title: "Stop run?",
      body: "Requests a cooperative stop. The current LLM call may finish first.",
      confirmLabel: "Stop",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.stopRun();
      toast("Stop requested", "warn");
      await refresh();
    } catch (e) {
      toast(e.message, "error");
    }
  };

  $("#btn-save-project").onclick = async () => {
    const path = $("#project-path").value.trim();
    if (!path) return toast("Path required", "warn");
    try {
      await api.setProject(path);
      toast("Project path saved", "ok");
      await refresh();
    } catch (e) {
      toast(e.message, "error");
    }
  };

  $("#chat-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const input = $("#chat-input");
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    const box = $("#chat-messages");
    box.insertAdjacentHTML("beforeend", bubble("user", msg, "webview", new Date().toISOString()));
    box.insertAdjacentHTML("beforeend", bubble("agent", "Thinking…", "webview", new Date().toISOString()));
    box.scrollTop = box.scrollHeight;
    try {
      await api.chat(msg);
      await loadChat(true);
    } catch (e) {
      toast(e.message, "error");
      await loadChat(true);
    }
  };

  $("#btn-save-settings").onclick = async () => {
    const payload = {};
    $$("#settings-grid [data-key]").forEach((el) => {
      payload[el.dataset.key] = el.value;
    });
    try {
      await api.saveSettings(payload);
      await api.startup($("#startup-toggle").checked);
      toast("Settings saved", "ok");
    } catch (e) {
      toast(e.message, "error");
    }
  };

  $("#btn-goto-approvals").onclick = () => setView("approvals");
}

/* ——— Live SSE activity feed ——— */
const live = {
  es: null,
  stickBottom: true,
  seen: new Set(),
  activeAgentEl: null,
};

function phaseClass(phase) {
  return (phase || "Idle").toLowerCase().replace(/\s+/g, "_");
}

function liveIcon(type) {
  const map = {
    agent_execution_started: "🤖",
    agent_execution_completed: "✓",
    tool_usage_started: "🔧",
    tool_usage_finished: "✅",
    tool_usage_error: "⚠️",
    llm_call_started: "💭",
    llm_call_failed: "❌",
    task_started: "▶",
    task_completed: "▣",
    task_failed: "✕",
    crew_kickoff_started: "🚀",
    crew_kickoff_completed: "🏁",
    crew_kickoff_failed: "💥",
    phase: "●",
    connected: "📡",
    error: "❌",
  };
  return map[type] || "•";
}

function isNearBottom(el, px = 48) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < px;
}

function updateJumpBtn() {
  const btn = $("#jump-latest");
  if (!btn) return;
  btn.hidden = live.stickBottom;
}

function appendLiveEvent(ev) {
  if (!ev || !ev.type) return;
  if (ev.type === "cost_update") {
    if (typeof ev.total_cost_usd === "number") setLiveCost(ev.total_cost_usd);
    return;
  }
  if (ev.type === "phase") {
    setLivePhase(ev.phase || ev.message, ev.phase_detail || ev.message || "");
  }
  if (ev.type === "connected") {
    if (ev.phase) setLivePhase(ev.phase, "");
    if (typeof ev.total_cost_usd === "number") setLiveCost(ev.total_cost_usd);
  }

  const id = ev.id || `${ev.type}-${ev.timestamp}-${ev.message}`;
  if (live.seen.has(id)) return;
  live.seen.add(id);
  if (live.seen.size > 400) {
    const first = live.seen.values().next().value;
    live.seen.delete(first);
  }

  const feed = $("#live-feed");
  if (!feed) return;

  // Collapse prior tool_started matching this tool finish
  if (ev.type === "tool_usage_finished" && ev.tool) {
    const open = [...feed.querySelectorAll(".live-item.tool_usage_started[data-tool]")].filter(
      (el) => el.dataset.tool === ev.tool && !el.classList.contains("done")
    );
    const last = open[open.length - 1];
    if (last) {
      last.classList.add("done");
      last.querySelector(".tool-chip")?.classList.add("done");
      const spin = last.querySelector(".spin");
      if (spin) spin.replaceWith(Object.assign(document.createElement("span"), { textContent: "✓" }));
      setTimeout(() => last.classList.add("collapsed"), 700);
    }
  }

  if (ev.type === "agent_execution_completed" && live.activeAgentEl) {
    live.activeAgentEl.classList.remove("active-pulse");
    live.activeAgentEl = null;
  }

  const row = document.createElement("div");
  row.className = `live-item ${ev.type}`;
  row.dataset.id = id;
  if (ev.tool) row.dataset.tool = ev.tool;

  let msgHtml = escapeText(ev.message || ev.type);
  if (ev.type === "tool_usage_started") {
    msgHtml = `<span class="tool-chip"><span class="spin"></span> running ${escapeText(
      ev.tool || "tool"
    )}…</span>`;
  } else if (ev.type === "tool_usage_finished") {
    msgHtml = `<span class="tool-chip done">✓ ${escapeText(ev.tool || "tool")} done</span>`;
    row.classList.add("collapsed");
  } else if (ev.type === "agent_execution_started") {
    row.classList.add("active-pulse");
    live.activeAgentEl = row;
  }

  const metaParts = [formatDateTime(ev.timestamp)];
  if (ev.agent) metaParts.push(ev.agent);
  if (ev.task) metaParts.push(ev.task);
  if (ev.elapsed_ms != null) metaParts.push(`${(ev.elapsed_ms / 1000).toFixed(1)}s`);

  row.innerHTML = `
    <div class="live-ico">${liveIcon(ev.type)}</div>
    <div class="live-body">
      <div class="live-msg">${msgHtml}</div>
      <div class="live-meta">${escapeText(metaParts.join(" · "))}</div>
    </div>`;

  feed.appendChild(row);

  // Cap DOM nodes
  while (feed.children.length > 180) {
    feed.removeChild(feed.firstChild);
  }

  if (live.stickBottom) {
    feed.scrollTop = feed.scrollHeight;
  }
  updateJumpBtn();
}

function startLiveFeed() {
  const conn = $("#live-conn");
  const feed = $("#live-feed");
  const jump = $("#jump-latest");
  if (!feed) return;

  feed.addEventListener("scroll", () => {
    live.stickBottom = isNearBottom(feed);
    updateJumpBtn();
  });
  jump?.addEventListener("click", () => {
    live.stickBottom = true;
    feed.scrollTop = feed.scrollHeight;
    updateJumpBtn();
  });

  const connect = () => {
    if (live.es) {
      try {
        live.es.close();
      } catch (_) {
        /* ignore */
      }
    }
    const es = new EventSource("/api/live-events");
    live.es = es;
    if (conn) {
      conn.textContent = "Connecting…";
      conn.className = "live-conn muted";
    }
    es.onopen = () => {
      if (conn) {
        conn.textContent = "Live";
        conn.className = "live-conn ok";
      }
    };
    es.onmessage = (msg) => {
      try {
        appendLiveEvent(JSON.parse(msg.data));
      } catch (e) {
        console.warn("live event parse", e);
      }
    };
    es.onerror = () => {
      if (conn) {
        conn.textContent = "Reconnecting…";
        conn.className = "live-conn err";
      }
      // EventSource auto-retries; no manual reconnect needed
    };
  };

  connect();
}

async function boot() {
  wire();
  setView("dashboard");
  startLiveFeed();
  await refresh();
  setInterval(refresh, 3000);
}

boot();
