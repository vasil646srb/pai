// Owner request backlog panel on the chat page. Uses api() from app.js (owner token).
(() => {
  const $ = (id) => document.getElementById(id);
  const panel = $("reqPanel");
  if (!panel) return;

  function setMsg(text) { $("reqMsg").textContent = text; }

  function row(r) {
    const item = document.createElement("div");
    item.className = "req-item" + (r.done ? " done" : "");
    const text = document.createElement("div");
    text.className = "req-text";
    text.textContent = r.text;  // textContent: never parsed as HTML
    const meta = document.createElement("div");
    meta.className = "req-meta";
    meta.textContent = new Date(r.created_at * 1000).toLocaleString("ru-RU");
    const actions = document.createElement("div");
    actions.className = "req-actions";
    const toggle = document.createElement("button");
    toggle.className = "btn ghost sm";
    toggle.type = "button";
    toggle.textContent = r.done ? "Вернуть" : "Готово";
    toggle.addEventListener("click", () => act(() => api(`/requests/${r.id}`, { method: "PATCH", body: { done: !r.done } })));
    const del = document.createElement("button");
    del.className = "btn ghost sm danger";
    del.type = "button";
    del.textContent = "Удалить";
    del.addEventListener("click", () => {
      if (confirm("Удалить запрос?")) act(() => api(`/requests/${r.id}`, { method: "DELETE" }));
    });
    actions.append(toggle, del);
    item.append(text, meta, actions);
    return item;
  }

  async function load() {
    const list = $("reqList");
    try {
      const items = await api(`/requests?include_done=${$("reqShowDone").checked}`);
      list.textContent = "";
      if (!items.length) setMsg("Запросов пока нет.");
      else setMsg("");
      items.forEach((r) => list.appendChild(row(r)));
    } catch (e) {
      setMsg("⚠ " + e.message);
    }
  }

  async function act(fn) {
    try { await fn(); await load(); } catch (e) { setMsg("⚠ " + e.message); }
  }

  $("btnRequest").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) { $("reqText").focus(); load(); }
  });
  $("reqClose").addEventListener("click", () => { panel.hidden = true; });
  $("reqShowDone").addEventListener("change", load);
  $("reqSave").addEventListener("click", () => {
    const text = $("reqText").value.trim();
    if (!text) { setMsg("Напишите текст запроса"); return; }
    act(async () => {
      await api("/requests", { method: "POST", body: { text } });
      $("reqText").value = "";
    });
  });
  $("reqText").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("reqSave").click(); }
  });
})();
