// Разделы боковой панели: «Поиск по памяти» (view-semantic) и «Эмбеддинги» (view-embeddings).
// Опирается на api() из app.js. Все данные выводятся через textContent.
(function () {
  const $ = (id) => document.getElementById(id);
  const KIND = { node: "память", file: "файл", inbox: "входящее", request: "запрос" };
  let timer = null;

  function say(id, text, isErr = false) {
    const el = $(id);
    el.textContent = text;
    el.className = "form-msg" + (isErr ? " err" : "");
  }

  function statusText(s) {
    const chunks = Object.entries(s.chunks || {}).map(([k, v]) => `${KIND[k] || k}: ${v}`).join(", ") || "индекс пуст";
    let line = `${s.enabled ? "включён" : "выключен"} · ${chunks} · в очереди: ${s.pending}`;
    if (s.backfill_pending) line += " · идёт индексация";
    if (s.other_model_chunks) line += ` · чанков другой модели: ${s.other_model_chunks}`;
    if (s.last_error) line += ` · ошибка: ${s.last_error}`;
    return line;
  }

  function fmtEta(sec) {
    if (sec == null) return "—";
    if (sec === 0) return "0 сек";
    if (sec < 60) return `${sec} сек`;
    if (sec < 3600) return `${Math.round(sec / 60)} мин`;
    return `${Math.floor(sec / 3600)} ч ${Math.round((sec % 3600) / 60)} мин`;
  }

  function renderProgress(p) {
    const box = $("embProgress");
    if (!box) return;
    const running = !!p && p.state === "running" && p.total > 0;
    box.hidden = !running;
    if (!running) return;
    $("embProgressBar").style.width = p.percent + "%";
    $("embProgressText").textContent =
      `Индексация: обработано ${p.done} из ${p.total} фрагментов (${p.percent}%) · ` +
      `${p.rate_per_sec} фр./с · осталось ~${fmtEta(p.eta_sec)}`;
  }

  async function refreshStatus() {
    try {
      const s = await api("/semantic/status");
      const text = statusText(s);
      $("embStatus").textContent = text;
      $("semStatus").textContent = text;
      renderProgress(s.progress);
    } catch (e) {
      $("embStatus").textContent = e.message;
      $("semStatus").textContent = e.message;
    }
  }

  function fillPrecision(s) {
    const sel = $("semPrecision");
    sel.innerHTML = "";
    for (const lv of s.precision_levels) {
      const o = document.createElement("option");
      o.value = lv.level;
      o.textContent = `${lv.level} · ${lv.label} (≥ ${lv.min_score})`;
      if (lv.level === s.precision) o.selected = true;
      sel.appendChild(o);
    }
  }

  async function loadSettings() {
    const s = await api("/semantic/settings");
    $("embEnabled").checked = s.enabled;
    $("embBaseUrl").value = s.base_url;
    $("embModel").value = s.model;
    $("embDim").value = String(s.dim);
    $("embApi").value = s.api_format || "openai";
    $("embChunk").value = s.chunk_size;
    $("embOverlap").value = s.chunk_overlap;
    $("semTopK").value = s.top_k;
    $("semMinScore").value = s.min_score ?? "";
    fillPrecision(s);
  }

  async function loadAll() {
    try { await loadSettings(); } catch (e) { say("embMsg", e.message, true); }
    await refreshStatus();
    if (!timer) {
      timer = setInterval(() => {
        const active = $("view-semantic").classList.contains("active") ||
                       $("view-embeddings").classList.contains("active");
        if (active) refreshStatus();
      }, 5000);
    }
  }

  async function saveEmbeddings() {
    try {
      await api("/semantic/settings", { method: "PUT", body: {
        enabled: $("embEnabled").checked,
        base_url: $("embBaseUrl").value.trim(),
        model: $("embModel").value.trim(),
        dim: +$("embDim").value,
        api_format: $("embApi").value,
        chunk_size: +$("embChunk").value,
        chunk_overlap: +$("embOverlap").value,
      } });
      say("embMsg", "Сохранено.");
      await refreshStatus();
    } catch (e) { say("embMsg", e.message, true); }
  }

  async function saveSearchSettings() {
    try {
      await api("/semantic/settings", { method: "PUT", body: {
        precision: +$("semPrecision").value,
        top_k: +$("semTopK").value,
        min_score: $("semMinScore").value === "" ? null : +$("semMinScore").value,
      } });
      say("semSaveMsg", "Сохранено.");
    } catch (e) { say("semSaveMsg", e.message, true); }
  }

  async function ping() {
    try {
      const p = await api("/semantic/ping", { method: "POST" });
      say("embMsg", `Сервер отвечает: dim ${p.dim}, ${p.latency_ms} мс`);
    } catch (e) { say("embMsg", e.message, true); }
  }

  async function reindex() {
    try {
      await api("/semantic/reindex", { method: "POST" });
      say("embMsg", "Переиндексация запущена.");
      await refreshStatus();
    } catch (e) { say("embMsg", e.message, true); }
  }

  function renderHit(h) {
    const box = document.createElement("div");
    box.className = "sem-hit";
    const meta = document.createElement("div");
    meta.className = "sem-meta";
    const when = h.occurred_at ? new Date(h.occurred_at * 1000).toLocaleString("ru-RU") : "";
    for (const s of [`cos ${h.score.toFixed(3)}`, KIND[h.kind] || h.kind, h.space_id, when]) {
      const sp = document.createElement("span");
      sp.textContent = s;
      meta.appendChild(sp);
    }
    const bar = document.createElement("div");
    bar.className = "sem-bar";
    const fill = document.createElement("i");
    fill.style.width = Math.round(Math.max(0, Math.min(1, h.score)) * 100) + "%";
    bar.appendChild(fill);
    const text = document.createElement("div");
    text.className = "sem-text";
    text.textContent = h.text;
    box.append(meta, bar, text);
    return box;
  }

  async function runSearch(ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    const query = $("semQuery").value.trim();
    if (!query) return;
    const box = $("semResults");
    box.textContent = "Ищу…";
    try {
      const data = await api("/semantic/search", { method: "POST", body: {
        query,
        precision: +$("semPrecision").value,
        top_k: +$("semTopK").value,
        space_id: $("semSpace").value.trim() || null,
      } });
      box.innerHTML = "";
      const head = document.createElement("div");
      head.className = "field-hint";
      head.textContent = `Найдено: ${data.count} · порог cos ≥ ${data.min_score}`;
      box.appendChild(head);
      if (!data.count) {
        const none = document.createElement("div");
        none.className = "empty-state";
        none.textContent = "Ничего не найдено выше порога. Понизьте точность.";
        box.appendChild(none);
      }
      data.results.forEach((h) => box.appendChild(renderHit(h)));
    } catch (e) {
      box.textContent = e.message;
    }
    refreshStatus();
  }

  $("embSave").addEventListener("click", saveEmbeddings);
  $("embPing").addEventListener("click", ping);
  $("embReindex").addEventListener("click", reindex);
  $("semSaveBtn").addEventListener("click", saveSearchSettings);
  $("semSearchBtn").addEventListener("click", runSearch);
  $("semQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(e); });

  window.loadSemanticView = loadAll;
  window.loadEmbeddingsView = loadAll;
})();
