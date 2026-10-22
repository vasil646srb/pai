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

/* ===================== folds: remember open/closed state per viewer (localStorage, best effort) ===================== */
function foldsInit() {
  document.querySelectorAll("details.fold[data-fold]").forEach((d) => {
    const key = "pai.fold." + d.dataset.fold;
    try { const v = localStorage.getItem(key); if (v === "1" || v === "0") d.open = v === "1"; } catch (_) { /* storage blocked */ }
    d.addEventListener("toggle", () => { try { localStorage.setItem(key, d.open ? "1" : "0"); } catch (_) { /* ignore */ } });
  });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", foldsInit);
else foldsInit();


/* ===================== SPACES ===================== */

const EMB_LABEL = { ready: "готово", partial: "частично", none: "не индексировано", empty: "пусто", off: "выключено" };
const EMB_CLASS = { ready: "ok", partial: "warn", none: "bad", empty: "", off: "" };
const ITEM_STATE_LABEL = { indexed: "в индексе", pending: "ждёт индексации", stale: "старая модель", missing: "не в индексе" };
const KIND_LABEL = { node: "узел памяти", file: "файл", inbox: "входящее", request: "запрос" };

function fmtSpaceDate(ts) {
  if (!ts) return "";
  return new Date(Number(ts) * 1000).toLocaleString();
}

function renderEmbedBar(el, sem) {
  if (!sem) { el.innerHTML = ""; return; }
  const parts = [
    `модель: <b>${esc(sem.model || "—")}</b>`,
    `индекс ${sem.enabled ? "включён" : "выключен"}`,
    `в очереди: ${sem.pending || 0}`,
  ];
  if (sem.backfill_pending) parts.push("идёт пересчёт");
  if (sem.other_model_chunks) parts.push(`чанков другой модели: ${sem.other_model_chunks}`);
  const err = sem.last_error ? `<div class="emb-error">Ошибка эмбеддингов: ${esc(sem.last_error)}</div>` : "";
  el.innerHTML = `<div class="card embed-summary">${parts.join(" · ")}${err}</div>`;
}

function renderSpaceCard(s) {
  const e = s.embedding;
  const pct = e.sources ? Math.round((e.indexed * 100) / e.sources) : 0;
  const embBlock = e.status === "off" ? "" : `
        <div class="emb-bar"><div style="width:${pct}%"></div></div>
        <div class="page-sub tight">Эмбеддинги: ${e.indexed} из ${e.sources} · ожидают: ${e.pending} · старая модель: ${e.stale} · не индексированы: ${e.missing}</div>`;
  const persons = s.personas.length ? s.personas.map(esc).join(", ") : "—";
  return `
    <div class="card space-card" data-space="${esc(s.space_id)}">
      <div class="space-head">
        <span class="stat-label">${esc(s.space_id)}</span>
        <span class="emb-badge ${EMB_CLASS[e.status] || ""}">${EMB_LABEL[e.status] || e.status}</span>
      </div>
      <div class="space-counts">
        <div><span>${s.nodes}</span> узлов</div>
        <div><span>${s.summaries}</span> сводок</div>
        <div><span>${s.files}</span> файлов</div>
        <div><span>${s.inbox}</span> входящих</div>
        <div><span>${s.requests}</span> запросов</div>
      </div>${embBlock}
      <div class="page-sub tight">персоны: ${persons}</div>
      <div class="card-actions">
        <button class="btn sm" data-act="items" data-space="${esc(s.space_id)}">Содержимое</button>
        <button class="btn sm" data-act="rename" data-space="${esc(s.space_id)}">Переименовать</button>
        <button class="btn sm danger" data-act="delete" data-space="${esc(s.space_id)}">Удалить</button>
      </div>
      <div class="space-items" hidden></div>
    </div>`;
}

async function loadSpaces() {
  const list = document.getElementById("spacesList");
  const bar = document.getElementById("spacesEmbedBar");
  list.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    const [spaces, sem] = await Promise.all([
      api("/memory/spaces/overview"),
      api("/semantic/status").catch(() => null),
    ]);
    renderEmbedBar(bar, sem);
    if (!spaces.length) {
      list.innerHTML = `<div class="empty-state">Пространств пока нет</div>`;
      return;
    }
    list.innerHTML = `<div class="grid grid-3">` + spaces.map(renderSpaceCard).join("") + `</div>`;
  } catch (e) {
    list.innerHTML = `<div class="empty-state">Ошибка загрузки: ${esc(e.message)}</div>`;
  }
}

async function toggleSpaceItems(card, space) {
  const box = card.querySelector(".space-items");
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<div class="page-sub tight">Загрузка…</div>`;
  try {
    const r = await api(`/memory/spaces/${encodeURIComponent(space)}/items?limit=100`);
    const st = r.states || {};
    const head = `<div class="page-sub tight">Всего: ${r.total} · в индексе: ${st.indexed || 0} · ждут: ${st.pending || 0} · старая модель: ${st.stale || 0} · не в индексе: ${st.missing || 0}${r.total > r.items.length ? ` · показаны первые ${r.items.length}` : ""}</div>`;
    const rows = r.items.map((it) => `
      <div class="space-item">
        <div class="space-item-meta">
          <span class="tag">${esc(KIND_LABEL[it.kind] || it.kind)}</span>
          <span class="emb-state ${esc(it.state)}">${ITEM_STATE_LABEL[it.state] || it.state}</span>
          ${it.chunks ? `<span class="page-sub tight">чанков: ${it.chunks}</span>` : ""}
          <span class="page-sub tight">${esc(fmtSpaceDate(it.occurred_at))}</span>
        </div>
        <div class="space-item-text">${esc(it.preview) || "<i>пусто</i>"}</div>
      </div>`).join("");
    box.innerHTML = head + (rows || `<div class="empty-state">Пространство пустое</div>`);
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

document.getElementById("spacesList").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const from = btn.dataset.space;
  const enc = encodeURIComponent(from);
  try {
    if (btn.dataset.act === "items") {
      await toggleSpaceItems(btn.closest(".space-card"), from);
      return;
    }
    if (btn.dataset.act === "rename") {
      const to = (prompt(`Новое имя для пространства «${from}»:`, from) || "").trim();
      if (!to || to === from) return;
      const r = await api(`/memory/spaces/${enc}`, { method: "PATCH", body: { new_name: to } });
      toast(`Перенесено узлов: ${r.nodes_moved}`, "ok");
    } else {
      const typed = (prompt(`Все узлы пространства «${from}» будут стёрты без восстановления.\nЧтобы подтвердить, введите его имя:`) || "").trim();
      if (typed !== from) {
        if (typed) toast("Имя не совпало, удаление отменено", "err");
        return;
      }
      const r = await api(`/memory/spaces/${enc}?confirm=${enc}`, { method: "DELETE" });
      toast(`Удалено узлов: ${r.nodes_deleted}`, "ok");
    }
    loadSpaces();
    loadSpaceSelects();
  } catch (e) {
    toast("Ошибка: " + e.message, "err");
  }
});

/* ===================== sidebar groups (collapsible) ===================== */
// Each "nav-section-label" becomes a header of a group; the items under it are wrapped in .nav-group.
// Collapsed groups are remembered per browser. The active view stays visible even in a collapsed group.
(function initSidebarGroups() {
  const LS_KEY_COLLAPSED = "pai.sidebarCollapsed";
  const sidebar = document.getElementById("sidebar");
  if (!sidebar) return;

  let collapsed = [];
  try { collapsed = JSON.parse(localStorage.getItem(LS_KEY_COLLAPSED) || "[]"); } catch (_) { collapsed = []; }
  const save = () => { try { localStorage.setItem(LS_KEY_COLLAPSED, JSON.stringify(collapsed)); } catch (_) {} };

  sidebar.querySelectorAll(".nav-section-label").forEach((label) => {
    const key = label.textContent.trim();
    const items = [];
    let el = label.nextElementSibling;
    while (el && !el.classList.contains("nav-section-label")) { items.push(el); el = el.nextElementSibling; }

    const group = document.createElement("div");
    group.className = "nav-group";
    group.dataset.group = key;
    sidebar.insertBefore(group, label);
    group.appendChild(label);
    items.forEach((i) => group.appendChild(i));

    label.classList.add("nav-group-head");
    label.setAttribute("role", "button");
    label.setAttribute("tabindex", "0");
    const caret = document.createElement("span");
    caret.className = "caret";
    caret.textContent = "▾";
    label.textContent = "";
    label.append(caret, document.createTextNode(key));

    const apply = () => {
      const isCollapsed = collapsed.includes(key);
      group.classList.toggle("collapsed", isCollapsed);
      label.setAttribute("aria-expanded", String(!isCollapsed));
    };
    const toggle = () => {
      collapsed = collapsed.includes(key) ? collapsed.filter((k) => k !== key) : [...collapsed, key];
      save();
      apply();
    };
    label.addEventListener("click", toggle);
    label.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    apply();
  });
})();
