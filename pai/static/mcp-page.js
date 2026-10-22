(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const state = { servers: [], personas: [], editing: null };

  // ---------- helpers ----------
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, init);
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const detail = data && data.detail;
      const msg = typeof detail === "string" ? detail
        : Array.isArray(detail) ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
        : `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  const setNotice = (el, text, bad) => {
    el.textContent = text || "";
    el.classList.toggle("bad", !!bad);
    el.classList.toggle("good", !!text && !bad);
  };

  const parseKV = (text, sep) => {
    const out = {};
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (!line) continue;
      const i = line.indexOf(sep);
      if (i <= 0) throw new Error(`Строка «${line}»: ожидается ${sep === "=" ? "KEY=VALUE" : "Name: value"}`);
      out[line.slice(0, i).trim()] = line.slice(i + sep.length).trim();
    }
    return out;
  };
  const kvToText = (obj, sep) => Object.entries(obj || {})
    .map(([k, v]) => `${k}${sep}${v}`).join("\n");

  // ---------- servers ----------
  function renderServers() {
    const box = $("#servers");
    $("#count").textContent = state.servers.length ? `${state.servers.length} шт.` : "";
    if (!state.servers.length) {
      box.innerHTML = '<p class="muted">Серверов пока нет. Добавьте первый ниже.</p>';
      return;
    }
    box.innerHTML = state.servers.map((s) => {
      const rt = s.runtime || {};
      const status = rt.status || "stopped";
      const target = s.transport === "stdio"
        ? `<code>${esc([s.command, ...(s.args || [])].join(" "))}</code>`
        : `<code>${esc(s.url)}</code>`;
      const tools = (rt.tools || []);
      return `
        <div class="server" data-id="${esc(s.id)}">
          <div class="row" style="justify-content: space-between">
            <strong>${esc(s.name)}</strong>
            <span class="badge s-${esc(status)}">${esc(statusLabel(status))}</span>
          </div>
          <div class="muted">${esc(s.transport)} · ${target} · таймаут ${esc(s.timeout_seconds)} с</div>
          ${rt.error ? `<div class="error">${esc(rt.error)}</div>` : ""}
          <div class="row">
            <button type="button" data-act="reload">Переподключить</button>
            <button type="button" data-act="toggle">${s.enabled ? "Выключить" : "Включить"}</button>
            <button type="button" data-act="tools" ${tools.length ? "" : "disabled"}>Инструменты (${tools.length})</button>
            <button type="button" data-act="edit">Изменить</button>
            <button type="button" data-act="delete" class="danger">Удалить</button>
          </div>
          <div class="tools hidden" data-tools></div>
        </div>`;
    }).join("");
  }

  const statusLabel = (s) => ({
    ready: "подключён", error: "ошибка", connecting: "подключение…",
    stopped: "остановлен", disabled: "выключен" }[s] || s);

  async function loadServers() {
    state.servers = await api("/mcp/servers");
    renderServers();
    renderGrants();
  }

  async function onServersClick(e) {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const card = btn.closest(".server");
    const s = state.servers.find((x) => x.id === card.dataset.id);
    if (!s) return;
    const act = btn.dataset.act;
    try {
      if (act === "reload") {
        btn.disabled = true; btn.textContent = "…";
        await api(`/mcp/servers/${s.id}/reload`, { method: "POST" });
      } else if (act === "toggle") {
        await api(`/mcp/servers/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } });
      } else if (act === "tools") {
        const box = card.querySelector("[data-tools]");
        if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
        const list = await api(`/mcp/servers/${s.id}/tools`);
        box.innerHTML = list.map((t) => `
          <div class="tool"><code>${esc(t.name)}</code>
          <div class="muted">${esc(t.description)}</div></div>`).join("") || '<span class="muted">Нет инструментов</span>';
        box.classList.remove("hidden");
        return;
      } else if (act === "edit") {
        startEdit(s);
        return;
      } else if (act === "delete") {
        if (!confirm(`Удалить сервер «${s.name}»? Выдачи доступа персонам останутся, но инструменты перестанут работать.`)) return;
        await api(`/mcp/servers/${s.id}`, { method: "DELETE" });
      }
      await loadServers();
    } catch (err) {
      alert(err.message);
      await loadServers();
    }
  }

  // ---------- form ----------
  const form = $("#form");
  const transportSel = form.elements.transport;

  function syncTransport() {
    const http = transportSel.value === "http";
    document.querySelectorAll(".http-only").forEach((el) => el.classList.toggle("hidden", !http));
    document.querySelectorAll(".stdio-only").forEach((el) => el.classList.toggle("hidden", http));
  }
  transportSel.addEventListener("change", syncTransport);

  function startEdit(s) {
    state.editing = s.id;
    const f = form.elements;
    f.name.value = s.name; f.name.readOnly = true;
    f.transport.value = s.transport; f.transport.disabled = true;
    f.timeout_seconds.value = s.timeout_seconds;
    f.command.value = s.command || "";
    f.args.value = (s.args || []).join("\n");
    f.env.value = kvToText(s.env, "=");
    f.url.value = s.url || "";
    f.headers.value = kvToText(s.headers, ": ");
    f.enabled.checked = !!s.enabled;
    syncTransport();
    $("#form-title").textContent = `Изменить «${s.name}»`;
    $("#submit").textContent = "Сохранить и переподключить";
    $("#cancel-edit").classList.remove("hidden");
    setNotice($("#form-notice"), "");
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function resetForm() {
    state.editing = null;
    form.reset();
    form.elements.name.readOnly = false;
    form.elements.transport.disabled = false;
    form.elements.timeout_seconds.value = 30;
    form.elements.enabled.checked = true;
    syncTransport();
    $("#form-title").textContent = "Добавить сервер";
    $("#submit").textContent = "Добавить и подключить";
    $("#cancel-edit").classList.add("hidden");
  }

  $("#cancel-edit").addEventListener("click", () => { resetForm(); setNotice($("#form-notice"), ""); });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = form.elements;
    const notice = $("#form-notice");
    const submit = $("#submit");
    let body;
    try {
      const lines = (t) => t.split("\n").map((x) => x.trim()).filter(Boolean);
      const http = f.transport.value === "http";
      body = {
        name: f.name.value.trim(),
        transport: f.transport.disabled ? undefined : f.transport.value,
        timeout_seconds: Number(f.timeout_seconds.value) || 30,
        enabled: f.enabled.checked,
      };
      if (http) {
        body.url = f.url.value.trim();
        body.headers = parseKV(f.headers.value, ":");
        body.command = ""; body.args = []; body.env = {};
      } else {
        body.command = f.command.value.trim();
        body.args = lines(f.args.value);
        body.env = parseKV(f.env.value, "=");
        body.url = ""; body.headers = {};
      }
    } catch (err) {
      setNotice(notice, err.message, true);
      return;
    }

    submit.disabled = true;
    setNotice(notice, "Подключение…");
    try {
      if (state.editing) {
        const { name, transport, ...changes } = body;
        await api(`/mcp/servers/${state.editing}`, { method: "PATCH", body: changes });
      } else {
        await api("/mcp/servers", { method: "POST", body });
      }
      await loadServers();
      const saved = state.servers.find((s) => s.name === body.name);
      const st = saved && saved.runtime ? saved.runtime : {};
      setNotice(notice, st.status === "error" ? `Сохранено, но не подключено: ${st.error}` : "Готово.", st.status === "error");
      resetForm();
    } catch (err) {
      setNotice(notice, err.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  // ---------- catalog ----------
  const catState = { entries: [] };

  async function loadCatalog() {
    try {
      const data = await api("/mcp/catalog");
      catState.entries = data.entries || [];
      $("#cat-errors").textContent = (data.errors || []).length
        ? "Не удалось прочитать часть источников: " + data.errors.join("; ") : "";
    } catch (err) {
      catState.entries = [];
      $("#cat-errors").textContent = "Каталог недоступен: " + err.message;
    }
    renderCatalog();
  }

  function renderCatalog() {
    const q = $("#cat-search").value.trim().toLowerCase();
    const src = $("#cat-source").value;
    const list = catState.entries.filter((e) =>
      (!src || e.source === src) &&
      (!q || [e.title, e.description, e.author, e.id].some((x) => (x || "").toLowerCase().includes(q))));
    const grid = $("#cat-grid");
    if (!list.length) {
      grid.innerHTML = '<p class="muted">Ничего не найдено.</p>';
      return;
    }
    grid.innerHTML = list.map((e) => {
      const official = e.source === "official";
      const inst = e.install;
      const cmd = !inst ? "Команда не указана: откройте репозиторий и заполните вручную"
        : inst.transport === "http" ? `HTTP: ${inst.url}`
        : [inst.command, ...(inst.args || [])].join(" ");
      return `
        <div class="cat-card">
          <div class="row" style="justify-content: space-between">
            <h3>${esc(e.title)}</h3>
            <span class="tag ${official ? "official" : "community"}">${official ? "официальный" : "сообщество · не проверено"}</span>
          </div>
          <p class="cat-desc">${esc(e.description)}</p>
          <div class="muted">${e.author ? "автор: " + esc(e.author) + " · " : ""}<a href="${esc(e.repo)}" target="_blank" rel="noopener noreferrer">репозиторий</a></div>
          <div class="cat-install code">${esc(cmd)}</div>
          ${inst && inst.note ? `<div class="muted">${esc(inst.note)}</div>` : ""}
          <div><button type="button" class="primary" data-cat-id="${esc(e.id)}">Добавить</button></div>
        </div>`;
    }).join("");
  }

  $("#cat-search").addEventListener("input", renderCatalog);
  $("#cat-source").addEventListener("change", renderCatalog);

  $("#cat-grid").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-cat-id]");
    if (!btn) return;
    const e = catState.entries.find((x) => x.id === btn.dataset.catId);
    if (e) prefillFromCatalog(e);
  });

  function prefillFromCatalog(e) {
    if (state.editing) resetForm();
    const f = form.elements;
    const inst = e.install;
    f.name.value = e.id;
    f.timeout_seconds.value = 30;
    f.enabled.checked = true;
    if (inst && inst.transport === "http") {
      f.transport.value = "http";
      f.url.value = inst.url;
      f.headers.value = "";
    } else {
      f.transport.value = "stdio";
      f.command.value = inst ? inst.command : "";
      f.args.value = inst ? (inst.args || []).join("\n") : "";
      f.env.value = (e.env_keys || []).map((k) => `${k}=`).join("\n");
    }
    syncTransport();
    const hint = inst && inst.note ? inst.note + " " : "";
    setNotice($("#form-notice"), hint + "Проверьте команду и нажмите «Добавить и подключить».", false);
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- persona grants ----------
  async function loadPersonas() {
    state.personas = await api("/personas");
    const sel = $("#persona");
    const prev = sel.value;
    sel.innerHTML = state.personas.map((p) =>
      `<option value="${esc(p.persona_id)}">${esc(p.name)} (${esc(p.persona_id)})</option>`).join("");
    if (prev && state.personas.some((p) => p.persona_id === prev)) sel.value = prev;
    renderGrants();
  }

  function mcpToolNames() {
    return state.servers.flatMap((s) => (s.runtime && s.runtime.tools) || []);
  }

  function renderGrants() {
    const box = $("#grants");
    const names = mcpToolNames();
    const persona = state.personas.find((p) => p.persona_id === $("#persona").value);
    if (!names.length) {
      box.innerHTML = '<p class="muted">Нет подключённых инструментов. Подключите сервер выше.</p>';
      return;
    }
    const granted = new Set((persona && persona.tool_permissions) || []);
    box.innerHTML = names.map((n) => `
      <label class="check"><input type="checkbox" value="${esc(n)}" ${granted.has(n) ? "checked" : ""}>
      <code>${esc(n)}</code></label>`).join("");
  }

  $("#persona").addEventListener("change", () => { renderGrants(); setNotice($("#grant-notice"), ""); });

  $("#persona-save").addEventListener("click", async () => {
    const id = $("#persona").value;
    const persona = state.personas.find((p) => p.persona_id === id);
    if (!persona) return;
    const notice = $("#grant-notice");
    const known = new Set(mcpToolNames());
    const picked = [...document.querySelectorAll("#grants input[type=checkbox]")]
      .filter((c) => c.checked).map((c) => c.value);
    // keep every non-MCP permission and any MCP grant of a server that is currently offline
    const kept = persona.tool_permissions.filter((t) => !known.has(t));
    const next = [...kept, ...picked];
    try {
      const updated = await api(`/personas/${encodeURIComponent(id)}/tools`, {
        method: "PATCH", body: { tool_permissions: next } });
      persona.tool_permissions = updated.tool_permissions;
      setNotice(notice, "Сохранено.");
    } catch (err) {
      setNotice(notice, err.message, true);
    }
  });

  // ---------- boot ----------
  $("#refresh").addEventListener("click", () => { refreshAll(true); loadCatalog(); });
  $("#servers").addEventListener("click", onServersClick);

  async function refreshAll(manual) {
    try {
      await loadServers();
      await loadPersonas();
    } catch (err) {
      if (manual) alert(err.message);
    }
  }

  syncTransport();
  refreshAll(false);
  loadCatalog();
  // periodic status refresh; skipped while a tool list is open or a form is being edited
  setInterval(() => {
    if (document.hidden || state.editing || document.querySelector("[data-tools]:not(.hidden)")) return;
    loadServers().catch(() => {});
  }, 10000);
})();
