// Owner's inbox page. Uses the same token as the app (localStorage "pai.token").
(() => {
  const $ = (id) => document.getElementById(id);
  const readLS = (k) => { try { return localStorage.getItem(k) || ""; } catch (_) { return ""; } };
  const base = () => readLS("pai.apiBase").replace(/\/$/, "");

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {}, { Authorization: "Bearer " + readLS("pai.token") });
    const res = await fetch(base() + path, Object.assign({}, opts, { headers }));
    if (res.status === 401) throw new Error("Войдите в приложении (на главной странице), затем вернитесь сюда");
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j.detail) detail = j.detail; } catch (_) {}
      throw new Error(detail);
    }
    return res.json();
  }

  function card(m) {
    const box = document.createElement("div");
    box.className = "inbox-card" + (m.read ? "" : " unread");
    const head = document.createElement("div");
    head.className = "inbox-head";
    head.textContent = `${m.name || "Аноним"} · ${new Date(m.created_at * 1000).toLocaleString("ru-RU")}`;
    const body = document.createElement("div");
    body.className = "inbox-text";
    body.textContent = m.text;  // textContent: visitor text is never parsed as HTML
    const row = document.createElement("div");
    row.className = "inbox-actions";
    const toggle = document.createElement("button");
    toggle.className = "btn ghost sm";
    toggle.type = "button";
    toggle.textContent = m.read ? "Отметить новым" : "Прочитано";
    toggle.addEventListener("click", () => act(() => api(`/inbox/${m.id}/read?read=${!m.read}`, { method: "POST" })));
    const del = document.createElement("button");
    del.className = "btn ghost sm danger";
    del.type = "button";
    del.textContent = "Удалить";
    del.addEventListener("click", () => {
      if (confirm("Удалить сообщение?")) act(() => api(`/inbox/${m.id}`, { method: "DELETE" }));
    });
    row.append(toggle, del);
    box.append(head, body, row);
    return box;
  }

  async function act(fn) {
    try { await fn(); await load(); } catch (e) { $("inboxMsg").textContent = "⚠ " + e.message; }
  }

  async function load() {
    const list = $("inboxList");
    $("inboxMsg").textContent = "";
    try {
      const items = await api(`/inbox?unread=${$("onlyUnread").checked}`);
      list.textContent = "";
      if (!items.length) { $("inboxMsg").textContent = "Пока нет сообщений."; return; }
      items.forEach((m) => list.appendChild(card(m)));
    } catch (e) {
      $("inboxMsg").textContent = "⚠ " + e.message;
    }
  }

  $("btnRefresh").addEventListener("click", load);
  $("onlyUnread").addEventListener("change", load);
  load();
})();
