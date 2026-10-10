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

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s === undefined || s === null ? "" : String(s);
  return d.innerHTML;
}

function toast(msg, kind = "") {
  const stack = document.getElementById("toastStack");
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/* ===================== tiny markdown renderer (no deps) ===================== */
/* Deliberately minimal: headings, bold/italic, inline code, fenced code
   blocks, lists, blockquotes, links, hr, tables. Escapes HTML first so raw
   markdown text can never inject markup. */
function renderMarkdown(src) {
  if (!src) return "";
  const codeBlocks = [];
  let text = src.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000CODEBLOCK${idx}\u0000`;
  });

  text = esc(text);
  // restore code blocks (already escaped internally)
  text = text.replace(/\u0000CODEBLOCK(\d+)\u0000/g, (_, i) => codeBlocks[Number(i)]);
  // re-escape stray \u0000 markers that esc() would have mangled — none expected, no-op otherwise

  const lines = text.split("\n");
  const out = [];
  let inUl = false, inOl = false, inQuote = false;
  const closeLists = () => {
    if (inUl) { out.push("</ul>"); inUl = false; }
    if (inOl) { out.push("</ol>"); inOl = false; }
    if (inQuote) { out.push("</blockquote>"); inQuote = false; }
  };
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  for (const raw of lines) {
    if (raw.startsWith("\u0000CODEBLOCK") || /^<pre>/.test(raw)) { closeLists(); out.push(raw); continue; }
    const line = raw;
    if (/^\s*$/.test(line)) { closeLists(); continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
      closeLists();
      const lvl = m[1].length;
      out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { closeLists(); out.push("<hr/>"); continue; }
    if ((m = line.match(/^&gt;\s?(.*)$/))) {
      if (!inQuote) { closeLists(); out.push("<blockquote>"); inQuote = true; }
      out.push(inline(m[1]) + "<br/>");
      continue;
    }
    if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (inOl) { out.push("</ol>"); inOl = false; }
      if (inQuote) { out.push("</blockquote>"); inQuote = false; }
      if (!inUl) { out.push("<ul>"); inUl = true; }
      out.push(`<li>${inline(m[1])}</li>`);
      continue;
    }
    if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      if (inUl) { out.push("</ul>"); inUl = false; }
      if (inQuote) { out.push("</blockquote>"); inQuote = false; }
      if (!inOl) { out.push("<ol>"); inOl = true; }
      out.push(`<li>${inline(m[1])}</li>`);
      continue;
    }
    closeLists();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeLists();
  return out.join("\n");
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

function switchView(name, { persist = true } = {}) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.id === "view-" + name);
  });
  if (persist) localStorage.setItem(LS_KEY_VIEW, name);
  if (name === "overview") loadOverview();
  if (name === "tree") loadTreeRoots();
  if (name === "personas") { loadPersonas(); populateCreatePersonaSelects(); }
  if (name === "rules") loadRules();
  if (name === "skills") loadSkillsView();
  if (name === "functions") loadFunctions();
  if (name === "usage") loadUsage();
  if (name === "actionlog") loadActionLog();
  if (name === "services" && window.loadServicesView) window.loadServicesView();
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

/* ===================== CHAT ===================== */

let personasCache = [];
const chatHistory = []; // {role, text, tool_trace, usage}

/* Memory space of the active persona. A persona with allow_spaces
   (e.g. work -> ["work","shared"]) is rejected by the server (403) in any
   other space, so the chat must talk to the persona's own space; personas
   with no restriction use "personal". */
function chatSpaceFor(personaId) {
  const p = personasCache.find((x) => x.persona_id === personaId);
  if (!p) return "personal";
  const allow = p.allow_spaces || [], deny = p.deny_spaces || [];
  if (!allow.length) return deny.includes("personal") ? "default" : "personal";
  if (allow.includes("personal") && !deny.includes("personal")) return "personal";
  return allow.find((sp) => !deny.includes(sp)) || "personal";
}
// The memory space the chat talks to. Chosen in #chatSpace; the choice is remembered per persona
// in this browser only (a per-viewer convenience). The server still enforces allow/deny lists.
function currentChatSpace() {
  const sel = document.getElementById("chatSpace");
  if (sel && sel.value) return sel.value;
  return chatSpaceFor(document.getElementById("chatPersona").value || "brain");
}

const chatSpaceKey = (personaId) => `pai.chatSpace.${personaId}`;

async function populateChatSpaces() {
  const sel = document.getElementById("chatSpace");
  if (!sel) return;
  const persona_id = document.getElementById("chatPersona").value || "brain";
  const p = personasCache.find((x) => x.persona_id === persona_id);
  const allow = (p && p.allow_spaces) || [];
  const deny = (p && p.deny_spaces) || [];
  let options;
  if (allow.length) {
    options = allow.filter((sp) => !deny.includes(sp));
  } else {
    // unrestricted persona: any space that exists in memory is available
    let known = [];
    try { known = Object.keys(await api("/spaces")); } catch (_) { known = []; }
    options = ["personal", ...known].filter((sp, i, a) => a.indexOf(sp) === i && !deny.includes(sp));
  }
  let preferred = null;
  try { preferred = localStorage.getItem(chatSpaceKey(persona_id)); } catch (_) { /* storage unavailable */ }
  const value = (preferred && options.includes(preferred)) ? preferred : chatSpaceFor(persona_id);
  if (!options.includes(value)) options.unshift(value);
  sel.innerHTML = options.map((sp) => `<option value="${esc(sp)}">${esc(sp)}</option>`).join("");
  sel.value = value;
}

async function loadPersonasForChat() {
  try {
    personasCache = await api("/personas");
  } catch (e) {
    toast("Не удалось загрузить персоны: " + e.message, "err");
    return;
  }
  const sel = document.getElementById("chatPersona");
  const prev = sel.value;  // keep the user's selection when the list is refreshed
  sel.innerHTML = personasCache.map((p) => `<option value="${esc(p.persona_id)}">${esc(p.name)}</option>`).join("");
  if (prev && personasCache.some((p) => p.persona_id === prev)) sel.value = prev;
  updatePersonaMenuLabel();
  await populateChatSpaces();
}

function updatePersonaMenuLabel() {
  const sel = document.getElementById("chatPersona");
  if (!sel) return;
  const opt = sel.options[sel.selectedIndex];
  if (opt) sel.title = opt.textContent;
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* fall through to the legacy path */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
  ta.remove();
  return ok;
}

function attachCopyButton(msgDiv, getText) {
  // Re-attach on every call: a streamed message's button must read the final text.
  msgDiv.querySelectorAll(".msg-copy").forEach((el) => el.remove());
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-copy";
  btn.title = "Копировать сообщение";
  btn.setAttribute("aria-label", "Копировать сообщение");
  btn.textContent = "⧉";
  btn.addEventListener("click", async () => {
    const ok = await copyText(getText());
    btn.textContent = ok ? "✓" : "✗";
    setTimeout(() => { btn.textContent = "⧉"; }, 1200);
  });
  msgDiv.appendChild(btn);
}

function renderQuickReplies(list) {
  const box = document.getElementById("quickReplies");
  box.innerHTML = "";
  (list || []).filter(Boolean).slice(0, 2).forEach((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn sm quick-reply";
    b.textContent = t;
    b.addEventListener("click", () => sendChat(t));
    box.appendChild(b);
  });
  box.hidden = box.children.length === 0;
}

// opts.scroll (default true): scroll the chat to the bottom after appending.
// Streamed assistant answers pass scroll:false so the reader keeps their place in the history.
function appendMessage(role, text, meta, attachments, reasoning, opts = {}) {
  const wrap = document.getElementById("chatMessages");
  const empty = wrap.querySelector(".empty-state");
  if (empty) empty.remove();

  const div = document.createElement("div");
  div.className = "msg " + role;
  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (attachments && attachments.length) {
    const att = document.createElement("div");
    att.className = "attachments";
    attachments.forEach((dataUrl) => {
      const img = document.createElement("img");
      img.src = dataUrl;
      att.appendChild(img);
    });
    bubble.appendChild(att);
  }

  if (role === "assistant") {
    const md = document.createElement("div");
    md.className = "md";
    md.innerHTML = renderMarkdown(text);
    bubble.appendChild(md);
    if (reasoning && reasoning.trim()) {
      // finished (history) message: show the saved reasoning, collapsed
      const blk = ensureReasoningBlock(div_holder(div, bubble));
      blk.classList.remove("streaming");
      blk.classList.add("collapsed");
      blk.querySelector(".reasoning-block-body").textContent = reasoning;
    }
  } else if (text) {
    const textNode = document.createElement("div");
    textNode.textContent = text;
    bubble.appendChild(textNode);
  }
  div.appendChild(bubble);

  if (meta) {
    const m = document.createElement("div");
    m.className = "meta";
    m.textContent = meta;
    div.appendChild(m);
  }

  attachCopyButton(div, () => text);
  wrap.appendChild(div);
  if (opts.scroll !== false) wrap.scrollTop = wrap.scrollHeight;
  return div;
}

// A service run is an event, not part of the user's dialogue: rendered apart, with its source.
function appendServiceEvent(m, persona_id) {
  const wrap = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = "msg service-event " + (m.role === "assistant" ? "assistant" : "user");
  const label = document.createElement("div");
  label.className = "service-event-label";
  const who = m.role === "assistant" ? "результат" : "задание";
  label.textContent = `Служба «${m.origin_name || "без названия"}» · ${who}`;
  div.appendChild(label);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (m.role === "assistant") {
    const md = document.createElement("div");
    md.className = "md";
    md.innerHTML = renderMarkdown(m.text || "");
    bubble.appendChild(md);
  } else {
    bubble.textContent = m.text || "";
  }
  div.appendChild(bubble);
  if (m.occurred_at) {
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${persona_id} · ${fmtTs(m.occurred_at)} · событие службы`;
    div.appendChild(meta);
  }
  attachCopyButton(div, () => m.text || "");
  wrap.appendChild(div);
}

function renderLiveToolCall(container, name, args) {
  const c = document.createElement("div");
  c.className = "tool-call live";
  c.dataset.tool = name;
  c.innerHTML = `<span class="tool-spin"></span><span class="tname">${esc(name)}</span>(${esc(JSON.stringify(args))})<span class="tool-live-label"> · выполняется…</span>`;
  container.appendChild(c);
  const wrap = document.getElementById("chatMessages");
  wrap.scrollTop = wrap.scrollHeight;
  return c;
}

function resolveLiveToolCall(el, result) {
  if (!el) return;
  el.classList.remove("live");
  const label = el.querySelector(".tool-live-label");
  if (label) label.remove();
  const spinner = el.querySelector(".tool-spin");
  if (spinner) spinner.remove();
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(result, null, 2);
  pre.style.display = "none";
  el.appendChild(pre);
  el.addEventListener("click", () => { pre.style.display = pre.style.display === "none" ? "block" : "none"; });
}

function appendToolTrace(div, trace) {
  if (!trace || !trace.length) return;
  const tt = document.createElement("div");
  tt.className = "tool-trace";
  trace.forEach((call) => {
    const c = document.createElement("div");
    c.className = "tool-call";
    c.innerHTML = `<span class="tname">${esc(call.name)}</span>(${esc(JSON.stringify(call.arguments))})`;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(call.result, null, 2);
    pre.style.display = "none";
    c.appendChild(pre);
    c.addEventListener("click", () => { pre.style.display = pre.style.display === "none" ? "block" : "none"; });
    tt.appendChild(c);
  });
  div.appendChild(tt);
}

/* ===================== KV-cache status ===================== */

function cachePillClass(pressure) {
  if (pressure >= 3) return "crit";
  if (pressure >= 1) return "warn";
  return "ok";
}

function renderCacheStatus(status) {
  if (!status) return;
  const pill = document.getElementById("cachePill");
  const detail = document.getElementById("cacheDetail");
  const usagePct = Math.round((status.usage_ratio ?? 0) * 100);
  const freePct = Math.round((status.free_ratio ?? 0) * 100);
  const targetPct = Math.round((status.target_free_ratio ?? 0.5) * 100);
  const cls = cachePillClass(status.pressure_level ?? 0);

  if (pill) {
    pill.className = "cache-pill " + cls;
    pill.textContent = `KV-кеш ${usagePct}% занято`;
  }
  if (detail) {
    const srcLabel = status.source === "vllm_metrics" ? "реальные метрики vLLM" : "локальная оценка (GPU-хост недоступен)";
    detail.innerHTML = `
      занято: <b style="color:var(--text)">${usagePct}%</b> · свободно: <b style="color:var(--success)">${freePct}%</b> (цель: не менее ${targetPct}%)<br/>
      источник: ${esc(srcLabel)}
      ${status.auto_compressed ? `<div class="compressed-note">⚙ авто-сжатие запущено: +${status.compressed_nodes} узел(ов)</div>` : ""}
    `;
  }

  const card = document.getElementById("cacheStatusCard");
  if (card) {
    card.innerHTML = `
      <div class="cache-status-row">
        <span class="cache-status-num" style="color:${cls === "crit" ? "var(--danger)" : cls === "warn" ? "var(--accent-live)" : "var(--success)"}">${usagePct}%</span>
        <div class="cache-bar-track">
          <div class="cache-bar-fill" style="width:${usagePct}%; background:${cls === "crit" ? "var(--danger)" : cls === "warn" ? "var(--accent-live)" : "var(--success)"}"></div>
          <div class="cache-bar-target" style="left:${100 - targetPct}%"></div>
        </div>
        <span class="tag ${cls === "ok" ? "" : "lvl"}" style="${cls !== "ok" ? `color:${cls === "crit" ? "var(--danger)" : "var(--accent-live)"}; border-color:${cls === "crit" ? "var(--danger)" : "var(--accent-live)"};` : ""}">pressure ${status.pressure_level ?? 0}</span>
      </div>
      <div style="margin-top:10px; font-size:12px; color:var(--text-dim);">
        источник: ${esc(status.source === "vllm_metrics" ? "реальные метрики vLLM (/metrics)" : "локальная оценка по бюджету персоны")} ·
        цель: держать не менее ${targetPct}% кеша свободным для нового диалога
        ${status.auto_compressed ? `<div class="compressed-note">⚙ на последнем ходе сжатие сработало автоматически: +${status.compressed_nodes} узел(ов)</div>` : ""}
      </div>
    `;
  }
}

let cachePollTimer = null;
async function loadCacheStatus() {
  try {
    const status = await api("/cache/status");
    renderCacheStatus(status);
  } catch (e) {
    const card = document.getElementById("cacheStatusCard");
    if (card) card.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}
function startCachePolling() {
  if (cachePollTimer) return;
  loadCacheStatus();
  cachePollTimer = setInterval(loadCacheStatus, 8000);
}

/* usage widget: collapsible, collapsed by default, persists via localStorage */
const LS_KEY_USAGE_COLLAPSED = "pai.usageCollapsed.v2";
function initUsageWidget() {
  // Виджет теперь внутри #statsModal: модалка сама управляет
  // показом/скрытием, а сам виджет внутри неё всегда развёрнут.
  const widget = document.getElementById("usageWidget");
  if (widget) widget.classList.remove("collapsed");
}

function renderBudget(preview) {
  // The chat widget no longer contains the legacy #budgetBars element.
  // Keep this function backwards-compatible for older layouts, but never
  // dereference missing DOM nodes: a usage response must not turn into a
  // chat error just because an optional visualization is absent.
  const container = document.getElementById("budgetBars");
  const freeEl = document.getElementById("budgetFree");
  if (!preview || !preview.budget) {
    if (container) container.innerHTML = `<div class="empty-state" style="padding:16px 8px;">Нет данных</div>`;
    if (freeEl) freeEl.textContent = "";
    return;
  }
  const b = preview.budget;
  const categories = [
    ["system_core", "system core"],
    ["persona_header", "persona header"],
    ["current_time", "current time"],
    ["skill_triggers", "skill triggers"],
    ["tree_tops", "tree tops"],
    ["tail", "tail"],
    ["retrieved_fragments", "retrieved fragments"],
    ["reasoning_reserve", "reasoning reserve"],
  ];
  const colors = ["#4fa8c4", "#5fb0a6", "#79ad82", "#9aab6b", "#b39c5f", "#cf9548", "#e0a13f", "#e8a33d"];
  const total = b.total_budget || categories.reduce((s, [k]) => s + (b[k] || 0), 0);

  const track = document.createElement("div");
  track.className = "budget-bar-track";
  categories.forEach(([k], i) => {
    const seg = document.createElement("div");
    seg.className = "seg";
    seg.style.width = (((b[k] || 0) / total) * 100) + "%";
    seg.style.background = colors[i];
    track.appendChild(seg);
  });

  const rows = categories.map(([k, label], i) => `
    <div class="budget-row">
      <span><span class="dot" style="background:${colors[i]}"></span>${label}</span>
      <span class="mono">${b[k] ?? 0}</span>
    </div>`).join("");

  if (container) {
    container.innerHTML = "";
    container.appendChild(track);
    const rowsWrap = document.createElement("div");
    rowsWrap.innerHTML = rows;
    container.appendChild(rowsWrap);
  }

  if (freeEl) {
    freeEl.innerHTML = `свободно: <b style="color:var(--success)">${preview.free_space_est ?? "—"}</b> / ${total} токенов<br/>
      system≈${preview.system_prompt_tokens_est ?? "—"} · user≈${preview.user_tokens_est ?? "—"} · reply≈${preview.reply_tokens_est ?? "—"}`;
  }
}

function fmtChatNum(v) {
  return v == null || v === "" ? "—" : Number(v).toLocaleString("ru-RU");
}
function fmtUsd(v) {
  if (v == null || v === "") return "—";
  return "$" + Number(v).toFixed(6);
}
function fmtSec(v) {
  if (v == null || v === "") return "—";
  return Number(v) < 1 ? `${Math.round(Number(v) * 1000)} ms` : `${Number(v).toFixed(2)} s`;
}
function renderChatUsage(turn, preview, ttftSeconds) {
  turn = turn || {};
  const ctx = turn.context_length;
  const input = Number(turn.input_tokens || 0);
  const util = turn.context_utilization != null
    ? Number(turn.context_utilization)
    : (ctx ? input / Number(ctx) : null);
  const speed = Number(turn.generation_tokens_per_second || 0);
  const latency = Number(turn.latency_seconds || 0);
  const inputPrice = turn.events?.length ? turn.events[turn.events.length - 1].prompt_price_per_1m : null;
  const outputPrice = turn.events?.length ? turn.events[turn.events.length - 1].completion_price_per_1m : null;

  const set = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  set("usageWidgetSummary", ctx ? `${fmtChatNum(input)} / ${fmtChatNum(ctx)} · ${util != null ? (util * 100).toFixed(1) + "%" : "—"}` : `${fmtChatNum(input)} input · ${fmtChatNum(turn.output_tokens)} output`);
  set("cuContext", ctx ? `${fmtChatNum(input)} / ${fmtChatNum(ctx)}` : fmtChatNum(input));
  set("cuContextHint", util != null ? `${(util * 100).toFixed(1)}% окна контекста` : "context window неизвестен");
  set("cuInput", fmtChatNum(input));
  set("cuOutput", fmtChatNum(turn.output_tokens));
  set("cuReasoning", fmtChatNum(turn.reasoning_tokens));
  set("cuCached", fmtChatNum(turn.cached_input_tokens));
  set("cuSpeed", speed ? `${speed.toFixed(1)} ток/с` : "—");
  set("cuTtft", ttftSeconds != null ? fmtSec(ttftSeconds) : "—");
  set("cuCost", fmtUsd(turn.cost_usd));
  set("cuModel", turn.model || "—");
  set("cuTotal", fmtChatNum(turn.total_tokens));
  set("cuCalls", fmtChatNum(turn.llm_calls));
  set("cuLatency", fmtSec(latency));
  set("cuInputPrice", inputPrice != null ? `$${Number(inputPrice).toFixed(4)} / 1M` : "—");
  set("cuOutputPrice", outputPrice != null ? `$${Number(outputPrice).toFixed(4)} / 1M` : "—");
  const _statsLbl = document.getElementById("statsCompactLabel");
  if (_statsLbl) {
    _statsLbl.textContent = (input || turn.output_tokens)
      ? `${fmtChatNum(input)}\u2191 ${fmtChatNum(turn.output_tokens)}\u2193`
      : "\u2014";
  }

  const free = document.getElementById("budgetFree");
  if (free) {
    const parts = [];
    if (preview) {
      parts.push(`Оценка бюджета: system≈${fmtChatNum(preview.system_prompt_tokens_est)} · user≈${fmtChatNum(preview.user_tokens_est)} · reply≈${fmtChatNum(preview.reply_tokens_est)}`);
      if (preview.budget?.total_budget) parts.push(`бюджет ${fmtChatNum(preview.budget.total_budget)} · свободно ${fmtChatNum(preview.free_space_est)}`);
    }
    if (turn.events?.length > 1) parts.push(`вызовы: ${turn.events.map((e,i) => `#${i+1} ${e.input_tokens}→${e.output_tokens}`).join(" · ")}`);
    free.innerHTML = parts.join("<br/>") || "Данных пока нет";
  }
}

function setTokSpeed(text) {
  const el = document.getElementById("tokSpeed");
  if (el) el.textContent = text || "";
}

// Session totals live in memory for this page load only (not persisted).
const sessionTokenUsage = { input: 0, output: 0, contextWindow: null };

function renderChatTokUsage(turn, preview) {
  const el = document.getElementById("chatTokUsage");
  if (!el) return;
  if (turn) {
    sessionTokenUsage.input += Number(turn.input_tokens || 0);
    sessionTokenUsage.output += Number(turn.output_tokens || 0);
  }
  const ctx = (turn && turn.context_length) || (preview && preview.context_window) || sessionTokenUsage.contextWindow;
  if (ctx) sessionTokenUsage.contextWindow = Number(ctx);
  const win = sessionTokenUsage.contextWindow ? fmtChatNum(sessionTokenUsage.contextWindow) : "—";
  const last = turn
    ? `ответ ↑${fmtChatNum(turn.input_tokens)} ↓${fmtChatNum(turn.output_tokens)}`
    : "ответ —";
  const total = `сессия ↑${fmtChatNum(sessionTokenUsage.input)} ↓${fmtChatNum(sessionTokenUsage.output)}`;
  el.textContent = `окно ${win} · ${last} · ${total}`;
}

// rough token estimate for live speed display (chars/4 ~ tokens, matches
// backend's estimate_tokens heuristic closely enough for a live readout)
function estTokens(s) {
  return Math.max(1, Math.round((s || "").length / 4));
}

/* ===================== theme toggle ===================== */

function applyThemeIcon() {
  const isLight = document.documentElement.getAttribute("data-theme") !== "dark";
  document.getElementById("themeToggle").textContent = isLight ? "🌙" : "☀️";
}

document.getElementById("themeToggle").addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  const next = current === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("pai.theme", next);
  applyThemeIcon();
});

applyThemeIcon();

/* ===================== image attachments ===================== */

// Pending attachments for the next outgoing message: array of data URLs
// (already base64-encoded, ready to drop straight into the request body
// and into an <img src>). Cleared after each send.
let pendingAttachments = [];

function renderAttachmentPreviews() {
  const container = document.getElementById("chatAttachments");
  container.innerHTML = "";
  pendingAttachments.forEach((dataUrl, i) => {
    const thumb = document.createElement("div");
    thumb.className = "attachment-thumb";
    const img = document.createElement("img");
    img.src = dataUrl;
    thumb.appendChild(img);
    const rm = document.createElement("button");
    rm.className = "remove";
    rm.type = "button";
    rm.textContent = "×";
    rm.title = "Убрать";
    rm.addEventListener("click", () => {
      pendingAttachments.splice(i, 1);
      renderAttachmentPreviews();
    });
    thumb.appendChild(rm);
    container.appendChild(thumb);
  });
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

document.getElementById("btnAttachImage").addEventListener("click", () => {
  document.getElementById("chatImageInput").click();
});

document.getElementById("chatImageInput").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  e.target.value = ""; // allow re-selecting the same file next time
  for (const file of files) {
    if (!file.type.startsWith("image/")) continue;
    try {
      const dataUrl = await readFileAsDataUrl(file);
      pendingAttachments.push(dataUrl);
    } catch (err) {
      toast("Не удалось прочитать изображение: " + err.message, "err");
    }
  }
  renderAttachmentPreviews();
});

// Paste an image straight from the clipboard into the chat input.
document.getElementById("chatInput").addEventListener("paste", async (e) => {
  const items = Array.from(e.clipboardData ? e.clipboardData.items : []);
  const imageItems = items.filter((it) => it.type && it.type.startsWith("image/"));
  if (!imageItems.length) return;
  e.preventDefault();
  for (const it of imageItems) {
    const file = it.getAsFile();
    if (!file) continue;
    try {
      const dataUrl = await readFileAsDataUrl(file);
      pendingAttachments.push(dataUrl);
    } catch (err) {
      toast("Не удалось прочитать изображение: " + err.message, "err");
    }
  }
  renderAttachmentPreviews();
});

/* ===================== reasoning display ===================== */

// ensureReasoningBlock() expects an element exposing .querySelector(".md" / ".bubble");
// while a message is still being built (not yet attached) wrap bubble in a tiny shim.
function div_holder(div, bubble) {
  return { querySelector: (sel) => (sel === ".bubble" ? bubble : bubble.querySelector(sel)) };
}

function ensureReasoningBlock(assistantDiv) {
  let block = assistantDiv.querySelector(".reasoning-block");
  if (block) return block;
  block = document.createElement("div");
  const _collapsedByDefault = window.matchMedia("(max-width: 720px)").matches;
  block.className = "reasoning-block streaming" + (_collapsedByDefault ? " collapsed" : "");
  const head = document.createElement("div");
  head.className = "reasoning-block-head";
  head.innerHTML = `<span class="chevron">▾</span><span>Размышление модели</span>`;
  const body = document.createElement("div");
  body.className = "reasoning-block-body";
  head.addEventListener("click", () => block.classList.toggle("collapsed"));
  block.appendChild(head);
  block.appendChild(body);
  // Reasoning always precedes the final answer, so it goes above the
  // markdown content — but after any attachment thumbnails.
  const mdEl = assistantDiv.querySelector(".md");
  assistantDiv.querySelector(".bubble").insertBefore(block, mdEl);
  return block;
}

/* ===================== chat send ===================== */

let chatTurnStartedAt = 0;
let chatFirstTokenAt = null;

async function sendChat(override) {
  if (document.getElementById("btnSend").disabled) return;  // Ctrl+Enter used to bypass the disabled button
  chatTurnStartedAt = performance.now();
  chatFirstTokenAt = null;
  const input = document.getElementById("chatInput");
  const fromQuick = typeof override === "string";
  const text = (fromQuick ? override : input.value).trim();
  if (!text && !pendingAttachments.length) return;
  renderQuickReplies([]);
  const persona_id = document.getElementById("chatPersona").value || "brain";
  const space_id = currentChatSpace();
  const images = pendingAttachments.slice();

  appendMessage(
    "user", text, `${persona_id} · ${space_id} · ${fmtTs(Date.now() / 1000)}`, images,
  );
  if (!fromQuick) {
    input.value = "";
    input.style.height = "auto";
  }
  pendingAttachments = [];
  renderAttachmentPreviews();

  const btn = document.getElementById("btnSend");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span>`;
  setTokSpeed("генерация…");

  const base = getApiBase();
  let assistantDiv = null;
  let mdEl = null;
  let reasoningBlock = null;
  let reasoningBodyEl = null;
  let toolTraceEl = null;
  let liveToolEls = {}; // name -> most recent open <div class="tool-call live">
  let fullText = "";
  let fullReasoning = "";
  let finalVerification = null;
  let firstChunkAt = null;
  let lastChunkAt = null;
  let chunkCount = 0;

  function ensureAssistantDiv() {
    if (!assistantDiv) {
      assistantDiv = appendMessage("assistant", "", `${persona_id} · печатает…`, null, null, { scroll: false });
      assistantDiv.classList.add("streaming");
      mdEl = assistantDiv.querySelector(".md");
    }
    return assistantDiv;
  }

  function ensureToolTraceEl() {
    ensureAssistantDiv();
    if (!toolTraceEl) {
      toolTraceEl = document.createElement("div");
      toolTraceEl.className = "tool-trace";
      assistantDiv.parentNode.insertBefore(toolTraceEl, assistantDiv);
      // Tool calls hidden in the chat UI. Full request/response details
      // live on the Action Log page. For local debugging, set
      // localStorage.pai.showToolCalls = "1" and reload.
      const hideCb = document.getElementById("hideToolCalls");
      const hide = hideCb ? hideCb.checked : localStorage.getItem("pai.hideToolCalls") !== "0";
      if (hide) toolTraceEl.style.display = "none";
    }
    return toolTraceEl;
  }

  assistantDiv = appendMessage("assistant", "", `${persona_id} · печатает…`, null, null, { scroll: false });
    assistantDiv.classList.add("streaming");
    mdEl = assistantDiv.querySelector(".md");
  try {
    let streamBase = getApiBase();
    if (streamBase && streamBase.endsWith("/")) streamBase = streamBase.slice(0, -1);
    const res = await fetch(streamBase + "/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, persona_id, space_id, images }),
    });
    if (!res.ok || !res.body) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
      throw new Error(detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let finalTrace = [];
    let finalPreview = null;
    let finalCacheStatus = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // Normalize SSE line endings so LF/CRLF providers behave identically.
      buf = buf.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      const parts = buf.split("\n\n");
      buf = parts.pop(); // last event may be incomplete
      for (const part of parts) {
        const dataLines = part
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim());
        if (!dataLines.length) continue;
        let evt;
        try { evt = JSON.parse(dataLines.join("\n")); } catch (_) { continue; }

        if (evt.type === "error") {
          throw new Error(evt.message || "ошибка потока");
        }
        if (evt.type === "tool_call") {
          const trace = ensureToolTraceEl();
          const key = evt.name + "::" + JSON.stringify(evt.arguments);
          liveToolEls[key] = renderLiveToolCall(trace, evt.name, evt.arguments);
          continue;
        }
        if (evt.type === "tool_result") {
          const key = evt.name + "::" + JSON.stringify(evt.arguments);
          resolveLiveToolCall(liveToolEls[key], evt.result);
          delete liveToolEls[key];

          // После propose_function сервер уже сохранил draft в functions.json.
          // Немедленно перечитываем /functions, чтобы draft появился в UI
          // без ручного обновления страницы.
          if (evt.name === "propose_function" && evt.result && evt.result.created) {
            const functionView = document.getElementById("view-functions");
            if (functionView && functionView.classList.contains("active")) {
              await loadFunctions();
            }
          }
          continue;
        }
        if (evt.type === "delta_reset") {
          // the model streamed a preamble and then asked for tools: drop it
          fullText = "";
          if (mdEl) mdEl.innerHTML = "";
          continue;
        }
        if (evt.type === "check_failed") {
          // an answer that failed the answer check never stays in the chat: keep it in reasoning
          ensureAssistantDiv();
          if (!reasoningBlock) {
            reasoningBlock = ensureReasoningBlock(assistantDiv);
            reasoningBodyEl = reasoningBlock.querySelector(".reasoning-block-body");
          }
          const note = `— Ответ не прошёл проверку (попытка ${evt.attempt}) —\n${evt.text}` +
            (evt.report ? `\nОтчёт проверки: ${evt.report}` : "");
          fullReasoning += (fullReasoning ? "\n\n" : "") + note;
          reasoningBodyEl.textContent = fullReasoning;
          fullText = "";
          if (mdEl) mdEl.innerHTML = "";
          continue;
        }
        if (evt.type === "reasoning_delta") {
          ensureAssistantDiv();
          if (!reasoningBlock) {
            reasoningBlock = ensureReasoningBlock(assistantDiv);
            reasoningBodyEl = reasoningBlock.querySelector(".reasoning-block-body");
          }
          fullReasoning += evt.text;
          reasoningBodyEl.textContent = fullReasoning;
          const wrap = document.getElementById("chatMessages");
          wrap.scrollTop = wrap.scrollHeight;
          // Allow the browser to paint before processing another buffered SSE event.
          await new Promise(requestAnimationFrame);
          continue;
        }
        if (evt.type === "delta") {
          ensureAssistantDiv();
          if (reasoningBlock) reasoningBlock.classList.remove("streaming");
          const now = performance.now();
          if (firstChunkAt === null) firstChunkAt = now;
          if (chatFirstTokenAt === null) chatFirstTokenAt = now;
          lastChunkAt = now;
          chunkCount++;
          fullText += evt.text;
          mdEl.innerHTML = renderMarkdown(fullText);

          if (firstChunkAt !== null && lastChunkAt > firstChunkAt) {
            const elapsedS = (lastChunkAt - firstChunkAt) / 1000;
            const toks = estTokens(fullText);
            const tps = elapsedS > 0 ? (toks / elapsedS) : 0;
            setTokSpeed(`${tps.toFixed(1)} ток/с`);
          }

          // reader.read() can resolve immediately for already-buffered chunks.
          // Force a frame boundary so each delta becomes visibly incremental.
          await new Promise(requestAnimationFrame);
        }
        if (evt.type === "tool_trace") {
          finalTrace = evt.trace || [];
        }
        if (evt.type === "cache_status") {
          finalCacheStatus = evt.status || null;
        }
        if (evt.type === "done") {
          finalPreview = evt.live_context_preview;
          if (evt.turn_usage) renderChatUsage(evt.turn_usage, finalPreview, chatFirstTokenAt != null ? (chatFirstTokenAt - chatTurnStartedAt) / 1000 : null);
          renderChatTokUsage(evt.turn_usage || null, finalPreview);
          if (evt.cache_status) finalCacheStatus = evt.cache_status;
          if (evt.reply) fullText = evt.reply;  // authoritative final text
          if (evt.verification) finalVerification = evt.verification;
          if (!fullReasoning && evt.reasoning) {
            fullReasoning = evt.reasoning;
            // reasoning that never arrived as deltas: render it now (was silently dropped before)
            reasoningBlock = ensureReasoningBlock(ensureAssistantDiv());
            reasoningBodyEl = reasoningBlock.querySelector(".reasoning-block-body");
            reasoningBodyEl.textContent = fullReasoning;
          }
        }
      }
    }

    if (reasoningBlock) {
      reasoningBlock.classList.remove("streaming");
      if (!fullReasoning.trim()) reasoningBlock.remove();
    }

    if (!assistantDiv) {
      assistantDiv = appendMessage("assistant", fullText, `${persona_id}`);
      mdEl = assistantDiv.querySelector(".md");
    } else {
      mdEl.innerHTML = renderMarkdown(fullText);
      assistantDiv.classList.remove("streaming");
      const toolNote = finalTrace.length ? ` · ${finalTrace.length} вызов(ов) инструментов` : "";
      const metaEl = assistantDiv.querySelector(".meta");
      if (metaEl) metaEl.textContent = `${persona_id}${toolNote}`;
    }
    if (assistantDiv) attachCopyButton(assistantDiv, () => fullText);
    // quick replies: the checker's suggestions for the user's likely next message (last attempt)
    const lastCheck = finalVerification && finalVerification.length ? finalVerification[finalVerification.length - 1] : null;
    renderQuickReplies(lastCheck ? lastCheck.suggested_replies : []);
    // tool calls were already rendered live during the stream; nothing more to append here
    chatHistory.push({ role: "assistant", tool_trace: finalTrace });
    if (finalPreview) renderBudget(finalPreview);
    if (finalPreview && !document.getElementById("cuInput")?.textContent) renderChatUsage(null, finalPreview, null);
    if (finalPreview) renderChatTokUsage(null, finalPreview);
    if (finalCacheStatus) renderCacheStatus(finalCacheStatus);
    refreshHeaderStack();
  } catch (e) {
    toast("Ошибка чата: " + e.message, "err");
    if (assistantDiv) {
      assistantDiv.classList.remove("streaming");
      const metaEl = assistantDiv.querySelector(".meta");
      if (metaEl) metaEl.textContent = `${persona_id} · ошибка`;
      if (!fullText && !fullReasoning) mdEl.innerHTML = renderMarkdown("⚠ " + e.message);
    } else {
      appendMessage("assistant", "⚠ " + e.message, "ошибка");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Отправить";
    setTokSpeed("");
  }
}

document.getElementById("btnSend").addEventListener("click", sendChat);
document.getElementById("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendChat();
  }
  // plain Enter (and Shift+Enter) just insert a newline — default textarea behavior
});
document.getElementById("chatInput").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(140, e.target.scrollHeight) + "px";
});

/* ===================== reasoning toggle (chat toolbar) ===================== */

// Reasoning is a provider-level generation setting (like temperature), edited
// only on the Settings page (llmReasoningEnabled / llmReasoningEffort).

/* ===================== chat history auto-load ===================== */

let historyLoadToken = 0;
async function loadChatHistory() {
  const persona_id = document.getElementById("chatPersona").value || "brain";
  const space_id = currentChatSpace();
  const myToken = ++historyLoadToken;
  const wrap = document.getElementById("chatMessages");
  wrap.innerHTML = `<div class="empty-state">Загрузка истории…</div>`;
  chatHistory.length = 0;
  try {
    const res = await api(`/chat/history?space_id=${encodeURIComponent(space_id)}&limit=200`);
    if (myToken !== historyLoadToken) return; // a newer load superseded this one
    wrap.innerHTML = "";
    if (!res.messages || !res.messages.length) {
      wrap.innerHTML = `<div class="empty-state">История пуста для «${esc(space_id)}» — начните диалог.</div>`;
      return;
    }
    res.messages.forEach((m) => {
      // Skip persisted tool/skill results — they live in memory as
      // level-0 nodes for the assistant to retrieve via get_fragment,
      // but they are internal state, not part of the visible dialogue.
      if (m.text && m.text.startsWith("[Skill/tool execution result]")) return;
      if (m.origin === "service") {
        appendServiceEvent(m, persona_id);
        return;
      }
      const meta = m.occurred_at ? `${persona_id} · ${fmtTs(m.occurred_at)}` : persona_id;
      appendMessage(m.role, m.text, meta, null, m.reasoning);
    });
    wrap.scrollTop = wrap.scrollHeight;
  } catch (e) {
    if (myToken !== historyLoadToken) return;
    wrap.innerHTML = `<div class="empty-state">Не удалось загрузить историю: ${esc(e.message)}</div>`;
  }
}
document.getElementById("chatPersona").addEventListener("change", () => {
  updatePersonaMenuLabel();
  populateChatSpaces().then(loadChatHistory);
});
document.getElementById("chatSpace").addEventListener("change", (e) => {
  const persona_id = document.getElementById("chatPersona").value || "brain";
  try { localStorage.setItem(chatSpaceKey(persona_id), e.target.value); } catch (_) { /* storage unavailable */ }
  loadChatHistory();
});

/* ===================== OVERVIEW ===================== */

async function loadOverview() {
  const statCards = document.getElementById("statCards");
  const spaceCards = document.getElementById("spaceCards");
  const logEl = document.getElementById("retrievalLog");
  statCards.innerHTML = `<div class="card stat-card"><div class="stat-label">загрузка…</div></div>`;
  loadCacheStatus();
  try {
    const stats = await api("/memory/stats");
    const comp = stats.compression || {};
    const ratio = comp.raw_nodes && comp.total_nodes
      ? (comp.total_nodes / comp.raw_nodes).toFixed(2)
      : "—";

    statCards.innerHTML = `
      <div class="card stat-card"><div class="stat-label">всего узлов</div><div class="stat-value cold">${stats.total_nodes ?? 0}</div></div>
      <div class="card stat-card"><div class="stat-label">уровней сжатия</div><div class="stat-value">${(comp.levels || []).length}</div></div>
      <div class="card stat-card"><div class="stat-label">размер WAL</div><div class="stat-value">${fmtBytes(stats.wal_size_bytes)}</div></div>
      <div class="card stat-card"><div class="stat-label">размер БД (индекс)</div><div class="stat-value warm">${fmtBytes(stats.db_size_bytes)}</div></div>
    `;

    renderStackViz(document.getElementById("overviewStack"), stats.by_level || {});
    const legend = document.getElementById("overviewLevelLegend");
    const levels = Object.keys(stats.by_level || {}).map(Number).sort((a, b) => a - b);
    legend.innerHTML = levels.map((l) => `
      <span class="tag lvl ${lvlClass(l)}"><span style="display:inline-block;width:6px;height:6px;border-radius:2px;background:${levelColor(l)};margin-right:5px;"></span>level ${l}: ${stats.by_level[l]}</span>
    `).join("") || `<span class="tag">пусто</span>`;

    const spaces = Object.entries(stats.by_space || {});
    spaceCards.innerHTML = spaces.length ? spaces.map(([sp, n]) => `
      <div class="card stat-card"><div class="stat-label">${esc(sp)}</div><div class="stat-value">${n}</div></div>
    `).join("") : `<div class="empty-state">Пространства ещё не заполнены</div>`;

    refreshHeaderStack();
  } catch (e) {
    statCards.innerHTML = `<div class="empty-state">Ошибка загрузки: ${esc(e.message)}</div>`;
  }

  try {
    const log = await api("/memory/retrieval-log?limit=25");
    if (!log.length) {
      logEl.innerHTML = `<div class="empty-state">Вызовов ещё не было</div>`;
    } else {
      logEl.innerHTML = `<div style="display:flex; flex-direction:column; gap:6px;">` + log.map((r) => {
        let ids = [];
        try { ids = JSON.parse(r.result_node_ids || "[]"); } catch (_) {}
        return `<div style="display:flex; justify-content:space-between; gap:10px; font-size:12px; padding:6px 8px; border:1px solid var(--border-soft); border-radius:4px;">
          <span class="mono" style="color:var(--accent-cold)">${esc(r.tool)}</span>
          <span style="color:var(--text-faint); flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(ids.join(", ") || "—")}</span>
          <span class="mono" style="color:var(--text-faint)">${fmtTs(r.ts)}</span>
        </div>`;
      }).join("") + `</div>`;
    }
  } catch (e) {
    logEl.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

document.getElementById("btnCompress").addEventListener("click", async () => {
  const space_id = document.getElementById("compressSpace").value;
  const force_top = document.getElementById("compressForceTop").checked;
  const resultEl = document.getElementById("compressResult");
  resultEl.textContent = "выполняется…";
  try {
    const res = await api("/memory/compress", { method: "POST", body: { space_id, force_top } });
    resultEl.textContent = `создано узлов: ${res.count}`;
    toast(`Сжатие завершено: +${res.count} узлов`, "ok");
    loadOverview();
  } catch (e) {
    resultEl.textContent = "";
    toast("Ошибка сжатия: " + e.message, "err");
  }
});

/* ===================== TREE / NODE INSPECTOR ===================== */

async function loadTreeRoots() {
  const space_id = document.getElementById("treeSpace").value;
  const root = document.getElementById("treeRoot");
  root.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    const nodes = await api(`/memory/tree/roots?space_id=${encodeURIComponent(space_id)}`);
    if (!nodes.length) {
      root.innerHTML = `<div class="empty-state">Пусто в этом пространстве</div>`;
      return;
    }
    root.innerHTML = "";
    nodes.forEach((n) => root.appendChild(renderTreeNode(n)));
  } catch (e) {
    root.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

function renderTreeNode(node) {
  const wrap = document.createElement("div");
  wrap.className = "tree-node";

  const row = document.createElement("div");
  row.className = "tree-node-row";
  row.dataset.nodeId = node.id;

  const hasChildren = node.child_ids && node.child_ids.length > 0;
  row.innerHTML = `
    <span class="twist">${hasChildren ? "▸" : "·"}</span>
    <span class="lvl-chip" style="background:${levelColor(node.level)}"></span>
    <span class="summ" title="${esc(node.summary_text || node.raw_content || "")}">${esc((node.summary_text || node.raw_content || "(пусто)").slice(0, 90))}</span>
    <span class="cnt">L${node.level}${hasChildren ? " · " + node.child_ids.length : ""}</span>
  `;

  let childrenBox = null;
  let expanded = false;

  row.addEventListener("click", async (ev) => {
    document.querySelectorAll(".tree-node-row.selected").forEach((el) => el.classList.remove("selected"));
    row.classList.add("selected");
    inspectNode(node.id);

    if (!hasChildren) return;
    expanded = !expanded;
    const twist = row.querySelector(".twist");
    if (expanded) {
      twist.textContent = "▾";
      if (!childrenBox) {
        childrenBox = document.createElement("div");
        childrenBox.className = "tree-children";
        childrenBox.innerHTML = `<div class="empty-state" style="padding:8px;">загрузка…</div>`;
        wrap.appendChild(childrenBox);
        try {
          const children = await api(`/memory/tree/${encodeURIComponent(node.id)}/children`);
          childrenBox.innerHTML = "";
          children
            .sort((a, b) => (b.occurred_at || 0) - (a.occurred_at || 0))
            .forEach((c) => childrenBox.appendChild(renderTreeNode(c)));
          if (!children.length) childrenBox.innerHTML = `<div class="empty-state" style="padding:8px;">нет дочерних узлов</div>`;
        } catch (e) {
          childrenBox.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
        }
      } else {
        childrenBox.style.display = "";
      }
    } else if (childrenBox) {
      childrenBox.style.display = "none";
      twist.textContent = "▸";
    }
  });

  wrap.appendChild(row);
  return wrap;
}

document.getElementById("btnReloadTree").addEventListener("click", loadTreeRoots);
document.getElementById("treeSpace").addEventListener("change", loadTreeRoots);

async function inspectNode(nodeId) {
  const el = document.getElementById("inspector");
  el.innerHTML = `<div class="empty-state">Загрузка узла ${esc(nodeId)}…</div>`;
  try {
    const n = await api(`/memory/node/${encodeURIComponent(nodeId)}`);
    renderInspector(n);
  } catch (e) {
    el.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

function renderInspector(n) {
  const el = document.getElementById("inspector");
  const statuses = ["active", "pinned", "excluded", "archived", "hidden"];

  el.innerHTML = `
    <div class="inspector-header">
      <div>
        <div class="inspector-id">${esc(n.id)} <span class="tag lvl ${lvlClass(n.level)}" style="margin-left:8px;">level ${n.level}</span> <span class="badge-status ${esc(n.status)}" style="margin-left:6px;">${esc(n.status)}</span></div>
      </div>
      <div class="inspector-actions">
        <button class="btn sm ghost" id="btnCopyId">Copy ID</button>
        <button class="btn sm ghost" id="btnCopyCall">Copy get_fragment()</button>
      </div>
    </div>

    <div class="kv-grid">
      <span class="k">space_id</span><span class="v">${esc(n.space_id)}</span>
      <span class="k">type</span><span class="v">${esc(n.type)}</span>
      <span class="k">parent_id</span><span class="v">${n.parent_id ? esc(n.parent_id) : "—"}</span>
      <span class="k">child_ids</span><span class="v">${(n.child_ids || []).length ? esc(n.child_ids.join(", ")) : "—"}</span>
      <span class="k">occurred_at</span><span class="v">${fmtTs(n.occurred_at)}</span>
      <span class="k">timeframe</span><span class="v">${n.timeframe_start ? fmtTs(n.timeframe_start) + " → " + fmtTs(n.timeframe_end) : "—"}</span>
      <span class="k">tags</span><span class="v">${(n.tags || []).length ? esc(n.tags.join(", ")) : "—"}</span>
      <span class="k">retrieval_count</span><span class="v">${n.retrieval_count}</span>
      <span class="k">position_in_history</span><span class="v">${n.position_in_history}</span>
    </div>

    <div class="content-block-label">summary_text</div>
    <div class="content-block">${esc(n.summary_text || "—")}</div>

    ${n.raw_content ? `<div class="content-block-label">raw_content (level 0, точный текст)</div><div class="content-block">${esc(n.raw_content)}</div>` : ""}

    <div class="content-block-label">Сменить статус</div>
    <div style="display:flex; gap:6px; flex-wrap:wrap;">
      ${statuses.map((s) => `<button class="btn sm ${s === n.status ? "primary" : "ghost"}" data-status="${s}">${s}</button>`).join("")}
    </div>
  `;

  el.querySelector("#btnCopyId").addEventListener("click", () => {
    navigator.clipboard.writeText(n.id);
    toast("ID скопирован");
  });
  el.querySelector("#btnCopyCall").addEventListener("click", () => {
    navigator.clipboard.writeText(JSON.stringify({ name: "get_fragment", arguments: { id: n.id } }));
    toast("Вызов инструмента скопирован");
  });
  el.querySelectorAll("[data-status]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const status = btn.dataset.status;
      if (status === n.status) return;
      try {
        const updated = await api(`/memory/node/${encodeURIComponent(n.id)}/status`, { method: "PATCH", body: { status } });
        toast(`Статус изменён на "${status}"`, "ok");
        renderInspector(updated);
      } catch (e) {
        toast("Ошибка: " + e.message, "err");
      }
    });
  });
}

document.getElementById("btnSearch").addEventListener("click", runSearch);
document.getElementById("searchQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });

async function runSearch() {
  const query = document.getElementById("searchQuery").value.trim();
  const space_id = document.getElementById("searchSpace").value || null;
  const box = document.getElementById("searchResults");
  if (!query) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="empty-state">Поиск…</div>`;
  try {
    const res = await api("/memory/search", { method: "POST", body: { query, space_id, limit: 15 } });
    if (!res.results.length) {
      box.innerHTML = `<div class="empty-state">Ничего не найдено (это вспомогательный поиск — не замена get_fragment)</div>`;
      return;
    }
    box.innerHTML = res.results.map((r) => `
      <div class="search-result" data-id="${esc(r.id)}">
        <div style="display:flex; justify-content:space-between;">
          <span class="sid">${esc(r.id)}</span>
          <span class="tag lvl ${lvlClass(r.level)}">L${r.level} · ${esc(r.space_id)}</span>
        </div>
        <div class="snip">${(r.snippet || "").replace(/\[/g, "<mark>").replace(/\]/g, "</mark>")}</div>
      </div>
    `).join("");
    box.querySelectorAll(".search-result").forEach((el) => {
      el.addEventListener("click", () => inspectNode(el.dataset.id));
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

/* ===================== PERSONAS ===================== */

let toolsCatalogCache = null;
let skillsCatalogCache = null;

async function getToolsCatalog() {
  if (!toolsCatalogCache) toolsCatalogCache = await api("/tools");
  return toolsCatalogCache;
}

async function getSkillsCatalog() {
  if (!skillsCatalogCache) skillsCatalogCache = await api("/skills");
  return skillsCatalogCache;
}

function fillMultiSelect(select, options, valueKey, labelKey, selected) {
  const sel = new Set(selected || []);
  select.innerHTML = options.map((o) => {
    const v = o[valueKey];
    return `<option value="${esc(v)}" ${sel.has(v) ? "selected" : ""}>${esc(o[labelKey] || v)}</option>`;
  }).join("");
}

function getSelectedValues(select) {
  return Array.from(select.selectedOptions).map((o) => o.value);
}

async function populateCreatePersonaSelects() {
  const [tools, skills] = await Promise.all([getToolsCatalog(), getSkillsCatalog()]);
  const toolsSel = document.getElementById("npTools");
  const skillsSel = document.getElementById("npSkills");
  fillMultiSelect(toolsSel, tools, "name", "name", ["get_fragment", "search_archive", "calculate_time_delta"]);
  fillMultiSelect(skillsSel, skills, "skill_id", "skill_id", []);
}

async function loadPersonas() {
  // Перечитываем каталог инструментов: активированные custom-функции
  // должны сразу появляться в списке разрешений персоны.
  toolsCatalogCache = null;
  const box = document.getElementById("personaList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    const personas = await api("/personas");
    if (!personas.length) { box.innerHTML = `<div class="empty-state">Персон нет</div>`; return; }
    box.innerHTML = personas.map((p) => `
      <div class="persona-card" data-id="${esc(p.persona_id)}">
        <div class="persona-card-top">
          <div><span class="persona-name">${esc(p.name)}</span><span class="persona-id">${esc(p.persona_id)}</span></div>
          <span class="tag">${esc(p.tone)}</span>
        </div>
        <div class="persona-desc">${esc(p.description || "—")}</div>
        <div class="persona-meta">
          <span class="tag">spaces: ${(p.allow_spaces || []).length ? esc(p.allow_spaces.join(", ")) : "все"}</span>
          ${(p.deny_spaces || []).length ? `<span class="tag" style="color:var(--danger); border-color:var(--danger);">deny: ${esc(p.deny_spaces.join(", "))}</span>` : ""}
          ${(p.active_skill_ids || []).length ? `<span class="tag">skills: ${esc(p.active_skill_ids.join(", "))}</span>` : ""}
          <span class="tag">tools: ${esc((p.tool_permissions || []).join(", "))}</span>
        </div>
        <div class="persona-actions">
          <button class="btn sm btnEditPersona" data-id="${esc(p.persona_id)}">Изменить</button>
          <button class="btn sm danger btnDeletePersona" data-id="${esc(p.persona_id)}">Удалить</button>
        </div>
        <div class="persona-edit-form" id="edit-${esc(p.persona_id)}" style="display:none;"></div>
      </div>
    `).join("");

    box.querySelectorAll(".btnEditPersona").forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = personas.find((x) => x.persona_id === btn.dataset.id);
        toggleEditForm(p);
      });
    });
    box.querySelectorAll(".btnDeletePersona").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Удалить персону "${btn.dataset.id}"?`)) return;
        try {
          await api(`/personas/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
          toast("Персона удалена", "ok");
          loadPersonas();
          loadPersonasForChat();
        } catch (e) {
          toast("Ошибка: " + e.message, "err");
        }
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

async function toggleEditForm(p) {
  const wrap = document.getElementById(`edit-${p.persona_id}`);
  if (!wrap) return;
  const isOpen = wrap.style.display !== "none";
  // close any other open forms
  document.querySelectorAll(".persona-edit-form").forEach((el) => (el.style.display = "none"));
  if (isOpen) return;
  wrap.style.display = "block";
  wrap.innerHTML = `
    <label>Имя</label>
    <input type="text" class="epName" value="${esc(p.name)}" />
    <label>Описание</label>
    <textarea class="epDesc" rows="2">${esc(p.description || "")}</textarea>
    <label>Инструкции персоны (system_core_addon — уходят в системный промпт)</label>
    <textarea class="epAddon" rows="9" style="font-family:var(--mono);font-size:12px;">${esc(p.system_core_addon || "")}</textarea>
    <label>Тон</label>
    <input type="text" class="epTone" value="${esc(p.tone)}" />
    <label>Разрешённые пространства памяти (через запятую, пусто = все)</label>
    <input type="text" class="epAllow" value="${esc((p.allow_spaces || []).join(", "))}" />
    <label>Запрещённые пространства памяти (через запятую)</label>
    <input type="text" class="epDeny" value="${esc((p.deny_spaces || []).join(", "))}" />
    <label>Разрешённые теги (пусто = без ограничений)</label>
    <input type="text" class="epAllowTags" value="${esc((p.allow_tags || []).join(", "))}" />
    <label>Скрытые теги (например diary)</label>
    <input type="text" class="epDenyTags" value="${esc((p.deny_tags || []).join(", "))}" />
    <label>Активные навыки</label>
    <select class="epSkills" multiple size="4"></select>
    <label>Разрешённые инструменты</label>
    <select class="epTools" multiple size="4"></select>
    <div class="section-title" style="margin-top:16px; font-size:14px;">Бюджет контекста (токены)</div>
    <div class="budget-settings-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:8px 0 14px;">
      ${[
        ["system_core","System core"],["persona_header","Persona header"],["current_time","Current time"],
        ["skill_triggers","Skill triggers"],["tree_tops","Tree tops"],["tail","Tail"],
        ["retrieved_fragments","Retrieved fragments"],["reasoning_reserve","Reasoning reserve"],["total_budget","Total budget"]
      ].map(([key,label]) => `
        <div class="field budget-field">
          <label>${label}</label>
          <div style="display:flex;gap:5px;align-items:center;">
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="-1000">−1000</button>
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="-100">−100</button>
            <input type="number" min="0" step="100" class="epBudget" data-key="${key}" value="${Number((p.budget_profile || {})[key] ?? 0)}" style="min-width:0;flex:1;" />
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="100">+100</button>
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="1000">+1000</button>
          </div>
        </div>`).join("")}
    </div>
    <div class="page-sub" style="margin:-4px 0 12px;">Быстрые кнопки меняют значение на 100 или 1000 токенов. Значения сохраняются отдельно для каждой персоны.</div>
    <div class="persona-actions">
      <button class="btn sm primary epSave">Сохранить</button>
      <button class="btn sm ghost epCancel">Отмена</button>
    </div>
  `;
  const [tools, skills] = await Promise.all([getToolsCatalog(), getSkillsCatalog()]);
  fillMultiSelect(wrap.querySelector(".epTools"), tools, "name", "name", p.tool_permissions || []);
  fillMultiSelect(wrap.querySelector(".epSkills"), skills, "skill_id", "skill_id", p.active_skill_ids || []);
  wrap.querySelectorAll(".budget-adjust").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = wrap.querySelector(`.epBudget[data-key="${btn.dataset.key}"]`);
      if (!input) return;
      const current = Number(input.value || 0);
      input.value = Math.max(0, current + Number(btn.dataset.delta || 0));
    });
  });
  const splitList = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  wrap.querySelector(".epCancel").addEventListener("click", () => (wrap.style.display = "none"));
  wrap.querySelector(".epSave").addEventListener("click", async () => {
    try {
      await api(`/personas/${encodeURIComponent(p.persona_id)}`, {
        method: "PATCH",
        body: {
          name: wrap.querySelector(".epName").value.trim(),
          description: wrap.querySelector(".epDesc").value.trim(),
          system_core_addon: wrap.querySelector(".epAddon").value,
          allow_tags: splitList(wrap.querySelector(".epAllowTags").value),
          deny_tags: splitList(wrap.querySelector(".epDenyTags").value),
          tone: wrap.querySelector(".epTone").value.trim() || "neutral",
          allow_spaces: splitList(wrap.querySelector(".epAllow").value),
          deny_spaces: splitList(wrap.querySelector(".epDeny").value),
          active_skill_ids: getSelectedValues(wrap.querySelector(".epSkills")),
          tool_permissions: getSelectedValues(wrap.querySelector(".epTools")),
          budget_profile: Object.fromEntries([...wrap.querySelectorAll(".epBudget")].map((input) => [input.dataset.key, Math.max(0, Math.round(Number(input.value || 0)))])),
        },
      });
      toast("Настройки персоны сохранены", "ok");
      loadPersonas();
      loadPersonasForChat();
    } catch (e) {
      toast("Ошибка: " + e.message, "err");
    }
  });
}

document.getElementById("btnCreatePersona").addEventListener("click", async () => {
  const persona_id = document.getElementById("npId").value.trim();
  const name = document.getElementById("npName").value.trim();
  if (!persona_id || !name) { toast("Укажите persona_id и имя", "err"); return; }
  const splitList = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  try {
    await api("/personas", {
      method: "POST",
      body: {
        persona_id, name,
        description: document.getElementById("npDesc").value.trim(),
        system_core_addon: document.getElementById("npAddon").value,
        allow_spaces: splitList(document.getElementById("npAllowSpaces").value),
        deny_spaces: splitList(document.getElementById("npDenySpaces").value),
        tone: document.getElementById("npTone").value.trim() || "neutral",
        active_skill_ids: getSelectedValues(document.getElementById("npSkills")),
        tool_permissions: getSelectedValues(document.getElementById("npTools")),
      },
    });
    toast("Персона создана", "ok");
    ["npId", "npName", "npDesc", "npAddon", "npAllowSpaces", "npDenySpaces", "npTone"].forEach((id) => document.getElementById(id).value = "");
    loadPersonas();
    loadPersonasForChat();
  } catch (e) {
    toast("Ошибка: " + e.message, "err");
  }
});

/* ===================== COLLAPSIBLE CARDS ===================== */

const COLLAPSE_STORAGE_KEY = "pai_collapsed_cards_v1";

function getCollapsedCards() {
  try { return JSON.parse(localStorage.getItem(COLLAPSE_STORAGE_KEY) || "{}"); }
  catch (_) { return {}; }
}

function setCardCollapsed(card, collapsed, persist = true) {
  const body = card.querySelector(".collapse-body");
  const header = card.querySelector(".collapse-header");
  const toggle = card.querySelector(".collapse-toggle");
  if (!body || !header || !toggle) return;
  card.classList.toggle("is-collapsed", collapsed);
  header.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-label", collapsed ? "Развернуть карточку" : "Свернуть карточку");
  toggle.title = collapsed ? "Развернуть" : "Свернуть";
  toggle.textContent = collapsed ? "⌄" : "⌃";
  if (persist) {
    const key = card.dataset.collapseKey;
    if (key) {
      const state = getCollapsedCards();
      state[key] = collapsed;
      localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(state));
    }
  }
}

function initCollapsibleCards(container) {
  const state = getCollapsedCards();
  container.querySelectorAll(".collapsible-card").forEach((card) => {
    const key = card.dataset.collapseKey;
    setCardCollapsed(card, key ? state[key] === true : false, false);
    const header = card.querySelector(".collapse-header");
    const toggle = card.querySelector(".collapse-toggle");
    if (!header || !toggle) return;
    header.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, select, textarea")) return;
      setCardCollapsed(card, !card.classList.contains("is-collapsed"));
    });
    header.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target.closest("button")) return;
      event.preventDefault();
      setCardCollapsed(card, !card.classList.contains("is-collapsed"));
    });
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      setCardCollapsed(card, !card.classList.contains("is-collapsed"));
    });
  });
}

/* ===================== RULES ===================== */

/* ===================== SKILLS VIEW ===================== */

async function loadSkillsView() {
  const box = document.getElementById("skillList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    skillsCatalogCache = null;
    const skills = await getSkillsCatalog();
    if (!skills.length) { box.innerHTML = `<div class="empty-state">Скилов нет</div>`; return; }
    box.innerHTML = skills.map((s) => `
      <div class="rule-card collapsible-card" data-id="${esc(s.skill_id)}" data-collapse-key="skill:${esc(s.skill_id)}">
        <div class="rule-card-top collapse-header" role="button" tabindex="0" aria-expanded="true">
          <div class="collapse-heading">
            <span class="tag">${esc(s.skill_id)}</span>
            <span class="tag">v${esc(String(s.version))}</span>
          </div>
          <button class="collapse-toggle" type="button" aria-label="Свернуть карточку" title="Свернуть">⌃</button>
        </div>
        <div class="collapse-body">
          <div class="rule-text"><b>Триггер:</b> ${esc(s.trigger_description)}</div>
          <div class="rule-text" style="margin-top:6px; white-space:pre-wrap;">${esc(s.full_instruction_text || "")}</div>
          <div class="rule-meta">
            <span>id: ${esc(s.skill_id)}</span>
            <span>обновлено: ${fmtTs(s.last_updated)}</span>
          </div>
          <div class="rule-actions">
            <button class="btn sm danger" data-act="delete">удалить</button>
          </div>
        </div>
      </div>
    `).join("");

    initCollapsibleCards(box);
    box.querySelectorAll(".rule-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm(`Удалить скил «${id}»? Он также будет отвязан от всех персон, где был подключён.`)) return;
          try {
            await api(`/skills/${encodeURIComponent(id)}`, { method: "DELETE" });
            skillsCatalogCache = null;
            toast("Скил удалён", "ok");
            loadSkillsView();
          } catch (e) {
            toast("Ошибка: " + e.message, "err");
          }
        });
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

document.getElementById("btnCreateSkill").addEventListener("click", async () => {
  const skill_id = document.getElementById("nsId").value.trim();
  const trigger_description = document.getElementById("nsTrigger").value.trim();
  const full_instruction_text = document.getElementById("nsText").value.trim();
  if (!skill_id) { toast("Введите skill_id", "err"); return; }
  if (!trigger_description) { toast("Введите триггер", "err"); return; }
  try {
    await api("/skills", { method: "POST", body: { skill_id, trigger_description, full_instruction_text } });
    document.getElementById("nsId").value = "";
    document.getElementById("nsTrigger").value = "";
    document.getElementById("nsText").value = "";
    skillsCatalogCache = null;
    toast("Скил создан", "ok");
    loadSkillsView();
  } catch (e) {
    toast("Ошибка: " + e.message, "err");
  }
});

async function loadRules() {
  const box = document.getElementById("ruleList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    let rules = await api("/rules");
    const filter = document.getElementById("rulesStatusFilter").value;
    if (filter) rules = rules.filter((r) => r.status === filter);
    if (!rules.length) { box.innerHTML = `<div class="empty-state">Правил нет</div>`; return; }
    rules.sort((a, b) => b.created_at - a.created_at);
    box.innerHTML = rules.map((r) => `
      <div class="rule-card collapsible-card" data-id="${esc(r.id)}" data-collapse-key="rule:${esc(r.id)}">
        <div class="rule-card-top collapse-header" role="button" tabindex="0" aria-expanded="true">
          <div class="collapse-heading">
            <span class="badge-status ${esc(r.status)}">${esc(r.status)}</span>
            <span class="tag">${esc(r.scope)}${r.persona_id ? " · " + esc(r.persona_id) : ""}${r.space_id ? " · " + esc(r.space_id) : ""}</span>
          </div>
          <button class="collapse-toggle" type="button" aria-label="Свернуть карточку" title="Свернуть">⌃</button>
        </div>
        <div class="collapse-body">
          <div class="rule-text">${esc(r.rule_text)}</div>
          ${r.trigger_context ? `<div style="color:var(--text-faint); font-size:11.5px; margin-bottom:6px;">из: ${esc(r.trigger_context)}</div>` : ""}
          <div class="rule-meta">
            <span>id: ${esc(r.id)}</span>
            <span>confidence: ${r.confidence}</span>
            <span>создано: ${fmtTs(r.created_at)}</span>
          </div>
          <div class="rule-actions">
          ${r.status === "draft" ? `<button class="btn sm" data-act="validate">validate</button>` : ""}
          ${r.status === "validated" ? `<button class="btn sm primary" data-act="activate">activate</button>` : ""}
          ${r.status === "active" ? `<button class="btn sm ghost" data-act="archive">archive</button>` : ""}
          ${r.status !== "rejected" && r.status !== "archived" ? `<button class="btn sm danger" data-act="reject">reject</button>` : ""}
          <button class="btn sm danger" data-act="delete">удалить</button>
          </div>
        </div>
      </div>
    `).join("");

    initCollapsibleCards(box);
    box.querySelectorAll(".rule-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const action = btn.dataset.act;
          if (action === "delete") {
            if (!confirm("Удалить это правило безвозвратно?")) return;
            try {
              await api(`/rules/${encodeURIComponent(id)}`, { method: "DELETE" });
              toast("Правило удалено", "ok");
              loadRules();
            } catch (e) {
              toast("Ошибка: " + e.message, "err");
            }
            return;
          }
          try {
            await api(`/rules/${encodeURIComponent(id)}/${action}`, { method: "POST" });
            toast("Готово", "ok");
            loadRules();
          } catch (e) {
            toast("Ошибка: " + e.message, "err");
          }
        });
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

async function loadFunctions() {
  const box = document.getElementById("functionList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    let funcs = await api("/functions");
    const filter = document.getElementById("functionsStatusFilter").value;
    if (filter) funcs = funcs.filter((f) => f.status === filter);
    funcs.sort((a, b) => b.created_at - a.created_at);
    if (!funcs.length) { box.innerHTML = `<div class="empty-state">Функций нет</div>`; return; }
    box.innerHTML = funcs.map((f) => {
      const impl = JSON.stringify(f.implementation || {}, null, 2);
      const params = JSON.stringify(f.parameters || {}, null, 2);
      return `
        <div class="rule-card collapsible-card" data-id="${esc(f.id)}" data-collapse-key="function:${esc(f.id)}">
          <div class="rule-card-top collapse-header" role="button" tabindex="0" aria-expanded="true">
            <div class="collapse-heading">
              <span class="badge-status ${esc(f.status)}">${esc(f.status)}</span>
              <span class="tag">${esc(f.kind)} · ${esc(f.name)}</span>
            </div>
            <button class="collapse-toggle" type="button" aria-label="Свернуть карточку" title="Свернуть">⌃</button>
          </div>
          <div class="collapse-body">
            <div class="rule-text">${esc(f.description || "Без описания")}</div>
            <div class="grid grid-2" style="margin-top:8px;">
              <div><div class="section-title" style="font-size:11px;">parameters</div><pre class="mono" style="white-space:pre-wrap;">${esc(params)}</pre></div>
              <div><div class="section-title" style="font-size:11px;">implementation</div><pre class="mono" style="white-space:pre-wrap;">${esc(impl)}</pre></div>
            </div>
            <div class="rule-meta"><span>id: ${esc(f.id)}</span><span>создано: ${fmtTs(f.created_at)}</span></div>
            <div class="rule-actions">
              ${f.status === "draft" ? `<button class="btn sm" data-act="validate">проверить</button>` : ""}
              ${f.status === "validated" ? `<button class="btn sm primary" data-act="activate">✓ подтвердить и активировать</button>` : ""}
              ${f.status !== "rejected" && f.status !== "active" ? `<button class="btn sm danger" data-act="reject">отклонить</button>` : ""}
              ${f.status === "active" ? `<button class="btn sm danger" data-act="archive">отключить</button>` : ""}
              <button class="btn sm danger" data-act="delete">удалить</button>
            </div>
          </div>
        </div>`;
    }).join("");
    initCollapsibleCards(box);
    box.querySelectorAll(".rule-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const action = btn.dataset.act;
          if (action === "activate" && !confirm("Активировать эту функцию? После подтверждения ассистент сможет вызывать её в чате.")) return;
          if (action === "archive" && !confirm("Отключить эту функцию? Она перестанет передаваться модели.")) return;
          if (action === "delete") {
            if (!confirm("Удалить эту функцию безвозвратно? Она также будет отвязана от всех персон.")) return;
            try {
              await api(`/functions/${encodeURIComponent(id)}`, { method: "DELETE" });
              toast("Функция удалена", "ok");
              toolsCatalogCache = null;
              loadFunctions();
            } catch (e) { toast("Ошибка: " + e.message, "err"); }
            return;
          }
          try {
            await api(`/functions/${encodeURIComponent(id)}/${action}`, { method: "POST" });
            toast(action === "activate" ? "Функция активирована" : "Готово", "ok");
            toolsCatalogCache = null;
            loadFunctions();
          } catch (e) { toast("Ошибка: " + e.message, "err"); }
        });
      });
    });
  } catch (e) { box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`; }
}

document.getElementById("functionsStatusFilter").addEventListener("change", loadFunctions);
document.getElementById("btnReloadFunctions").addEventListener("click", loadFunctions);
document.getElementById("btnCreateFunction").addEventListener("click", async () => {
  const name = document.getElementById("nfName").value.trim();
  if (!name) { toast("Укажите имя функции", "err"); return; }
  try {
    const parameters = JSON.parse(document.getElementById("nfParameters").value);
    const implementation = JSON.parse(document.getElementById("nfImplementation").value || "{}");
    await api("/functions", { method: "POST", body: {
      name,
      description: document.getElementById("nfDescription").value.trim(),
      kind: document.getElementById("nfKind").value,
      parameters, implementation,
    }});
    toast("Draft функции создан", "ok");
    ["nfName", "nfDescription", "nfImplementation"].forEach((id) => document.getElementById(id).value = "");
    document.getElementById("nfParameters").value = '{"type":"object","properties":{}}';
    loadFunctions();
  } catch (e) { toast("Ошибка: " + e.message, "err"); }
});

document.getElementById("rulesStatusFilter").addEventListener("change", loadRules);
document.getElementById("btnReloadRules").addEventListener("click", loadRules);

document.getElementById("btnCreateRule").addEventListener("click", async () => {
  const rule_text = document.getElementById("nrText").value.trim();
  if (!rule_text) { toast("Введите текст правила", "err"); return; }
  try {
    await api("/rules", {
      method: "POST",
      body: {
        rule_text,
        scope: document.getElementById("nrScope").value,
        persona_id: document.getElementById("nrPersona").value.trim() || null,
        space_id: document.getElementById("nrSpace").value.trim() || null,
        trigger_context: document.getElementById("nrTrigger").value.trim(),
      },
    });
    toast("Draft создан", "ok");
    ["nrText", "nrPersona", "nrSpace", "nrTrigger"].forEach((id) => document.getElementById(id).value = "");
    loadRules();
  } catch (e) {
    toast("Ошибка: " + e.message, "err");
  }
});

/* ===================== ACTION LOG ===================== */

const AL_COLORS = {
  instructions: "#6ea8fe", memory: "#7dd3a8", history: "#f2c46d",
  user: "#f08fb0", tools: "#b98cff", other: "#8a8f98",
};
const AL_GROUP_LABEL = {
  instructions: "инструкции и правила", memory: "сжатая память", history: "история чата / фрагменты",
  user: "сообщение пользователя", tools: "инструменты", other: "прочее",
};
const alN = (n) => Number(n || 0).toLocaleString("ru-RU");

function alParse(v) {
  if (v == null) return null;
  if (typeof v === "object") return v;
  try { return JSON.parse(v); } catch { return null; }
}

function alLabel(row, req) {
  if (row.kind !== "llm_request") return `вызов функции: <span class="mono">${esc(row.name || "")}</span>`;
  const purpose = req && req.purpose;
  const phase = req && req.phase;
  let tag = "";
  if (purpose === "compression") tag = ` · сжатие ${esc(phase || "")}`;
  else if (phase === "planning") tag = " · планирование пула";
  else if (phase === "analysis") tag = " · анализ результатов";
  else if (phase === "final") tag = " · финальный ответ";
  return `запрос к модели: <span class="mono">${esc(row.name || "")}</span>${tag}`;
}

function alChip(k, v, h) {
  return `<div class="al-chip"><div class="k">${esc(k)}</div><div class="v">${v}</div>${h ? `<div class="h">${h}</div>` : ""}</div>`;
}

function alSummary(req, resp, latency) {
  const bd = req.breakdown || {};
  const tk = (resp && resp.tokens) || {};
  const ctxLen = resp && resp.context_length;
  const ctx = tk.context || bd.total_tokens_est || 0;
  const srcTag = tk.source === "provider" ? "по данным провайдера" : "оценка (≈ 4 симв./ток.)";
  const util = ctxLen ? `${(ctx / ctxLen * 100).toFixed(1)}% из ${alN(ctxLen)}` : srcTag;
  const chips = [
    alChip("контекст (вход)", alN(ctx), util),
    alChip("ответ модели", alN(tk.completion), tk.reasoning ? `из них reasoning ${alN(tk.reasoning)}` : srcTag),
  ];
  if (tk.cached) chips.push(alChip("из кэша", alN(tk.cached), "cached prompt tokens"));
  if (latency != null) {
    const tps = latency > 0 && tk.completion ? ` · ${(tk.completion / latency).toFixed(1)} ток/с` : "";
    chips.push(alChip("время", `${Number(latency).toFixed(2)}s`, tps.replace(/^ · /, "")));
  }
  if (req.max_tokens) chips.push(alChip("max_tokens", alN(req.max_tokens), req.temperature != null ? `temp ${req.temperature}` : ""));
  const h = bd.chat_history;
  if (h && req.purpose !== "compression") {
    chips.push(alChip("история чата", `${alN(h.messages_in_tail)} сообщ.`,
      `польз. ${alN(h.user)} · ассист. ${alN(h.assistant)}${h.tail_length_cfg ? ` · лимит ${h.tail_length_cfg}` : ""}`));
  }
  const nInstr = (bd.instruction_keys || []).length;
  if (nInstr) chips.push(alChip("блоков инструкций", alN(nInstr), "core / persona / skills / rules / planner"));
  return `<div class="al-sum">${chips.join("")}</div>`;
}

function alStack(bd) {
  const parts = (bd.parts || []).filter((p) => (p.tokens || 0) > 0);
  const total = parts.reduce((a, p) => a + p.tokens, 0) || 1;
  const bar = parts.map((p) =>
    `<i style="width:${(p.tokens / total * 100).toFixed(2)}%;background:${AL_COLORS[p.group] || AL_COLORS.other}" title="${esc(p.label)}: ${alN(p.tokens)}"></i>`
  ).join("");
  const groups = {};
  parts.forEach((p) => { groups[p.group] = (groups[p.group] || 0) + p.tokens; });
  const legend = Object.keys(groups).map((g) =>
    `<span><b style="background:${AL_COLORS[g] || AL_COLORS.other}"></b>${esc(AL_GROUP_LABEL[g] || g)} — ${alN(groups[g])} (${(groups[g] / total * 100).toFixed(0)}%)</span>`
  ).join("");
  const calib = bd.calibration
    ? `<div class="al-note">Токены по частям — оценка, масштабированная под реальный prompt_tokens провайдера (коэф. ×${bd.calibration}).</div>`
    : `<div class="al-note">Провайдер не вернул usage — токены по частям приблизительные (≈ 4 симв./ток., для кириллицы занижены).</div>`;
  return `<div class="al-bar">${bar}</div><div class="al-legend">${legend}</div>${calib}`;
}

function alMetaText(p) {
  const m = p.meta || {};
  const out = [];
  if (p.key === "tail") out.push(`${m.count} сообщ.: польз. ${m.user_msgs}, ассист. ${m.assistant_msgs}`);
  else if (p.key === "tree_tops") out.push(`${m.count} узл. сжатой памяти`);
  else if (p.key === "rules") out.push(`${m.count} правил`);
  else if (p.key === "skills") out.push(`${m.count} навыков: ${(m.items || []).join(", ")}`);
  else if (p.key === "tool_schemas") out.push(`${m.count} инструм.: ${(m.names || []).join(", ")}`);
  else if (p.key === "fragments") out.push(`${m.count} фрагментов`);
  if (m.images) out.push(`изображений: ${m.images}`);
  return out.join(" · ");
}

function alParts(bd) {
  const parts = bd.parts || [];
  if (!parts.length) return "";
  const total = parts.reduce((a, p) => a + (p.tokens || 0), 0) || 1;
  const rows = parts.map((p) => {
    const meta = alMetaText(p);
    const extra = [];
    if (p.key === "rules" && (p.meta.items || []).length)
      extra.push(`<ul style="margin:4px 0 0 16px;padding:0;">${p.meta.items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`);
    if (p.key === "fragments" && (p.meta.fragments || []).length)
      extra.push(p.meta.fragments.map((f) =>
        `<div class="al-frag"><span class="mono">${esc(f.id)}</span> · L${f.level} · ≈${alN(f.tokens_est)} ток. · ${esc(f.preview)}</div>`).join(""));
    if (p.text) extra.push(`<details><summary>показать текст (${alN(p.chars)} симв.)</summary><pre class="mono al-pre">${esc(p.text)}</pre></details>`);
    return `<tr>
      <td><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${AL_COLORS[p.group] || AL_COLORS.other};margin-right:6px"></span>${esc(p.label)}
        ${meta ? `<div class="meta">${esc(meta)}</div>` : ""}${extra.join("")}</td>
      <td class="n">${alN(p.tokens)}</td>
      <td class="n hide-m">${(p.tokens / total * 100).toFixed(1)}%</td>
      <td class="n hide-m">${alN(p.chars)}</td>
    </tr>`;
  }).join("");
  return `<table class="al-parts"><thead><tr><th>Часть запроса</th><th>токены</th><th class="hide-m">доля</th><th class="hide-m">симв.</th></tr></thead><tbody>${rows}
    <tr><td><b>Итого</b></td><td class="n"><b>${alN(parts.reduce((a, p) => a + (p.tokens || 0), 0))}</b></td><td class="hide-m"></td><td class="hide-m"></td></tr></tbody></table>`;
}

function alCompression(req, resp) {
  const c = req.compression;
  if (!c) return "";
  const rc = (resp && resp.compression) || {};
  const tk = (resp && resp.tokens) || {};
  const chips = [
    alChip("уровни", `L${c.source_level} → L${c.target_level}`, `попытка ${(c.attempt || 0) + 1}`),
    alChip("фрагментов", alN(c.fragments_count), "узлов на входе"),
  ];
  if (tk.completion != null) chips.push(alChip("вход → выход", `${alN(tk.context)} → ${alN(tk.completion)}`, "токены"));
  if (rc.compression_ratio) chips.push(alChip("степень сжатия", `×${rc.compression_ratio}`, `${alN(rc.summary_chars)} симв. в саммари`));
  if (rc.empty) chips.push(alChip("результат", "пусто", "будет повтор"));
  return `<div class="al-sec">Сжатие</div><div class="al-sum">${chips.join("")}</div>
    <div class="al-note">ID узлов: <span class="mono">${esc((c.source_node_ids || []).join(", "))}</span></div>`;
}

function alBody(row) {
  const req = alParse(row.request) || {};
  const resp = alParse(row.response);
  if (row.kind !== "llm_request") {
    const pj = (o) => esc(typeof o === "string" ? o : JSON.stringify(o, null, 2));
    return `<div class="al-sec">аргументы</div><pre class="mono al-pre">${pj(req)}</pre>` +
      (resp != null ? `<div class="al-sec">результат</div><pre class="mono al-pre">${pj(resp)}</pre>` : "") +
      (row.error ? `<div class="al-sec">ошибка</div><pre class="mono al-pre">${esc(row.error)}</pre>` : "");
  }
  const bd = req.breakdown;
  let html = "";
  html += alSummary(req, resp, row.latency_seconds);
  html += alCompression(req, resp);
  if (bd) {
    html += `<div class="al-sec">Из чего состоит запрос</div>` + alStack(bd) + alParts(bd);
  } else {
    html += `<div class="al-note">Для этой записи нет разбивки (запись создана до обновления).</div>`;
  }
  // raw + response
  const { breakdown, messages, tools, ...params } = req;
  html += `<div class="al-sec">Параметры запроса</div><pre class="mono al-pre">${esc(JSON.stringify(params, null, 2))}</pre>`;
  if (resp) {
    html += `<div class="al-sec">Ответ модели</div>`;
    if (resp.reasoning) html += `<details><summary class="al-note">reasoning (${alN(resp.tokens && resp.tokens.reasoning)} ток.)</summary><pre class="mono al-pre">${esc(resp.reasoning)}</pre></details>`;
    if (resp.content) html += `<pre class="mono al-pre">${esc(resp.content)}</pre>`;
    if (resp.tool_calls && resp.tool_calls.length) html += `<div class="al-note">tool_calls:</div><pre class="mono al-pre">${esc(JSON.stringify(resp.tool_calls, null, 2))}</pre>`;
    if (resp.usage && Object.keys(resp.usage).length) html += `<details><summary class="al-note">usage (сырой ответ провайдера)</summary><pre class="mono al-pre">${esc(JSON.stringify(resp.usage, null, 2))}</pre></details>`;
  }
  html += `<details><summary class="al-note">полный messages[] как отправлено</summary><pre class="mono al-pre">${esc(JSON.stringify({ messages, tools }, null, 2))}</pre></details>`;
  if (row.error) html += `<div class="al-sec">ошибка</div><pre class="mono al-pre">${esc(row.error)}</pre>`;
  return html;
}

async function loadActionLog() {
  const listEl = document.getElementById("actionLogList");
  const kind = document.getElementById("actionLogKind").value;
  const purpose = document.getElementById("actionLogPurpose").value;
  const limit = document.getElementById("actionLogLimit").value;
  listEl.innerHTML = `<div class="empty-state">загрузка…</div>`;
  try {
    const qs = new URLSearchParams({ limit });
    if (kind) qs.set("kind", kind);
    let rows = await api(`/actions/log?${qs.toString()}`);
    if (purpose) rows = rows.filter((r) => {
      if (r.kind !== "llm_request") return false;
      const rq = alParse(r.request) || {};
      return (rq.purpose || "chat") === purpose;
    });
    if (!rows.length) {
      listEl.innerHTML = `<div class="empty-state">Пока нет записей</div>`;
      return;
    }
    listEl.innerHTML = rows.map((row) => {
      const req = alParse(row.request) || {};
      const resp = alParse(row.response) || {};
      const time = new Date(row.ts * 1000).toLocaleString("ru-RU");
      const isComp = req.purpose === "compression";
      const kindBadge = row.kind === "llm_request"
        ? (isComp ? `<span class="badge-status comp">сжатие</span>` : `<span class="badge-status validated">llm_request</span>`)
        : `<span class="badge-status active">tool_call</span>`;
      const errBadge = row.error ? `<span class="badge-status rejected">ошибка</span>` : "";
      const latency = row.latency_seconds != null ? `${Number(row.latency_seconds).toFixed(2)}s` : "—";
      const tk = resp.tokens;
      const tokBadge = tk ? `<span class="mono" title="контекст → ответ">${alN(tk.context)} → ${alN(tk.completion)} ток.</span>` : "";
      return `
        <details class="card action-log-row" data-seq="${row.seq}">
          <summary>
            <span>${time}</span>
            ${kindBadge}
            <span>${alLabel(row, req)}</span>
            ${tokBadge}
            <span class="mono">${latency}</span>
            ${row.space_id ? `<span class="mono">${esc(row.space_id)}</span>` : ""}
            ${errBadge}
          </summary>
          <div class="action-log-detail" data-lazy="1"></div>
        </details>`;
    }).join("");
    // Detail is rendered lazily on first open — 200 rows × full breakdown is heavy on mobile.
    const byseq = new Map(rows.map((r) => [String(r.seq), r]));
    listEl.querySelectorAll("details.action-log-row").forEach((d) => {
      d.addEventListener("toggle", () => {
        if (!d.open) return;
        const box = d.querySelector(".action-log-detail");
        if (box.dataset.lazy === "1") {
          box.innerHTML = alBody(byseq.get(d.dataset.seq));
          box.dataset.lazy = "0";
        }
      });
    });
  } catch (e) {
    listEl.innerHTML = `<div class="empty-state">Ошибка загрузки: ${esc(e.message)}</div>`;
  }
}

document.getElementById("actionLogRefresh").addEventListener("click", loadActionLog);
document.getElementById("actionLogKind").addEventListener("change", loadActionLog);
document.getElementById("actionLogPurpose").addEventListener("change", loadActionLog);
document.getElementById("actionLogLimit").addEventListener("change", loadActionLog);

/* ===================== USAGE ===================== */

async function loadUsage() {
  const cards = document.getElementById("usageCards");
  const byKind = document.getElementById("usageByKind");
  const modelsEl = document.getElementById("usageModels");
  const eventsEl = document.getElementById("usageEvents");
  cards.innerHTML = `<div class="card stat-card"><div class="stat-label">загрузка…</div></div>`;
  try {
    const u = await api("/usage");
    const fmtN = (n) => Number(n || 0).toLocaleString("ru-RU");
    const fmtUsd = (n) => `$${Number(n || 0).toFixed(6)}`;
    const last = u.last_event || {};
    const ctx = last.context_length;
    const util = last.context_utilization;

    cards.innerHTML = `
      <div class="card stat-card"><div class="stat-label">вызовов всего</div><div class="stat-value">${fmtN(u.total_calls)}</div></div>
      <div class="card stat-card"><div class="stat-label">входные токены</div><div class="stat-value cold">${fmtN(u.total_input_tokens)}</div><div class="stat-hint">prompt tokens</div></div>
      <div class="card stat-card"><div class="stat-label">выходные токены</div><div class="stat-value warm">${fmtN(u.total_output_tokens)}</div><div class="stat-hint">completion tokens</div></div>
      <div class="card stat-card"><div class="stat-label">реальная стоимость</div><div class="stat-value">${fmtUsd(u.total_cost_usd)}</div><div class="stat-hint">${esc(u.pricing_source || "provider")}</div></div>
    `;
    document.getElementById("usageSpeed").textContent = `${Number(u.avg_generation_tokens_per_second || 0).toFixed(1)} ток/с`;
    document.getElementById("usageContext").textContent = ctx ? fmtN(ctx) : "—";
    document.getElementById("usageContextHint").textContent =
      util != null ? `${(util * 100).toFixed(1)}% занято последним prompt` : (last.model || "контекст неизвестен");
    document.getElementById("usageReasoning").textContent = fmtN(u.total_reasoning_tokens);
    document.getElementById("usageCached").textContent = fmtN(u.total_cached_input_tokens);

    const models = u.models || [];
    modelsEl.innerHTML = models.length ? `
      <div class="usage-table">
        <div class="usage-row usage-head"><span>Модель</span><span>Вход</span><span>Выход</span><span>Контекст</span><span>Цена / 1M</span><span>Стоимость</span></div>
        ${models.map(m => `<div class="usage-row">
          <span class="mono" title="${esc(m.model)}">${esc(m.model || "unknown")}</span>
          <span>${fmtN(m.input_tokens)}</span><span>${fmtN(m.output_tokens)}</span>
          <span>${m.context_length ? fmtN(m.context_length) : "—"}</span>
          <span>${m.prompt_price_per_1m != null ? `$${m.prompt_price_per_1m} / $${m.completion_price_per_1m ?? "—"}` : "—"}</span>
          <span>${fmtUsd(m.cost_usd)}</span>
        </div>`).join("")}
      </div>
      <div class="usage-foot">Источник цен и context window: ${esc(u.pricing_source || "provider response")} · обновлено ${u.pricing_updated_at ? new Date(u.pricing_updated_at * 1000).toLocaleTimeString() : "—"}</div>
    ` : `<div class="empty-state">Нет данных по моделям</div>`;

    const events = (u.events || []).slice().reverse().slice(0, 30);
    eventsEl.innerHTML = events.length ? `
      <div class="usage-table">
        <div class="usage-row usage-head"><span>Время / модель</span><span>Input</span><span>Output</span><span>Контекст</span><span>Скорость</span><span>Цена</span></div>
        ${events.map(e => `<div class="usage-row">
          <span><b>${new Date(e.ts * 1000).toLocaleTimeString()}</b><small>${esc(e.model || "unknown")}</small></span>
          <span>${fmtN(e.input_tokens)}</span><span>${fmtN(e.output_tokens)}</span>
          <span>${e.context_length ? `${fmtN(e.context_length)}${e.context_utilization != null ? ` · ${(e.context_utilization*100).toFixed(1)}%` : ""}` : "—"}</span>
          <span>${Number(e.generation_tokens_per_second || 0).toFixed(1)} ток/с</span>
          <span>${fmtUsd(e.cost_usd)}</span>
        </div>`).join("")}
      </div>` : `<div class="empty-state">Пока не было запросов</div>`;

    const kinds = Object.entries(u.calls_by_kind || {});
    byKind.innerHTML = kinds.length
      ? kinds.map(([k, n]) => `<div class="budget-row"><span>${esc(k)}</span><span class="mono">${n}</span></div>`).join("")
        + `<div class="budget-row"><span>средняя задержка</span><span class="mono">${u.avg_latency_seconds}s</span></div>`
        + `<div class="budget-row"><span>средняя скорость</span><span class="mono">${Number(u.avg_generation_tokens_per_second || 0).toFixed(1)} ток/с</span></div>`
      : `<div class="empty-state">Пока не было вызовов</div>`;
  } catch (e) {
    cards.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

/* ===================== SETTINGS ===================== */

document.getElementById("apiBaseInput").value = getApiBase();
document.getElementById("btnSaveSettings").addEventListener("click", async () => {
  setApiBase(document.getElementById("apiBaseInput").value.trim());
  toast("Сохранено");
  await checkConnection();
  init();
});
document.getElementById("btnTestConn").addEventListener("click", async () => {
  const statusEl = document.getElementById("settingsStatus");
  statusEl.textContent = "Проверка…";
  const ok = await checkConnection();
  statusEl.textContent = ok ? "✓ соединение установлено" : "✗ не удалось подключиться";
});

/* ---------- LLM provider settings ---------- */

function renderLlmStatusPill(data) {
  const pill = document.getElementById("llmStatusPill");
  if (!pill) return;
  if (data.using_mock) {
    pill.className = "llm-status-pill off";
    pill.textContent = "используется заглушка (MockLLM) — провайдер не настроен";
  } else if (data.active) {
    pill.className = "llm-status-pill ok";
    pill.textContent = `активно: ${data.model || "модель не указана"}`;
  } else {
    pill.className = "llm-status-pill off";
    pill.textContent = "не настроено";
  }
}

function fmtPrice(v) {
  if (v == null) return "—";
  // Sub-$1/1M is the common case for small/open models — show enough
  // precision to distinguish e.g. $0.15 from $0.20 without noisy trailing zeros.
  return v < 1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;
}

function fmtCtx(v) {
  if (!v) return "—";
  return v >= 1000 ? `${Math.round(v / 1000)}k` : String(v);
}

// Model-picker factory: builds an independent picker (state + DOM wiring)
// bound to a given set of element ids. Used twice — once for the main
// assistant model, once for the summarizer model — so the two pickers
// don't share selection/sort/filter state.
function makeModelPicker(ids) {
  const state = {
    models: [],       // raw list from /settings/llm/models
    sortKey: "id",
    sortDir: "asc",    // "asc" | "desc"
    query: "",
    onlyPriced: false,
    selectedId: "",
  };

  function getRows() {
    const q = state.query.trim().toLowerCase();
    let rows = state.models.filter((m) => {
      if (state.onlyPriced && m.price_prompt_per_1m == null && m.price_completion_per_1m == null) return false;
      if (!q) return true;
      return m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q);
    });
    const { sortKey, sortDir } = state;
    const dir = sortDir === "asc" ? 1 : -1;
    rows = rows.slice().sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      // Unknown price/context always sorts last regardless of direction —
      // otherwise ascending-price sort would put "unpriced" ahead of "cheap",
      // which reads as backwards to the user.
      const aMissing = av == null;
      const bMissing = bv == null;
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;
      if (bMissing) return -1;
      if (sortKey === "id") return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });
    return rows;
  }

  function renderHead() {
    document.querySelectorAll(`#${ids.head} .mt-col`).forEach((el) => {
      const key = el.dataset.sort;
      const ico = el.querySelector(".sort-ico");
      if (key === state.sortKey) {
        el.classList.add("sorted");
        ico.textContent = state.sortDir === "asc" ? "▲" : "▼";
      } else {
        el.classList.remove("sorted");
        ico.textContent = "";
      }
    });
  }

  function renderBody() {
    const body = document.getElementById(ids.body);
    const rows = getRows();
    if (!rows.length) {
      body.innerHTML = `<div class="model-table-empty">Ничего не найдено</div>`;
      return;
    }
    body.innerHTML = rows.map((m) => {
      const selected = m.id === state.selectedId ? " selected" : "";
      const pIn = m.price_prompt_per_1m;
      const pOut = m.price_completion_per_1m;
      return `
        <div class="model-row-item${selected}" data-id="${esc(m.id)}">
          <span class="mr-id" title="${esc(m.id)}">${esc(m.id)}</span>
          <span class="mr-price${pIn == null ? " unknown" : ""}">${fmtPrice(pIn)}</span>
          <span class="mr-price${pOut == null ? " unknown" : ""}">${fmtPrice(pOut)}</span>
          <span class="mr-ctx">${fmtCtx(m.context_length)}</span>
        </div>`;
    }).join("");
    body.querySelectorAll(".model-row-item").forEach((el) => {
      el.addEventListener("click", () => select(el.dataset.id));
    });
  }

  function select(id) {
    state.selectedId = id;
    document.getElementById(ids.manual).value = id;
    renderBody();
  }

  document.getElementById(ids.head).addEventListener("click", (e) => {
    const col = e.target.closest(".mt-col");
    if (!col) return;
    const key = col.dataset.sort;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDir = "asc"; // cheapest / smallest-context first by default
    }
    renderHead();
    renderBody();
  });
  document.getElementById(ids.search).addEventListener("input", (e) => {
    state.query = e.target.value;
    renderBody();
  });
  document.getElementById(ids.filterPriced).addEventListener("change", (e) => {
    state.onlyPriced = e.target.checked;
    renderBody();
  });
  // Manual field opens/closes the picker; typing directly still works for
  // providers where the user already knows the exact model id.
  document.getElementById(ids.manual).addEventListener("click", () => {
    const picker = document.getElementById(ids.picker);
    if (state.models.length) picker.hidden = !picker.hidden;
  });

  return {
    state,
    renderHead,
    renderBody,
    select,
    async load(baseUrl, apiKeyField, hintEl) {
      const picker = document.getElementById(ids.picker);
      hintEl.textContent = "Загрузка списка моделей…";
      const params = new URLSearchParams({ base_url: baseUrl });
      if (apiKeyField) params.set("api_key", apiKeyField); // else: backend falls back to the saved key
      try {
        const data = await api(`/settings/llm/models?${params.toString()}`);
        const models = data.models || [];
        state.models = models;
        state.query = "";
        document.getElementById(ids.search).value = "";
        const withPrice = models.filter((m) => m.price_prompt_per_1m != null || m.price_completion_per_1m != null).length;
        picker.hidden = models.length === 0;
        renderHead();
        renderBody();
        hintEl.textContent = models.length
          ? `Загружено моделей: ${models.length}${withPrice ? ` · с ценой: ${withPrice}` : " · провайдер не сообщает цены"}`
          : "Провайдер не вернул список моделей.";
      } catch (e) {
        hintEl.textContent = "Ошибка: " + e.message;
      }
    },
  };
}

const modelPicker = makeModelPicker({
  head: "modelTableHead", body: "modelTableBody", manual: "llmModelManual",
  picker: "modelPicker", search: "modelSearch", filterPriced: "modelFilterPriced",
});
const summarizerModelPicker = makeModelPicker({
  head: "modelTableHeadSummarizer", body: "modelTableBodySummarizer", manual: "llmSummarizerModelManual",
  picker: "modelPickerSummarizer", search: "modelSearchSummarizer", filterPriced: "modelFilterPricedSummarizer",
});
const verifyModelPicker = makeModelPicker({
  head: "modelTableHeadVerify", body: "modelTableBodyVerify", manual: "llmVerifyModelManual",
  picker: "modelPickerVerify", search: "modelSearchVerify", filterPriced: "modelFilterPricedVerify",
});
const STRICTNESS_LABELS = {
  1: "1 · легко", 2: "2 · мягко", 3: "3 · средне", 4: "4 · строго", 5: "5 · очень строго",
};
function setStrictnessLabel() {
  const v = document.getElementById("llmVerifyStrictness").value;
  document.getElementById("llmVerifyStrictnessLabel").textContent = STRICTNESS_LABELS[v] || v;
}
document.getElementById("llmVerifyStrictness").addEventListener("input", setStrictnessLabel);

const visionModelPicker = makeModelPicker({
  head: "modelTableHeadVision", body: "modelTableBodyVision", manual: "llmVisionModelManual",
  picker: "modelPickerVision", search: "modelSearchVision", filterPriced: "modelFilterPricedVision",
});
// Legacy alias — some code below still refers to the main picker's state
// directly by its old name.
const modelPickerState = modelPicker.state;

async function loadLlmSettings() {
  try {
    const data = await api("/settings/llm");
    document.getElementById("llmBaseUrl").value = data.base_url || "";
    document.getElementById("llmSiteUrl").value = data.site_url || "";
    document.getElementById("llmSiteName").value = data.site_name || "";
    document.getElementById("llmApiKey").value = "";
    document.getElementById("llmApiKey").placeholder = data.api_key_set
      ? `сохранён: ${data.api_key_preview} (оставьте пустым, чтобы не менять)`
      : "не задан";
    document.getElementById("llmApiKeyHint").textContent = data.api_key_set
      ? "Ключ хранится на сервере и не показывается полностью. Впишите новый, чтобы заменить."
      : "";
    modelPickerState.selectedId = data.model || "";
    document.getElementById("llmModelManual").value = data.model || "";
    document.getElementById("llmVerifyStrictness").value = data.verify_strictness ?? 3;
    setStrictnessLabel();
    visionModelPicker.state.selectedId = data.vision_model || "";
    document.getElementById("llmVisionModelManual").value = data.vision_model || "";
    verifyModelPicker.state.selectedId = data.verify_model || "";
    document.getElementById("llmVerifyModelManual").value = data.verify_model || "";
    document.getElementById("llmVerifyModelManual").placeholder = data.verify_model
      ? ""
      : `пусто = как для суммаризации (сейчас: ${data.effective_verify_model || "—"})`;
    summarizerModelPicker.state.selectedId = data.summarizer_model || "";
    document.getElementById("llmSummarizerModelManual").value = data.summarizer_model || "";
    document.getElementById("llmSummarizerModelManual").placeholder = data.summarizer_model
      ? ""
      : `пусто = как основная (сейчас: ${data.effective_summarizer_model || "—"})`;
    document.getElementById("llmTemperature").value = data.temperature ?? 0.3;
    document.getElementById("llmMaxTokens").value = data.max_tokens ?? 1024;
    document.getElementById("llmTopP").value = data.top_p ?? "";
    document.getElementById("llmContextWindow").value = data.context_window ?? 8192;
    document.getElementById("llmMaxToolIterations").value = data.max_tool_iterations ?? 10;
    document.getElementById("llmCompressionTriggerPct").value = data.compression_trigger_pct ?? 50;
    document.getElementById("llmTopK").value = data.top_k ?? "";
    document.getElementById("llmFrequencyPenalty").value = data.frequency_penalty ?? "";
    document.getElementById("llmPresencePenalty").value = data.presence_penalty ?? "";
    document.getElementById("llmReasoningEnabled").checked = !!data.reasoning_enabled;
    document.getElementById("llmReasoningEffort").value = data.reasoning_effort || "medium";
    renderLlmStatusPill(data);
  } catch (e) {
    const statusEl = document.getElementById("llmSettingsStatus");
    if (statusEl) statusEl.textContent = "Не удалось загрузить настройки: " + e.message;
  }
}

async function loadLlmModels() {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  await modelPicker.load(baseUrl, apiKeyField, hint);
}
document.getElementById("btnLoadModels").addEventListener("click", loadLlmModels);

async function loadSummarizerModels() {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  // Same provider/base URL as the main model — reuses the shared models endpoint.
  await summarizerModelPicker.load(baseUrl, apiKeyField, hint);
}
document.getElementById("btnLoadModelsSummarizer").addEventListener("click", loadSummarizerModels);
document.getElementById("btnLoadModelsVerify").addEventListener("click", async () => {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  await verifyModelPicker.load(baseUrl, apiKeyField, hint);
});
document.getElementById("btnLoadModelsVision").addEventListener("click", async () => {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  await visionModelPicker.load(baseUrl, apiKeyField, hint);
});
document.getElementById("btnClearVisionModel").addEventListener("click", () => {
  visionModelPicker.state.selectedId = "";
  document.getElementById("llmVisionModelManual").value = "";
  visionModelPicker.renderBody();
});
document.getElementById("btnClearVerifyModel").addEventListener("click", () => {
  verifyModelPicker.state.selectedId = "";
  document.getElementById("llmVerifyModelManual").value = "";
  document.getElementById("llmVerifyModelManual").placeholder = "пусто = как для суммаризации";
  verifyModelPicker.renderBody();
});
document.getElementById("btnClearSummarizerModel").addEventListener("click", () => {
  summarizerModelPicker.state.selectedId = "";
  document.getElementById("llmSummarizerModelManual").value = "";
  document.getElementById("llmSummarizerModelManual").placeholder = "пусто = как основная модель";
  summarizerModelPicker.renderBody();
});

document.getElementById("btnSaveLlmSettings").addEventListener("click", async () => {
  const statusEl = document.getElementById("llmSettingsStatus");
  const btn = document.getElementById("btnSaveLlmSettings");
  const model = document.getElementById("llmModelManual").value.trim();
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();

  if (!baseUrl || !model) {
    statusEl.textContent = "Укажите base URL и модель.";
    return;
  }

  const tempRaw = document.getElementById("llmTemperature").value.trim();
  const maxTokensRaw = document.getElementById("llmMaxTokens").value.trim();
  const topPRaw = document.getElementById("llmTopP").value.trim();
  const contextWindowRaw = document.getElementById("llmContextWindow").value.trim();
  const maxToolIterationsRaw = document.getElementById("llmMaxToolIterations").value.trim();
  const compressionTriggerPctRaw = document.getElementById("llmCompressionTriggerPct").value.trim();
  const topKRaw = document.getElementById("llmTopK").value.trim();
  const freqPenaltyRaw = document.getElementById("llmFrequencyPenalty").value.trim();
  const presPenaltyRaw = document.getElementById("llmPresencePenalty").value.trim();
  
  if (tempRaw && (Number(tempRaw) < 0 || Number(tempRaw) > 2)) {
    statusEl.textContent = "Temperature должна быть в диапазоне 0–2.";
    return;
  }
  if (maxTokensRaw && Number(maxTokensRaw) < 1) {
    statusEl.textContent = "Max tokens должен быть положительным числом.";
    return;
  }
  if (topPRaw && (Number(topPRaw) < 0 || Number(topPRaw) > 1)) {
    statusEl.textContent = "Top P должен быть в диапазоне 0–1.";
    return;
  }
  if (contextWindowRaw && Number(contextWindowRaw) < 1024) {
    statusEl.textContent = "Context window должен быть не менее 1024 токенов.";
    return;
  }
  if (maxToolIterationsRaw && Number(maxToolIterationsRaw) < 1) {
    statusEl.textContent = "Max tool iterations должен быть не менее 1.";
    return;
  }
  if (topKRaw && Number(topKRaw) < 1) {
    statusEl.textContent = "Top K должен быть положительным числом.";
    return;
  }
  if (freqPenaltyRaw && (Number(freqPenaltyRaw) < -2 || Number(freqPenaltyRaw) > 2)) {
    statusEl.textContent = "Frequency penalty должен быть в диапазоне -2 до 2.";
    return;
  }
  if (presPenaltyRaw && (Number(presPenaltyRaw) < -2 || Number(presPenaltyRaw) > 2)) {
    statusEl.textContent = "Presence penalty должен быть в диапазоне -2 до 2.";
    return;
  }

  btn.disabled = true;
  statusEl.textContent = "Сохранение и применение…";
  try {
    const payload = {
      base_url: baseUrl,
      model: model,
      summarizer_model: document.getElementById("llmSummarizerModelManual").value.trim(),
      verify_model: document.getElementById("llmVerifyModelManual").value.trim(),
      vision_model: document.getElementById("llmVisionModelManual").value.trim(),
      verify_strictness: Number(document.getElementById("llmVerifyStrictness").value),
      site_url: document.getElementById("llmSiteUrl").value.trim(),
      site_name: document.getElementById("llmSiteName").value.trim(),
      reasoning_enabled: document.getElementById("llmReasoningEnabled").checked,
      reasoning_effort: document.getElementById("llmReasoningEffort").value,
    };
    if (tempRaw) payload.temperature = Number(tempRaw);
    if (maxTokensRaw) payload.max_tokens = Number(maxTokensRaw);
    if (topPRaw) {
      payload.top_p = Number(topPRaw);
    } else {
      payload.clear_top_p = true; // empty field = explicitly unset, not "leave unchanged"
    }
    if (contextWindowRaw) payload.context_window = Number(contextWindowRaw);
    if (maxToolIterationsRaw) payload.max_tool_iterations = Number(maxToolIterationsRaw);
    if (compressionTriggerPctRaw) payload.compression_trigger_pct = Number(compressionTriggerPctRaw);
    if (topKRaw) {
      payload.top_k = Number(topKRaw);
    } else {
      payload.clear_top_k = true;
    }
    if (freqPenaltyRaw) {
      payload.frequency_penalty = Number(freqPenaltyRaw);
    } else {
      payload.clear_frequency_penalty = true;
    }
    if (presPenaltyRaw) {
      payload.presence_penalty = Number(presPenaltyRaw);
    } else {
      payload.clear_presence_penalty = true;
    }
    const apiKeyField = document.getElementById("llmApiKey").value;
    if (apiKeyField) payload.api_key = apiKeyField; // blank = keep the stored key server-side
    const data = await api("/settings/llm", { method: "PUT", body: payload });
    statusEl.textContent = "✓ применено — новые сообщения в чате пойдут через этот провайдер.";
    document.getElementById("llmVisionModelManual").value = data.vision_model || "";
    document.getElementById("llmVerifyModelManual").value = data.verify_model || "";
    document.getElementById("llmVerifyModelManual").placeholder = data.verify_model
      ? ""
      : `пусто = как для суммаризации (сейчас: ${data.effective_verify_model || "—"})`;
    document.getElementById("llmVisionModelManual").value = data.vision_model || "";
    document.getElementById("llmVerifyStrictness").value = data.verify_strictness ?? 3;
    setStrictnessLabel();
    document.getElementById("llmSummarizerModelManual").value = data.summarizer_model || "";
    document.getElementById("llmSummarizerModelManual").placeholder = data.summarizer_model
      ? ""
      : `пусто = как основная (сейчас: ${data.effective_summarizer_model || "—"})`;
    document.getElementById("llmTemperature").value = data.temperature ?? 0.3;
    document.getElementById("llmMaxTokens").value = data.max_tokens ?? 1024;
    document.getElementById("llmTopP").value = data.top_p ?? "";
    document.getElementById("llmContextWindow").value = data.context_window ?? 8192;
    document.getElementById("llmMaxToolIterations").value = data.max_tool_iterations ?? 10;
    document.getElementById("llmCompressionTriggerPct").value = data.compression_trigger_pct ?? 50;
    document.getElementById("llmTopK").value = data.top_k ?? "";
    document.getElementById("llmFrequencyPenalty").value = data.frequency_penalty ?? "";
    document.getElementById("llmPresencePenalty").value = data.presence_penalty ?? "";
    document.getElementById("llmReasoningEnabled").checked = !!data.reasoning_enabled;
    document.getElementById("llmReasoningEffort").value = data.reasoning_effort || "medium";
    renderLlmStatusPill(data);
    document.getElementById("llmApiKey").value = "";
    document.getElementById("llmApiKey").placeholder = data.api_key_set
      ? `сохранён: ${data.api_key_preview} (оставьте пустым, чтобы не менять)`
      : "не задан";
    toast("Настройки LLM сохранены");
    loadCacheStatus();
  } catch (e) {
    statusEl.textContent = "Ошибка: " + e.message;
    toast("Не удалось сохранить настройки LLM: " + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

/* ===================== init ===================== */

async function init() {
  initUsageWidget();
  await checkConnection();
  await loadPersonasForChat();
  await loadChatHistory();
  await loadLlmSettings();
  refreshHeaderStack();
  startCachePolling();
}

init();

(function initChatToolbarToggle() {
  const bar = document.getElementById("chatToolbar");
  const btn = document.getElementById("chatToolbarToggle");
  if (!bar || !btn) return;
  const KEY = "chatToolbarCollapsed";
  // collapsed by default; only an explicit "0" (user opened it) keeps it open
  if (localStorage.getItem(KEY) !== "0") bar.classList.add("collapsed");
  btn.addEventListener("click", () => {
    bar.classList.toggle("collapsed");
    localStorage.setItem(KEY, bar.classList.contains("collapsed") ? "1" : "0");
  });
})();

/* ===================== stats modal (header pill) ===================== */


/* ===================== stats modal (event delegation) =====================
   Bound on document, not on the button itself, so it works no matter
   when #statsToggle appears in the DOM. Replaces the earlier
   initStatsModal IIFE, which bailed silently whenever the button's id
   attribute was malformed.
   ------------------------------------------------------------------------- */
(function paiStatsDelegation() {
  function closeModal() {
    const m = document.getElementById("statsModal");
    const b = document.getElementById("statsModalBackdrop");
    if (m) m.classList.remove("visible");
    if (b) b.classList.remove("visible");
  }
  function toggleModal() {
    const m = document.getElementById("statsModal");
    const b = document.getElementById("statsModalBackdrop");
    if (!m || !b) return;
    const willOpen = !m.classList.contains("visible");
    m.classList.toggle("visible", willOpen);
    b.classList.toggle("visible", willOpen);
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("#statsToggle")) { toggleModal(); return; }
    if (e.target.closest("#statsModalClose") ||
        e.target.id === "statsModalBackdrop") { closeModal(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
})();


/* ===================== hide-tool-calls toggle =====================
   Restores the checkbox in the chat toolbar: default ON (= tool traces
   hidden), persisted under pai.hideToolCalls. Runs at script parse time,
   before init() calls loadChatHistory, so the state is correct before
   any live trace is rendered.
   ------------------------------------------------------------------------- */
(function paiHideToolCallsToggle() {
  const cb = document.getElementById("hideToolCalls");
  if (!cb) return;
  const stored = localStorage.getItem("pai.hideToolCalls");
  cb.checked = stored === null ? true : stored === "1";
  cb.addEventListener("change", () => {
    localStorage.setItem("pai.hideToolCalls", cb.checked ? "1" : "0");
    document.querySelectorAll(".tool-trace").forEach((el) => {
      el.style.display = cb.checked ? "none" : "";
    });
  });
})();
