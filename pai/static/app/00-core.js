/* ===================== sign-in: token on this device ===================== */
const LS_KEY_TOKEN = "pai.token";

function readToken() {
  try { return localStorage.getItem(LS_KEY_TOKEN) || ""; } catch (_) { return ""; }
}
function writeToken(t) {
  try { if (t) localStorage.setItem(LS_KEY_TOKEN, t); else localStorage.removeItem(LS_KEY_TOKEN); } catch (_) {}
}
function apiOrigin() {
  try { return new URL(getApiBase() || location.href, location.href).origin; } catch (_) { return location.origin; }
}
function isOwnApiUrl(url) {
  try { return new URL(url, location.href).origin === apiOrigin(); } catch (_) { return false; }
}
function showLogin(msg) {
  const gate = document.getElementById("authGate");
  if (!gate) return;
  gate.hidden = false;
  if (msg) document.getElementById("authMsg").textContent = msg;
  setTimeout(() => document.getElementById("authPassword").focus(), 0);
}

const _nativeFetch = window.fetch.bind(window);
// Every request to our own API carries the token; a 401 means the session is gone: show the login again.
window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : (input && input.url) || "";
  const headers = new Headers(init.headers || {});
  const token = readToken();
  if (token && isOwnApiUrl(url)) headers.set("Authorization", "Bearer " + token);
  const res = await _nativeFetch(input, { ...init, headers });
  if (res.status === 401 && isOwnApiUrl(url) && !url.endsWith("/auth/login")) {
    writeToken("");
    showLogin("Сессия истекла. Войдите снова.");
  }
  return res;
};

(async () => {
  try {
    const r = await _nativeFetch((getApiBase() || "").replace(/\/$/, "") + "/auth/status");
    const s = await r.json();
    if (s.required && !readToken()) {
      // Посетитель без токена не видит запрос пароля: его отправляем на страницу для гостей.
      // Владелец входит по адресу /?owner=1.
      if (new URLSearchParams(location.search).get("owner") === "1") showLogin(s.configured ? "" : "Вход не настроен: задайте PAI_PASSWORD на сервере");
      else location.replace("/guest");
    }
  } catch (_) {}
})();

document.getElementById("authForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("authMsg");
  const btn = document.getElementById("btnLogin");
  msg.textContent = "";
  btn.disabled = true;
  try {
    const res = await _nativeFetch((getApiBase() || "").replace(/\/$/, "") + "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.getElementById("authPassword").value }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
      msg.textContent = detail;
      return;
    }
    writeToken((await res.json()).token);
    location.reload();
  } catch (_) {
    msg.textContent = "Нет связи с сервером";
  } finally {
    btn.disabled = false;
  }
});

"use strict";

/* ===================== API client ===================== */

const LS_KEY_API_BASE = "pai.apiBase";

function getApiBase() {
  const stored = localStorage.getItem(LS_KEY_API_BASE);
  if (stored !== null) return stored;
  return ""; // same origin
}

function setApiBase(v) {
  localStorage.setItem(LS_KEY_API_BASE, v || "");
}

async function api(path, { method = "GET", body = null } = {}) {
  let base = getApiBase();
  // Normalize: strip trailing slash so base + path never produces "//path"
  if (base && base.endsWith("/")) base = base.slice(0, -1);
  const res = await fetch(base + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const detail = (data && data.detail) ? data.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

/* ===================== small utils ===================== */

function fmtTs(ts) {
  if (!ts && ts !== 0) return "—";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtBytes(n) {
  if (n === undefined || n === null) return "—";
  if (n < 1024) return n + " Б";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " КБ";
  return (n / (1024 * 1024)).toFixed(2) + " МБ";
}

function toast(msg, kind = "") {
  const stack = document.getElementById("toastStack");
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function lvlClass(level) {
  const capped = Math.max(0, Math.min(5, level));
  return "lvl-" + capped;
}

const LEVEL_COLORS = ["#4fa8c4", "#64a8ab", "#86a887", "#ab9d64", "#cf9548", "#e8a33d"];
function levelColor(level) {
  return LEVEL_COLORS[Math.max(0, Math.min(5, level))];
}

/* ===================== navigation ===================== */

const LS_KEY_VIEW = "pai.activeView";

/* Space pickers (overview compress, archive search, memory explorer tree) are filled from the server. */
async function loadSpaceSelects() {
  let spaces;
  try { spaces = await api("/memory/spaces"); } catch (_) { return; }
  const pickers = [
    ["compressSpace", "все пространства", "all"],
    ["searchSpace", "все пространства", "all"],
    ["treeSpace", null, null],
  ];
  pickers.forEach(([id, allLabel]) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const prev = sel.value;
    const head = allLabel === null ? "" : `<option value="${id === "searchSpace" ? "" : "all"}">${esc(allLabel)}</option>`;
    sel.innerHTML = head + spaces.map((sp) => `<option value="${esc(sp)}">${esc(sp)}</option>`).join("");
    if (prev && (spaces.includes(prev) || prev === "all")) sel.value = prev;
  });
}

function switchView(name, { persist = true } = {}) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.id === "view-" + name);
  });
  if (persist) localStorage.setItem(LS_KEY_VIEW, name);
  if (name === "overview") loadOverview();
  if (name === "spaces") loadSpaces();
  loadSpaceSelects();
  if (name === "tree") loadTreeRoots();
  if (name === "personas") { loadPersonas(); populateCreatePersonaSelects(); }
  if (name === "rules") loadRules();
  if (name === "skills") loadSkillsView();
  if (name === "functions") loadFunctions();
  if (name === "usage") loadUsage();
  if (name === "actionlog") loadActionLog();
  if (name === "services" && window.loadServicesView) window.loadServicesView();
  if (name === "semantic" && window.loadSemanticView) window.loadSemanticView();
  if (name === "data" && window.loadDataView) window.loadDataView();
  if (name === "embeddings" && window.loadEmbeddingsView) window.loadEmbeddingsView();
  if (name === "transcribe" && window.loadTranscribeView) window.loadTranscribeView();
}

document.querySelectorAll(".nav-item").forEach((el) => {
  el.addEventListener("click", () => {
    switchView(el.dataset.view);
    closeSidebar();
  });
});

/* restore the page the user was on before reload; fall back to chat
   if the stored view no longer exists (e.g. after an app update) */
(function restoreView() {
  const saved = localStorage.getItem(LS_KEY_VIEW);
  if (saved && document.getElementById("view-" + saved)) {
    switchView(saved, { persist: false });
  }
})();

/* ===================== mobile sidebar toggle ===================== */

function openSidebar() {
  document.getElementById("sidebar").classList.add("open");
  document.getElementById("sidebarBackdrop").classList.add("visible");
}
function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebarBackdrop").classList.remove("visible");
}
function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  if (sidebar.classList.contains("open")) closeSidebar();
  else openSidebar();
}
document.getElementById("menuToggle").addEventListener("click", toggleSidebar);
document.getElementById("sidebarBackdrop").addEventListener("click", closeSidebar);

/* ===================== header dropdowns (persona / stats) ===================== */

function closeHeaderMenus(except) {
  document.querySelectorAll(".header-menu.open").forEach((m) => {
    if (m !== except) m.classList.remove("open");
  });
}
document.querySelectorAll(".header-menu").forEach((menu) => {
  const btn = menu.querySelector(".header-menu-btn");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const willOpen = !menu.classList.contains("open");
    closeHeaderMenus();
    menu.classList.toggle("open", willOpen);
  });
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".header-menu")) closeHeaderMenus();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeHeaderMenus();
});

/* ===================== connection / header stack ===================== */

async function checkConnection() {
  const dot = document.getElementById("connDot");
  const label = document.getElementById("connLabel");
  try {
    await api("/health");
    dot.className = "status-dot ok";
    label.textContent = getApiBase() || location.origin;
    return true;
  } catch (e) {
    dot.className = "status-dot err";
    label.textContent = "нет соединения";
    return false;
  }
}

function renderStackViz(el, byLevel, labelEl) {
  el.innerHTML = "";
  const levels = Object.keys(byLevel).map(Number).sort((a, b) => a - b);
  const total = levels.reduce((s, l) => s + byLevel[l], 0);
  if (total === 0) {
    el.innerHTML = `<div class="seg" style="width:100%; background:var(--surface-3);"></div>`;
    if (labelEl) labelEl.textContent = "0 узлов";
    return;
  }
  levels.forEach((l) => {
    const seg = document.createElement("div");
    const pct = (byLevel[l] / total) * 100;
    seg.className = "seg";
    seg.style.width = pct + "%";
    seg.style.background = levelColor(l);
    seg.title = `level ${l}: ${byLevel[l]}`;
    el.appendChild(seg);
  });
  if (labelEl) labelEl.textContent = `${total} узлов, ${levels.length} уровн${levels.length === 1 ? "ень" : "я"}`;
}

async function refreshHeaderStack() {
  try {
    const stats = await api("/memory/stats");
    renderStackViz(document.getElementById("headerStack"), stats.by_level || {}, document.getElementById("headerStackLabel"));
  } catch (_) { /* silent in header */ }
}

