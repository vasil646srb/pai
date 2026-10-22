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

