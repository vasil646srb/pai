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
  if (row.kind === "laya") return alLayaLabel(row);
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

function alLayaLabel(row) {
  const lay = (alParse(row.response) || {}).laya || {};
  const parts = Object.entries(lay).map(([q, v]) => `${esc(q)} = ${esc(v.label || "?")} (${Math.round((v.p || 0) * 100)}%)`);
  return `laya · <span class="mono">${esc(row.name || "")}</span>${parts.length ? " · " + parts.join(", ") : ""}`;
}

function alLayaBody(row) {
  const req = alParse(row.request) || {};
  const resp = alParse(row.response) || {};
  const lay = resp.laya || {};
  let html = `<div class="al-sec">Запрос</div><pre class="mono al-pre">${esc(JSON.stringify(req, null, 2))}</pre>`;
  html += `<div class="al-sec">Ответ Laya</div>`;
  for (const q of Object.keys(lay)) {
    const v = lay[q] || {};
    const probs = Object.entries(v.probs || {}).sort((a, b) => b[1] - a[1])
      .map(([k, p]) => `${esc(k)}: ${(p * 100).toFixed(1)}%`).join(" · ");
    html += `<div class="al-note">${esc(q)} → <b>${esc(v.label || "—")}</b> · отступ ${esc(String(v.margin ?? "—"))} · confidence ${esc(String(v.confidence ?? "—"))}</div>`;
    html += `<div class="mono al-note">${probs}</div>`;
  }
  if (row.latency_seconds != null) html += `<div class="al-note">задержка ${Number(row.latency_seconds).toFixed(3)} с</div>`;
  if (resp.actual) html += `<div class="al-sec">Факт (старый путь)</div><pre class="mono al-pre">${esc(JSON.stringify(resp.actual, null, 2))}</pre>`;
  if (row.error) html += `<div class="al-sec">ошибка</div><pre class="mono al-pre">${esc(row.error)}</pre>`;
  return html;
}

function alBody(row) {
  if (row.kind === "laya") return alLayaBody(row);
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
      const kindBadge = row.kind === "laya" ? `<span class="badge-status laya">laya</span>` : row.kind === "llm_request"
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

async function loadBalance(refresh = false) {
  const cards = document.getElementById("balanceCards");
  const note = document.getElementById("balanceNote");
  const fmtUsd = (n) => (n == null ? "—" : `$${Number(n).toFixed(2)}`);
  cards.innerHTML = `<div class="card stat-card"><div class="stat-label">загрузка…</div></div>`;
  try {
    const b = await api(`/usage/balance${refresh ? "?refresh=true" : ""}`);
    if (!b.supported) {
      cards.innerHTML = "";
      note.textContent = "Баланс показывается только при провайдере OpenRouter.";
      return;
    }
    const k = b.key || {};
    const unlimited = k.limit == null;
    const acc = b.account;
    cards.innerHTML = `
      <div class="card stat-card"><div class="stat-label">остаток на ключе</div><div class="stat-value warm">${b.key ? (unlimited ? "без лимита" : fmtUsd(k.limit_remaining)) : "—"}</div><div class="stat-hint">${unlimited ? "лимит на ключе не задан" : `из ${fmtUsd(k.limit)}${k.limit_reset ? `, сброс: ${esc(k.limit_reset)}` : ""}`}</div></div>
      <div class="card stat-card"><div class="stat-label">потрачено сегодня</div><div class="stat-value">${fmtUsd(k.usage_daily)}</div><div class="stat-hint">UTC-день</div></div>
      <div class="card stat-card"><div class="stat-label">за месяц</div><div class="stat-value">${fmtUsd(k.usage_monthly)}</div><div class="stat-hint">всего с начала: ${fmtUsd(k.usage)}</div></div>
      ${acc ? `<div class="card stat-card"><div class="stat-label">кредиты аккаунта</div><div class="stat-value">${fmtUsd(acc.total_credits - acc.total_usage)}</div><div class="stat-hint">куплено ${fmtUsd(acc.total_credits)}</div></div>` : ""}`;
    const stamp = new Date(b.ts * 1000).toLocaleTimeString("ru-RU");
    note.textContent = (b.error ? `⚠ ${b.error} · ` : "") + `обновлено ${stamp}`;
  } catch (e) {
    cards.innerHTML = "";
    note.textContent = "⚠ Не удалось получить баланс: " + e.message;
  }
}

document.getElementById("btnBalanceRefresh").addEventListener("click", () => loadBalance(true));

async function loadUsage() {
  loadBalance();
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

