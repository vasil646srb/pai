// Раздел «Распознавание речи»: настройки Whisper-сервера, проверка связи и история обработанных файлов.
// Опирается на api() из app.js. Тексты речи — недоверенные данные: везде textContent, без innerHTML.
(function () {
  const $ = (id) => document.getElementById(id);
  const SOURCE = { upload: "веб-чат", "telegram-group": "группа Telegram", "telegram-owner": "личка владельца" };
  const STATUS = {
    ok: { label: "распознано", cls: "validated" },
    empty: { label: "речи нет", cls: "comp" },
    error: { label: "ошибка", cls: "rejected" },
  };
  let timer = null;

  function say(id, text, isErr = false) {
    const el = $(id);
    el.textContent = text;
    el.className = "form-msg" + (isErr ? " err" : "");
  }

  function fmtDuration(sec) {
    if (!sec) return "—";
    if (sec < 60) return `${sec} с`;
    return `${Math.floor(sec / 60)} мин ${sec % 60} с`;
  }

  function fmtSize(bytes) {
    if (!bytes) return "—";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
    return `${(bytes / 1048576).toFixed(1)} МБ`;
  }

  async function loadSettings() {
    const s = await api("/transcribe/settings");
    $("trEnabled").checked = !!s.enabled;
    $("trBaseUrl").value = s.base_url || "";
    $("trModel").value = s.model || "";
    $("trLanguage").value = s.language || "";
    $("trTimeout").value = s.timeout;
    $("trMaxDuration").value = s.max_duration;
    $("trState").textContent = s.ready
      ? `Готово: ${s.base_url}, модель ${s.model}`
      : "Не настроено: включите распознавание и укажите адрес сервера.";
  }

  function renderSummary(sum) {
    $("trSummary").textContent =
      `Всего: ${sum.total} · за сутки: ${sum.today} · распознано: ${sum.ok} · без речи: ${sum.empty} · ошибок: ${sum.error}`;
  }

  function cell(text, cls) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    return td;
  }

  function renderRow(r) {
    const tr = document.createElement("tr");
    const st = STATUS[r.status] || { label: r.status, cls: "" };
    const badge = document.createElement("span");
    badge.className = `badge-status ${st.cls}`;
    badge.textContent = st.label;
    const statusTd = document.createElement("td");
    statusTd.appendChild(badge);

    const textTd = document.createElement("td");
    if (r.status === "error") {
      textTd.textContent = r.error || "ошибка";
    } else if (r.preview) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = r.preview.slice(0, 80) + (r.preview.length > 80 ? "…" : "");
      const body = document.createElement("div");
      body.className = "field-hint";
      body.style.whiteSpace = "pre-wrap";
      body.textContent = r.preview + (r.chars > r.preview.length ? `\n… всего символов: ${r.chars}` : "");
      det.append(sum, body);
      textTd.appendChild(det);
    } else {
      textTd.textContent = "—";
    }

    tr.append(
      cell(new Date(r.ts * 1000).toLocaleString("ru-RU")),
      cell(SOURCE[r.source] || r.source),
      cell(r.file, "mono"),
      cell(r.kind || "—"),
      cell(fmtSize(r.size)),
      cell(fmtDuration(r.duration)),
      statusTd,
      textTd,
    );
    return tr;
  }

  async function loadHistory() {
    const box = $("trHistory");
    const qs = new URLSearchParams({ limit: "200" });
    if ($("trFilterSource").value) qs.set("source", $("trFilterSource").value);
    if ($("trFilterStatus").value) qs.set("status", $("trFilterStatus").value);
    try {
      const data = await api(`/transcribe/history?${qs.toString()}`);
      renderSummary(data.summary);
      box.innerHTML = "";
      if (!data.items.length) {
        const none = document.createElement("div");
        none.className = "empty-state";
        none.textContent = "Пока нет обработанных файлов. Загрузите аудио в чат или дождитесь голосового в Telegram.";
        box.appendChild(none);
        return;
      }
      const wrap = document.createElement("div");
      wrap.className = "usage-table-wrap";
      const table = document.createElement("table");
      table.className = "usage-table";
      const head = document.createElement("tr");
      for (const h of ["Время", "Источник", "Файл", "Формат", "Размер", "Длительность", "Статус", "Текст"]) {
        const th = document.createElement("th");
        th.textContent = h;
        head.appendChild(th);
      }
      const thead = document.createElement("thead");
      thead.appendChild(head);
      const tbody = document.createElement("tbody");
      data.items.forEach((r) => tbody.appendChild(renderRow(r)));
      table.append(thead, tbody);
      wrap.appendChild(table);
      box.appendChild(wrap);
    } catch (e) {
      box.textContent = e.message;
    }
  }

  async function loadAll() {
    try { await loadSettings(); } catch (e) { say("trMsg", e.message, true); }
    await loadHistory();
    if (!timer) {
      timer = setInterval(() => {
        if ($("view-transcribe").classList.contains("active")) loadHistory();
      }, 5000);
    }
  }

  async function saveSettings() {
    try {
      const s = await api("/transcribe/settings", { method: "PUT", body: {
        enabled: $("trEnabled").checked,
        base_url: $("trBaseUrl").value.trim(),
        model: $("trModel").value.trim(),
        language: $("trLanguage").value.trim(),
        timeout: +$("trTimeout").value,
        max_duration: +$("trMaxDuration").value,
      } });
      say("trMsg", "Сохранено.");
      $("trState").textContent = s.ready ? `Готово: ${s.base_url}, модель ${s.model}` : "Не настроено.";
    } catch (e) { say("trMsg", e.message, true); }
  }

  async function ping() {
    say("trMsg", "Проверяю…");
    try {
      const p = await api("/transcribe/ping", { method: "POST" });
      const said = p.text ? `вернул текст: «${p.text}»` : "тишину распознал без текста — это нормально";
      say("trMsg", `Сервер отвечает за ${p.latency_ms} мс, модель ${p.model}: ${said}.`);
    } catch (e) { say("trMsg", e.message, true); }
  }

  async function clearHistory() {
    if (!confirm("Очистить историю распознавания? Сами файлы и их тексты в памяти останутся.")) return;
    try {
      await api("/transcribe/history", { method: "DELETE" });
      await loadHistory();
    } catch (e) { say("trHistMsg", e.message, true); }
  }

  $("trSave").addEventListener("click", saveSettings);
  $("trPing").addEventListener("click", ping);
  $("trFilterSource").addEventListener("change", loadHistory);
  $("trFilterStatus").addEventListener("change", loadHistory);
  $("trHistRefresh").addEventListener("click", loadHistory);
  $("trHistClear").addEventListener("click", clearHistory);

  window.loadTranscribeView = loadAll;
})();
