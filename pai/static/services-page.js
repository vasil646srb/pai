const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (ts) => ts ? new Date(ts * 1000).toLocaleString("ru-RU") : "—";

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
  return data;
}
function say(text, isErr = false) {
  const m = $("msg"); m.textContent = text; m.className = "msg" + (isErr ? " err" : "");
}

async function loadPersonas() {
  const list = await api("/personas");
  $("persona").innerHTML = list.map((p) => `<option value="${esc(p.persona_id)}">${esc(p.name)}</option>`).join("");
}

function resetForm() {
  $("editId").value = ""; $("formTitle").textContent = "Новая служба";
  $("name").value = ""; $("prompt").value = ""; $("interval").value = 60; $("space").value = "personal";
  $("enabled").checked = true; $("btnSave").textContent = "Создать"; $("btnCancel").hidden = true;
  say("");
}

function fillForm(s) {
  $("editId").value = s.id; $("formTitle").textContent = "Редактирование: " + s.name;
  $("name").value = s.name; $("persona").value = s.persona_id; $("prompt").value = s.prompt;
  $("interval").value = s.interval_minutes; $("space").value = s.space_id; $("enabled").checked = !!s.enabled;
  $("btnSave").textContent = "Сохранить"; $("btnCancel").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function save() {
  const body = {
    name: $("name").value.trim(), persona_id: $("persona").value, prompt: $("prompt").value.trim(),
    interval_minutes: Number($("interval").value), space_id: $("space").value.trim() || "personal",
    enabled: $("enabled").checked,
  };
  const id = $("editId").value;
  try {
    $("btnSave").disabled = true;
    if (id) await api(`/services/${id}`, { method: "PUT", body });
    else await api("/services", { method: "POST", body });
    resetForm(); say("Сохранено."); await refresh();
  } catch (e) { say(e.message, true); }
  finally { $("btnSave").disabled = false; }
}

async function act(fn, okText) {
  try { await fn(); if (okText) say(okText); await refresh(); }
  catch (e) { say(e.message, true); }
}

function card(s) {
  const status = s.running ? '<span class="badge run">выполняется</span>'
    : s.enabled ? '<span class="badge on">включена</span>' : '<span class="badge off">выключена</span>';
  const runs = (s.runs || []).slice().reverse().map((r) => r.ok
    ? `<div class="run">${fmt(r.finished_at)} · ${r.duration_seconds} с\n${esc(r.reply)}</div>`
    : `<div class="run err">${fmt(r.finished_at)} · ошибка\n${esc(r.error)}</div>`).join("");
  return `
  <div class="card">
    <div class="row" style="justify-content:space-between;align-items:center">
      <strong>${esc(s.name)}</strong> ${status}
    </div>
    <div class="meta">персона: ${esc(s.persona_id)} · каждые ${s.interval_minutes} мин · пространство: ${esc(s.space_id)}</div>
    <div class="meta">последний запуск: ${fmt(s.last_run_at)} · следующий: ${s.enabled ? fmt(s.next_run_at) : "—"}</div>
    <div class="meta" style="margin-top:6px;white-space:pre-wrap">${esc(s.prompt)}</div>
    <div class="actions" data-id="${esc(s.id)}">
      <button data-act="run" ${s.running ? "disabled" : ""}>Запустить сейчас</button>
      <button data-act="toggle" data-enabled="${s.enabled ? "1" : "0"}">${s.enabled ? "Выключить" : "Включить"}</button>
      <button data-act="edit">Изменить</button>
      <button class="danger" data-act="delete" data-name="${esc(s.name)}">Удалить</button>
    </div>
    ${runs ? `<details style="margin-top:8px"><summary class="meta">История (${s.runs.length})</summary>${runs}</details>` : '<div class="meta" style="margin-top:8px">Запусков пока не было.</div>'}
  </div>`;
}

async function refresh() {
  try {
    const list = await api("/services");
    $("list").innerHTML = list.length ? list.map(card).join("") : '<p class="meta">Служб пока нет.</p>';
  } catch (e) { say(e.message, true); }
}

$("list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.parentElement.dataset.id;
  const act_ = btn.dataset.act;
  if (act_ === "run") act(() => api(`/services/${id}/run`, { method: "POST" }), "Запущено.");
  else if (act_ === "toggle") act(() => api(`/services/${id}`, { method: "PUT", body: { enabled: btn.dataset.enabled !== "1" } }));
  else if (act_ === "edit") api("/services").then((list) => { const s = list.find((x) => x.id === id); if (s) fillForm(s); }).catch((err) => say(err.message, true));
  else if (act_ === "delete" && confirm(`Удалить службу «${btn.dataset.name}»?`)) act(() => api(`/services/${id}`, { method: "DELETE" }), "Удалено.");
});
$("btnSave").addEventListener("click", save);
$("btnCancel").addEventListener("click", resetForm);
(async () => {
  try { await loadPersonas(); } catch (e) { say("Не удалось загрузить персоны: " + e.message, true); }
  await refresh();
  setInterval(refresh, 15000);
})();
