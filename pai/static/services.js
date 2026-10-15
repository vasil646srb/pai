// Services view: autonomous prompts a persona runs on an interval.
// Uses app.js helpers (api, esc) and the shared view/card/field styles.
(function () {
  const $ = (id) => document.getElementById(id);
  const fmt = (ts) => (ts ? new Date(ts * 1000).toLocaleString("ru-RU") : "—");
  let timer = null;

  function say(text, isErr = false) {
    const m = $("serviceMsg");
    m.textContent = text;
    m.className = "form-msg" + (isErr ? " err" : "");
  }

  async function loadPersonas() {
    const list = await api("/personas");
    const prev = $("svcPersona").value;
    $("svcPersona").innerHTML = list
      .map((p) => `<option value="${esc(p.persona_id)}">${esc(p.name)}</option>`).join("");
    if (prev) $("svcPersona").value = prev;
  }

  function resetForm() {
    $("svcEditId").value = "";
    $("serviceFormTitle").textContent = "Новая служба";
    $("svcName").value = "";
    $("svcPrompt").value = "";
    $("svcInterval").value = 60;
    $("svcSpace").value = "personal";
    $("svcEnabled").checked = true;
    $("btnSaveService").textContent = "Создать службу";
    $("btnCancelService").hidden = true;
    say("");
  }

  function fillForm(s) {
    $("svcFold").open = true;
    $("svcEditId").value = s.id;
    $("serviceFormTitle").textContent = "Редактирование: " + s.name;
    $("svcName").value = s.name;
    $("svcPersona").value = s.persona_id;
    $("svcPrompt").value = s.prompt;
    $("svcInterval").value = s.interval_minutes;
    $("svcSpace").value = s.space_id;
    $("svcEnabled").checked = !!s.enabled;
    $("btnSaveService").textContent = "Сохранить";
    $("btnCancelService").hidden = false;
    $("view-services").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function save() {
    const body = {
      name: $("svcName").value.trim(),
      persona_id: $("svcPersona").value,
      prompt: $("svcPrompt").value.trim(),
      interval_minutes: Number($("svcInterval").value),
      space_id: $("svcSpace").value.trim() || "personal",
      enabled: $("svcEnabled").checked,
    };
    const id = $("svcEditId").value;
    const btn = $("btnSaveService");
    btn.disabled = true;
    try {
      if (id) await api(`/services/${id}`, { method: "PUT", body });
      else await api("/services", { method: "POST", body });
      resetForm();
      say("Сохранено.");
      await refresh();
    } catch (e) {
      say(e.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function act(fn, okText) {
    try {
      await fn();
      if (okText) say(okText);
      await refresh();
    } catch (e) {
      say(e.message, true);
    }
  }

  function card(s) {
    const status = s.running
      ? '<span class="badge-status pinned">выполняется</span>'
      : s.enabled
        ? '<span class="badge-status active">включена</span>'
        : '<span class="badge-status draft">выключена</span>';
    const runs = (s.runs || []).slice().reverse().map((r) => r.ok
      ? `<div class="svc-run"><div class="svc-run-head">${fmt(r.finished_at)} · ${r.duration_seconds} с</div><div class="svc-run-text">${esc(r.reply)}</div></div>`
      : `<div class="svc-run err"><div class="svc-run-head">${fmt(r.finished_at)} · ошибка</div><div class="svc-run-text">${esc(r.error)}</div></div>`
    ).join("");
    return `
      <details class="fold-item persona-card" data-id="${esc(s.id)}">
        <summary><div class="persona-card-top">
          <div>
            <div class="persona-name">${esc(s.name)}</div>
            <div class="persona-id">${esc(s.persona_id)} · каждые ${s.interval_minutes} мин · пространство ${esc(s.space_id)}</div>
          </div>
          ${status}
        </div></summary>
        <div class="svc-prompt">${esc(s.prompt)}</div>
        <div class="meta-line">последний запуск: ${fmt(s.last_run_at)} · следующий: ${s.enabled ? fmt(s.next_run_at) : "—"}</div>
        <div class="card-actions">
          <button class="btn sm" data-act="run" ${s.running ? "disabled" : ""}>Запустить сейчас</button>
          <button class="btn sm ghost" data-act="toggle" data-enabled="${s.enabled ? "1" : "0"}">${s.enabled ? "Выключить" : "Включить"}</button>
          <button class="btn sm ghost" data-act="edit">Изменить</button>
          <button class="btn sm danger" data-act="delete" data-name="${esc(s.name)}">Удалить</button>
        </div>
        ${runs
          ? `<details class="svc-runs"><summary>История (${s.runs.length})</summary>${runs}</details>`
          : '<div class="meta-line">Запусков пока не было.</div>'}
      </details>`;
  }

  async function refresh() {
    try {
      const list = await api("/services");
      $("serviceList").innerHTML = list.length
        ? list.map(card).join("")
        : '<div class="empty-state">Служб пока нет. Создайте первую ниже.</div>';
    } catch (e) {
      say(e.message, true);
    }
  }

  $("serviceList").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const id = btn.closest(".persona-card").dataset.id;
    const action = btn.dataset.act;
    if (action === "run") {
      act(() => api(`/services/${id}/run`, { method: "POST" }), "Запущено.");
    } else if (action === "toggle") {
      act(() => api(`/services/${id}`, { method: "PUT", body: { enabled: btn.dataset.enabled !== "1" } }));
    } else if (action === "edit") {
      try {
        const s = (await api("/services")).find((x) => x.id === id);
        if (s) fillForm(s);
      } catch (err) {
        say(err.message, true);
      }
    } else if (action === "delete" && confirm(`Удалить службу «${btn.dataset.name}»?`)) {
      act(() => api(`/services/${id}`, { method: "DELETE" }), "Удалено.");
    }
  });

  $("btnSaveService").addEventListener("click", save);
  $("btnCancelService").addEventListener("click", resetForm);

  window.loadServicesView = async function () {
    try {
      await loadPersonas();
    } catch (e) {
      say("Не удалось загрузить персоны: " + e.message, true);
    }
    await refresh();
    clearInterval(timer);
    timer = setInterval(() => {
      if ($("view-services").classList.contains("active")) refresh();
    }, 15000);
  };
})();
