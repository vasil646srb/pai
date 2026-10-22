// Раздел «Данные»: обзор всей базы и просмотр записей (только чтение). Опирается на api() из app.js.
// Все строки из базы выводятся через textContent.
(function () {
  const $ = (id) => document.getElementById(id);
  const KIND_LABEL = { node: "память", file: "файлы", inbox: "входящие", request: "запросы" };
  const KINDS = ["node", "file", "inbox", "request"];
  const PAGE = 50;
  let offset = 0;
  let overview = null;

  const fmt = (ts) => (ts ? new Date(ts * 1000).toLocaleString("ru-RU") : "");

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function setOptions(sel, values, allLabel) {
    const prev = sel.value;
    sel.innerHTML = "";
    sel.appendChild(new Option(allLabel, ""));
    for (const v of values) sel.appendChild(new Option(v, v));
    if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }

  function renderOverview() {
    if (!overview) return;
    const curKind = $("dataKind").value;
    const kinds = $("dataKinds");
    kinds.innerHTML = "";
    for (const k of KINDS) {
      const sub = overview.index_model
        ? `в индексе: ${overview.indexed[k] ?? 0}`
        : "индекс не настроен";
      const card = el("div", "data-kind" + (curKind === k ? " active" : ""));
      card.append(el("div", "n", String(overview.kinds[k] ?? 0)), el("div", "", KIND_LABEL[k]), el("div", "sub", sub));
      card.addEventListener("click", () => { $("dataKind").value = k; offset = 0; renderOverview(); loadItems(); });
      kinds.appendChild(card);
    }

    const curSpace = $("dataSpace").value;
    const spaces = $("dataSpaces");
    spaces.innerHTML = "";
    for (const s of overview.spaces) {
      const card = el("div", "data-kind" + (curSpace === s.space_id ? " active" : ""));
      card.append(
        el("div", "n", String(s.nodes + s.files)),
        el("div", "", s.space_id),
        el("div", "sub", `память ${s.nodes} · файлы ${s.files}`),
      );
      card.addEventListener("click", () => { $("dataSpace").value = s.space_id; offset = 0; renderOverview(); loadItems(); });
      spaces.appendChild(card);
    }
    setOptions($("dataSpace"), overview.spaces.map((s) => s.space_id), "все");
  }

  function itemEl(it) {
    const card = el("div", "data-item");
    const title = el("div", "title", it.title || "(пусто)");
    const meta = el("div", "meta");
    const parts = [KIND_LABEL[it.kind], it.space_id, fmt(it.at)];
    if (it.indexed === true) parts.push("в индексе");
    if (it.indexed === false) parts.push("не в индексе");
    for (const p of parts) {
      const s = el("span", p === "в индексе" ? "ok" : "", p);
      meta.appendChild(s);
    }
    card.append(title, meta);
    card.addEventListener("click", () => openItem(it.kind, it.id));
    return card;
  }

  async function loadItems() {
    const box = $("dataList");
    box.textContent = "Загрузка…";
    const params = new URLSearchParams({
      kind: $("dataKind").value,
      q: $("dataQ").value.trim(),
      offset: String(offset),
      limit: String(PAGE),
      indexed: $("dataIndexed").value,
    });
    if ($("dataSpace").value) params.set("space_id", $("dataSpace").value);
    try {
      const d = await api("/data/items?" + params.toString());
      box.innerHTML = "";
      if (!d.items.length) box.appendChild(el("div", "empty-state", "Ничего не найдено."));
      d.items.forEach((it) => box.appendChild(itemEl(it)));
      $("dataPage").textContent = d.total
        ? `${offset + 1}–${Math.min(offset + PAGE, d.total)} из ${d.total}`
        : "0 записей";
      $("dataPrev").disabled = offset === 0;
      $("dataNext").disabled = offset + PAGE >= d.total;
    } catch (e) {
      box.textContent = e.message;
    }
  }

  async function openItem(kind, id) {
    const box = $("dataDetail");
    box.hidden = false;
    box.textContent = "Загрузка…";
    try {
      const d = await api(`/data/item/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
      box.innerHTML = "";
      box.appendChild(el("div", "section-title sub", `${KIND_LABEL[kind]} · ${d.space_id}`));
      const meta = [fmt(d.at)];
      if (d.name) meta.push(d.name);
      if (d.mime) meta.push(d.mime);
      if (d.tags && d.tags.length) meta.push(d.tags.join(", "));
      if (d.indexed === true) meta.push("в индексе");
      if (d.indexed === false) meta.push("не в индексе");
      box.appendChild(el("div", "field-hint", meta.filter(Boolean).join(" · ")));
      if (d.summary) box.appendChild(el("div", "field-hint", "Сводка: " + d.summary));
      const pre = el("pre", "", d.text || "");
      box.appendChild(pre);
      if (d.truncated) box.appendChild(el("div", "field-hint", "Показана только часть файла."));
      const close = el("button", "btn ghost sm", "Закрыть");
      close.addEventListener("click", () => { box.hidden = true; });
      box.appendChild(close);
    } catch (e) {
      box.textContent = e.message;
    }
  }

  async function loadOverview() {
    try {
      overview = await api("/data/overview");
      renderOverview();
    } catch (e) {
      $("dataMsg").textContent = e.message;
    }
  }

  $("dataApply").addEventListener("click", () => { offset = 0; loadItems(); });
  $("dataQ").addEventListener("keydown", (e) => { if (e.key === "Enter") { offset = 0; loadItems(); } });
  $("dataKind").addEventListener("change", () => { offset = 0; renderOverview(); loadItems(); });
  $("dataSpace").addEventListener("change", () => { offset = 0; renderOverview(); loadItems(); });
  $("dataIndexed").addEventListener("change", () => { offset = 0; loadItems(); });
  $("dataPrev").addEventListener("click", () => { offset = Math.max(0, offset - PAGE); loadItems(); });
  $("dataNext").addEventListener("click", () => { offset += PAGE; loadItems(); });

  window.loadDataView = async function () {
    await loadOverview();
    await loadItems();
  };
})();
