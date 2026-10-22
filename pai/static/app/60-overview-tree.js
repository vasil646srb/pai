/* ===================== OVERVIEW ===================== */

async function loadOverview() {
  const statCards = document.getElementById("statCards");
  const logEl = document.getElementById("retrievalLog");
  statCards.innerHTML = `<div class="card stat-card"><div class="stat-label">загрузка…</div></div>`;
  loadCacheStatus();
  try {
    const stats = await api("/memory/stats");
    const comp = stats.compression || {};
    const ratio = comp.raw_nodes && comp.total_nodes
      ? (comp.total_nodes / comp.raw_nodes).toFixed(2)
      : "—";

    statCards.innerHTML = `
      <div class="card stat-card"><div class="stat-label">всего узлов</div><div class="stat-value cold">${stats.total_nodes ?? 0}</div></div>
      <div class="card stat-card"><div class="stat-label">уровней сжатия</div><div class="stat-value">${(comp.levels || []).length}</div></div>
      <div class="card stat-card"><div class="stat-label">размер WAL</div><div class="stat-value">${fmtBytes(stats.wal_size_bytes)}</div></div>
      <div class="card stat-card"><div class="stat-label">размер БД (индекс)</div><div class="stat-value warm">${fmtBytes(stats.db_size_bytes)}</div></div>
    `;

    renderStackViz(document.getElementById("overviewStack"), stats.by_level || {});
    const legend = document.getElementById("overviewLevelLegend");
    const levels = Object.keys(stats.by_level || {}).map(Number).sort((a, b) => a - b);
    legend.innerHTML = levels.map((l) => `
      <span class="tag lvl ${lvlClass(l)}"><span style="display:inline-block;width:6px;height:6px;border-radius:2px;background:${levelColor(l)};margin-right:5px;"></span>level ${l}: ${stats.by_level[l]}</span>
    `).join("") || `<span class="tag">пусто</span>`;


    refreshHeaderStack();
  } catch (e) {
    statCards.innerHTML = `<div class="empty-state">Ошибка загрузки: ${esc(e.message)}</div>`;
  }

  try {
    const log = await api("/memory/retrieval-log?limit=25");
    if (!log.length) {
      logEl.innerHTML = `<div class="empty-state">Вызовов ещё не было</div>`;
    } else {
      logEl.innerHTML = `<div style="display:flex; flex-direction:column; gap:6px;">` + log.map((r) => {
        let ids = [];
        try { ids = JSON.parse(r.result_node_ids || "[]"); } catch (_) {}
        return `<div style="display:flex; justify-content:space-between; gap:10px; font-size:12px; padding:6px 8px; border:1px solid var(--border-soft); border-radius:4px;">
          <span class="mono" style="color:var(--accent-cold)">${esc(r.tool)}</span>
          <span style="color:var(--text-faint); flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(ids.join(", ") || "—")}</span>
          <span class="mono" style="color:var(--text-faint)">${fmtTs(r.ts)}</span>
        </div>`;
      }).join("") + `</div>`;
    }
  } catch (e) {
    logEl.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

document.getElementById("btnCompress").addEventListener("click", async () => {
  const space_id = document.getElementById("compressSpace").value;
  const force_top = document.getElementById("compressForceTop").checked;
  const resultEl = document.getElementById("compressResult");
  resultEl.textContent = "выполняется…";
  try {
    const res = await api("/memory/compress", { method: "POST", body: { space_id, force_top } });
    const parts = Object.entries(res.by_space || {}).map(([sid, r]) => `${sid}: ${r.error ? "ошибка" : r.count}`);
    resultEl.textContent = `создано узлов: ${res.count}` + (parts.length > 1 ? ` (${parts.join(", ")})` : "");
    const failed = Object.values(res.by_space || {}).filter((r) => r.error).length;
    toast(`Сжатие завершено: +${res.count} узлов` + (failed ? `, ошибок: ${failed}` : ""), failed ? "err" : "ok");
    loadOverview();
  } catch (e) {
    resultEl.textContent = "";
    toast("Ошибка сжатия: " + e.message, "err");
  }
});

/* ===================== TREE / NODE INSPECTOR ===================== */

async function loadTreeRoots() {
  const space_id = document.getElementById("treeSpace").value;
  const root = document.getElementById("treeRoot");
  root.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    const nodes = await api(`/memory/tree/roots?space_id=${encodeURIComponent(space_id)}`);
    if (!nodes.length) {
      root.innerHTML = `<div class="empty-state">Пусто в этом пространстве</div>`;
      return;
    }
    root.innerHTML = "";
    nodes.forEach((n) => root.appendChild(renderTreeNode(n)));
  } catch (e) {
    root.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

function renderTreeNode(node) {
  const wrap = document.createElement("div");
  wrap.className = "tree-node";

  const row = document.createElement("div");
  row.className = "tree-node-row";
  row.dataset.nodeId = node.id;

  const hasChildren = node.child_ids && node.child_ids.length > 0;
  row.innerHTML = `
    <span class="twist">${hasChildren ? "▸" : "·"}</span>
    <span class="lvl-chip" style="background:${levelColor(node.level)}"></span>
    <span class="summ" title="${esc(node.summary_text || node.raw_content || "")}">${esc((node.summary_text || node.raw_content || "(пусто)").slice(0, 90))}</span>
    <span class="cnt">L${node.level}${hasChildren ? " · " + node.child_ids.length : ""}</span>
  `;

  let childrenBox = null;
  let expanded = false;

  row.addEventListener("click", async (ev) => {
    document.querySelectorAll(".tree-node-row.selected").forEach((el) => el.classList.remove("selected"));
    row.classList.add("selected");
    inspectNode(node.id);

    if (!hasChildren) return;
    expanded = !expanded;
    const twist = row.querySelector(".twist");
    if (expanded) {
      twist.textContent = "▾";
      if (!childrenBox) {
        childrenBox = document.createElement("div");
        childrenBox.className = "tree-children";
        childrenBox.innerHTML = `<div class="empty-state" style="padding:8px;">загрузка…</div>`;
        wrap.appendChild(childrenBox);
        try {
          const children = await api(`/memory/tree/${encodeURIComponent(node.id)}/children`);
          childrenBox.innerHTML = "";
          children
            .sort((a, b) => (b.occurred_at || 0) - (a.occurred_at || 0))
            .forEach((c) => childrenBox.appendChild(renderTreeNode(c)));
          if (!children.length) childrenBox.innerHTML = `<div class="empty-state" style="padding:8px;">нет дочерних узлов</div>`;
        } catch (e) {
          childrenBox.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
        }
      } else {
        childrenBox.style.display = "";
      }
    } else if (childrenBox) {
      childrenBox.style.display = "none";
      twist.textContent = "▸";
    }
  });

  wrap.appendChild(row);
  return wrap;
}

document.getElementById("btnReloadTree").addEventListener("click", loadTreeRoots);
document.getElementById("treeSpace").addEventListener("change", loadTreeRoots);

async function inspectNode(nodeId) {
  const el = document.getElementById("inspector");
  el.innerHTML = `<div class="empty-state">Загрузка узла ${esc(nodeId)}…</div>`;
  try {
    const n = await api(`/memory/node/${encodeURIComponent(nodeId)}`);
    renderInspector(n);
  } catch (e) {
    el.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

function renderInspector(n) {
  const el = document.getElementById("inspector");
  const statuses = ["active", "pinned", "excluded", "archived", "hidden"];

  el.innerHTML = `
    <div class="inspector-header">
      <div>
        <div class="inspector-id">${esc(n.id)} <span class="tag lvl ${lvlClass(n.level)}" style="margin-left:8px;">level ${n.level}</span> <span class="badge-status ${esc(n.status)}" style="margin-left:6px;">${esc(n.status)}</span></div>
      </div>
      <div class="inspector-actions">
        <button class="btn sm ghost" id="btnCopyId">Copy ID</button>
        <button class="btn sm ghost" id="btnCopyCall">Copy get_fragment()</button>
      </div>
    </div>

    <div class="kv-grid">
      <span class="k">space_id</span><span class="v">${esc(n.space_id)}</span>
      <span class="k">type</span><span class="v">${esc(n.type)}</span>
      <span class="k">parent_id</span><span class="v">${n.parent_id ? esc(n.parent_id) : "—"}</span>
      <span class="k">child_ids</span><span class="v">${(n.child_ids || []).length ? esc(n.child_ids.join(", ")) : "—"}</span>
      <span class="k">occurred_at</span><span class="v">${fmtTs(n.occurred_at)}</span>
      <span class="k">timeframe</span><span class="v">${n.timeframe_start ? fmtTs(n.timeframe_start) + " → " + fmtTs(n.timeframe_end) : "—"}</span>
      <span class="k">tags</span><span class="v">${(n.tags || []).length ? esc(n.tags.join(", ")) : "—"}</span>
      <span class="k">retrieval_count</span><span class="v">${n.retrieval_count}</span>
      <span class="k">position_in_history</span><span class="v">${n.position_in_history}</span>
    </div>

    <div class="content-block-label">summary_text</div>
    <div class="content-block">${esc(n.summary_text || "—")}</div>

    ${n.raw_content ? `<div class="content-block-label">raw_content (level 0, точный текст)</div><div class="content-block">${esc(n.raw_content)}</div>` : ""}

    <div class="content-block-label">Сменить статус</div>
    <div style="display:flex; gap:6px; flex-wrap:wrap;">
      ${statuses.map((s) => `<button class="btn sm ${s === n.status ? "primary" : "ghost"}" data-status="${s}">${s}</button>`).join("")}
    </div>
  `;

  el.querySelector("#btnCopyId").addEventListener("click", () => {
    navigator.clipboard.writeText(n.id);
    toast("ID скопирован");
  });
  el.querySelector("#btnCopyCall").addEventListener("click", () => {
    navigator.clipboard.writeText(JSON.stringify({ name: "get_fragment", arguments: { id: n.id } }));
    toast("Вызов инструмента скопирован");
  });
  el.querySelectorAll("[data-status]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const status = btn.dataset.status;
      if (status === n.status) return;
      try {
        const updated = await api(`/memory/node/${encodeURIComponent(n.id)}/status`, { method: "PATCH", body: { status } });
        toast(`Статус изменён на "${status}"`, "ok");
        renderInspector(updated);
      } catch (e) {
        toast("Ошибка: " + e.message, "err");
      }
    });
  });
}

document.getElementById("btnSearch").addEventListener("click", runSearch);
document.getElementById("searchQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });

async function runSearch() {
  const query = document.getElementById("searchQuery").value.trim();
  const space_id = document.getElementById("searchSpace").value || null;
  const box = document.getElementById("searchResults");
  if (!query) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="empty-state">Поиск…</div>`;
  try {
    const res = await api("/memory/search", { method: "POST", body: { query, space_id, limit: 15 } });
    if (!res.results.length) {
      box.innerHTML = `<div class="empty-state">Ничего не найдено (это вспомогательный поиск — не замена get_fragment)</div>`;
      return;
    }
    box.innerHTML = res.results.map((r) => `
      <div class="search-result" data-id="${esc(r.id)}">
        <div style="display:flex; justify-content:space-between;">
          <span class="sid">${esc(r.id)}</span>
          <span class="tag lvl ${lvlClass(r.level)}">L${r.level} · ${esc(r.space_id)}</span>
        </div>
        <div class="snip">${esc(r.snippet || "").replace(/\[/g, "<mark>").replace(/\]/g, "</mark>")}</div>
      </div>
    `).join("");
    box.querySelectorAll(".search-result").forEach((el) => {
      el.addEventListener("click", () => inspectNode(el.dataset.id));
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

