/* ===================== PERSONAS ===================== */

let toolsCatalogCache = null;
let skillsCatalogCache = null;

async function getToolsCatalog() {
  if (!toolsCatalogCache) toolsCatalogCache = await api("/tools");
  return toolsCatalogCache;
}

async function getSkillsCatalog() {
  if (!skillsCatalogCache) skillsCatalogCache = await api("/skills");
  return skillsCatalogCache;
}

function fillMultiSelect(select, options, valueKey, labelKey, selected) {
  const sel = new Set(selected || []);
  select.innerHTML = options.map((o) => {
    const v = o[valueKey];
    return `<option value="${esc(v)}" ${sel.has(v) ? "selected" : ""}>${esc(o[labelKey] || v)}</option>`;
  }).join("");
}

function getSelectedValues(select) {
  return Array.from(select.selectedOptions).map((o) => o.value);
}

async function populateCreatePersonaSelects() {
  const [tools, skills] = await Promise.all([getToolsCatalog(), getSkillsCatalog()]);
  const toolsSel = document.getElementById("npTools");
  const skillsSel = document.getElementById("npSkills");
  fillMultiSelect(toolsSel, tools, "name", "name", ["get_fragment", "search_archive", "calculate_time_delta"]);
  fillMultiSelect(skillsSel, skills, "skill_id", "skill_id", []);
}

async function loadPersonas() {
  // Перечитываем каталог инструментов: активированные custom-функции
  // должны сразу появляться в списке разрешений персоны.
  toolsCatalogCache = null;
  const box = document.getElementById("personaList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    const personas = await api("/personas");
    if (!personas.length) { box.innerHTML = `<div class="empty-state">Персон нет</div>`; return; }
    box.innerHTML = personas.map((p) => `
      <details class="fold-item persona-card" data-id="${esc(p.persona_id)}">
        <summary><div class="persona-card-top">
          <div><span class="persona-name">${esc(p.name)}</span><span class="persona-id">${esc(p.persona_id)}</span></div>
          <span class="tag">${esc(p.tone)}</span>
        </div></summary>
        <div class="persona-desc">${esc(p.description || "—")}</div>
        <div class="persona-meta">
          <span class="tag">spaces: ${(p.allow_spaces || []).length ? esc(p.allow_spaces.join(", ")) : "все"}</span>
          ${(p.deny_spaces || []).length ? `<span class="tag" style="color:var(--danger); border-color:var(--danger);">deny: ${esc(p.deny_spaces.join(", "))}</span>` : ""}
          ${(p.active_skill_ids || []).length ? `<span class="tag">skills: ${esc(p.active_skill_ids.join(", "))}</span>` : ""}
          <span class="tag">tools: ${esc((p.tool_permissions || []).join(", "))}</span>
        </div>
        <div class="persona-actions">
          <button class="btn sm btnEditPersona" data-id="${esc(p.persona_id)}">Изменить</button>
          <button class="btn sm danger btnDeletePersona" data-id="${esc(p.persona_id)}">Удалить</button>
        </div>
        <div class="persona-edit-form" id="edit-${esc(p.persona_id)}" style="display:none;"></div>
      </details>
    `).join("");

    box.querySelectorAll(".btnEditPersona").forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = personas.find((x) => x.persona_id === btn.dataset.id);
        toggleEditForm(p);
      });
    });
    box.querySelectorAll(".btnDeletePersona").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Удалить персону "${btn.dataset.id}"?`)) return;
        try {
          await api(`/personas/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
          toast("Персона удалена", "ok");
          loadPersonas();
          loadPersonasForChat();
        } catch (e) {
          toast("Ошибка: " + e.message, "err");
        }
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

async function toggleEditForm(p) {
  const wrap = document.getElementById(`edit-${p.persona_id}`);
  if (!wrap) return;
  const isOpen = wrap.style.display !== "none";
  // close any other open forms
  document.querySelectorAll(".persona-edit-form").forEach((el) => (el.style.display = "none"));
  if (isOpen) return;
  wrap.style.display = "block";
  wrap.innerHTML = `
    <label>Имя</label>
    <input type="text" class="epName" value="${esc(p.name)}" />
    <label>Описание</label>
    <textarea class="epDesc" rows="2">${esc(p.description || "")}</textarea>
    <label>Инструкции персоны (system_core_addon — уходят в системный промпт)</label>
    <textarea class="epAddon" rows="9" style="font-family:var(--mono);font-size:12px;">${esc(p.system_core_addon || "")}</textarea>
    <label>Тон</label>
    <input type="text" class="epTone" value="${esc(p.tone)}" />
    <label>Разрешённые пространства памяти (через запятую, пусто = все)</label>
    <input type="text" class="epAllow" value="${esc((p.allow_spaces || []).join(", "))}" />
    <label>Запрещённые пространства памяти (через запятую)</label>
    <input type="text" class="epDeny" value="${esc((p.deny_spaces || []).join(", "))}" />
    <label>Разрешённые теги (пусто = без ограничений)</label>
    <input type="text" class="epAllowTags" value="${esc((p.allow_tags || []).join(", "))}" />
    <label>Скрытые теги (например diary)</label>
    <input type="text" class="epDenyTags" value="${esc((p.deny_tags || []).join(", "))}" />
    <label>Активные навыки</label>
    <select class="epSkills" multiple size="4"></select>
    <label>Разрешённые инструменты</label>
    <select class="epTools" multiple size="4"></select>
    <div class="section-title" style="margin-top:16px; font-size:14px;">Бюджет контекста (токены)</div>
    <div class="budget-settings-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:8px 0 14px;">
      ${[
        ["system_core","System core"],["persona_header","Persona header"],["current_time","Current time"],
        ["skill_triggers","Skill triggers"],["tree_tops","Tree tops"],["tail","Tail"],
        ["retrieved_fragments","Retrieved fragments"],["reasoning_reserve","Reasoning reserve"],["total_budget","Total budget"]
      ].map(([key,label]) => `
        <div class="field budget-field">
          <label>${label}</label>
          <div style="display:flex;gap:5px;align-items:center;">
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="-1000">−1000</button>
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="-100">−100</button>
            <input type="number" min="0" step="100" class="epBudget" data-key="${key}" value="${Number((p.budget_profile || {})[key] ?? 0)}" style="min-width:0;flex:1;" />
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="100">+100</button>
            <button type="button" class="btn sm ghost budget-adjust" data-key="${key}" data-delta="1000">+1000</button>
          </div>
        </div>`).join("")}
    </div>
    <div class="page-sub" style="margin:-4px 0 12px;">Быстрые кнопки меняют значение на 100 или 1000 токенов. Значения сохраняются отдельно для каждой персоны.</div>
    <div class="persona-actions">
      <button class="btn sm primary epSave">Сохранить</button>
      <button class="btn sm ghost epCancel">Отмена</button>
    </div>
  `;
  const [tools, skills] = await Promise.all([getToolsCatalog(), getSkillsCatalog()]);
  fillMultiSelect(wrap.querySelector(".epTools"), tools, "name", "name", p.tool_permissions || []);
  fillMultiSelect(wrap.querySelector(".epSkills"), skills, "skill_id", "skill_id", p.active_skill_ids || []);
  wrap.querySelectorAll(".budget-adjust").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = wrap.querySelector(`.epBudget[data-key="${btn.dataset.key}"]`);
      if (!input) return;
      const current = Number(input.value || 0);
      input.value = Math.max(0, current + Number(btn.dataset.delta || 0));
    });
  });
  const splitList = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  wrap.querySelector(".epCancel").addEventListener("click", () => (wrap.style.display = "none"));
  wrap.querySelector(".epSave").addEventListener("click", async () => {
    try {
      await api(`/personas/${encodeURIComponent(p.persona_id)}`, {
        method: "PATCH",
        body: {
          name: wrap.querySelector(".epName").value.trim(),
          description: wrap.querySelector(".epDesc").value.trim(),
          system_core_addon: wrap.querySelector(".epAddon").value,
          allow_tags: splitList(wrap.querySelector(".epAllowTags").value),
          deny_tags: splitList(wrap.querySelector(".epDenyTags").value),
          tone: wrap.querySelector(".epTone").value.trim() || "neutral",
          allow_spaces: splitList(wrap.querySelector(".epAllow").value),
          deny_spaces: splitList(wrap.querySelector(".epDeny").value),
          active_skill_ids: getSelectedValues(wrap.querySelector(".epSkills")),
          tool_permissions: getSelectedValues(wrap.querySelector(".epTools")),
          budget_profile: Object.fromEntries([...wrap.querySelectorAll(".epBudget")].map((input) => [input.dataset.key, Math.max(0, Math.round(Number(input.value || 0)))])),
        },
      });
      toast("Настройки персоны сохранены", "ok");
      loadPersonas();
      loadPersonasForChat();
    } catch (e) {
      toast("Ошибка: " + e.message, "err");
    }
  });
}

document.getElementById("btnCreatePersona").addEventListener("click", async () => {
  const persona_id = document.getElementById("npId").value.trim();
  const name = document.getElementById("npName").value.trim();
  if (!persona_id || !name) { toast("Укажите persona_id и имя", "err"); return; }
  const splitList = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  try {
    await api("/personas", {
      method: "POST",
      body: {
        persona_id, name,
        description: document.getElementById("npDesc").value.trim(),
        system_core_addon: document.getElementById("npAddon").value,
        allow_spaces: splitList(document.getElementById("npAllowSpaces").value),
        deny_spaces: splitList(document.getElementById("npDenySpaces").value),
        tone: document.getElementById("npTone").value.trim() || "neutral",
        active_skill_ids: getSelectedValues(document.getElementById("npSkills")),
        tool_permissions: getSelectedValues(document.getElementById("npTools")),
      },
    });
    toast("Персона создана", "ok");
    ["npId", "npName", "npDesc", "npAddon", "npAllowSpaces", "npDenySpaces", "npTone"].forEach((id) => document.getElementById(id).value = "");
    loadPersonas();
    loadPersonasForChat();
  } catch (e) {
    toast("Ошибка: " + e.message, "err");
  }
});

/* ===================== COLLAPSIBLE CARDS ===================== */

const COLLAPSE_STORAGE_KEY = "pai_collapsed_cards_v1";

function getCollapsedCards() {
  try { return JSON.parse(localStorage.getItem(COLLAPSE_STORAGE_KEY) || "{}"); }
  catch (_) { return {}; }
}

function setCardCollapsed(card, collapsed, persist = true) {
  const body = card.querySelector(".collapse-body");
  const header = card.querySelector(".collapse-header");
  const toggle = card.querySelector(".collapse-toggle");
  if (!body || !header || !toggle) return;
  card.classList.toggle("is-collapsed", collapsed);
  header.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-label", collapsed ? "Развернуть карточку" : "Свернуть карточку");
  toggle.title = collapsed ? "Развернуть" : "Свернуть";
  toggle.textContent = collapsed ? "⌄" : "⌃";
  if (persist) {
    const key = card.dataset.collapseKey;
    if (key) {
      const state = getCollapsedCards();
      state[key] = collapsed;
      localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(state));
    }
  }
}

function initCollapsibleCards(container) {
  const state = getCollapsedCards();
  container.querySelectorAll(".collapsible-card").forEach((card) => {
    const key = card.dataset.collapseKey;
    setCardCollapsed(card, key ? state[key] === true : false, false);
    const header = card.querySelector(".collapse-header");
    const toggle = card.querySelector(".collapse-toggle");
    if (!header || !toggle) return;
    header.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, select, textarea")) return;
      setCardCollapsed(card, !card.classList.contains("is-collapsed"));
    });
    header.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target.closest("button")) return;
      event.preventDefault();
      setCardCollapsed(card, !card.classList.contains("is-collapsed"));
    });
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      setCardCollapsed(card, !card.classList.contains("is-collapsed"));
    });
  });
}

/* ===================== RULES ===================== */

/* ===================== SKILLS VIEW ===================== */

async function loadSkillsView() {
  const box = document.getElementById("skillList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    skillsCatalogCache = null;
    const skills = await getSkillsCatalog();
    if (!skills.length) { box.innerHTML = `<div class="empty-state">Скилов нет</div>`; return; }
    box.innerHTML = skills.map((s) => `
      <details class="fold-item rule-card" data-id="${esc(s.skill_id)}">
<summary class="rule-card-top">
          <div class="collapse-heading">
            <span class="tag">${esc(s.skill_id)}</span>
            <span class="tag">v${esc(String(s.version))}</span>
            <span class="fold-preview">${esc(s.trigger_description || "")}</span>
          </div>
          </summary>
        <div class="fold-body">
          <div class="rule-text"><b>Триггер:</b> ${esc(s.trigger_description)}</div>
          <div class="rule-text" style="margin-top:6px; white-space:pre-wrap;">${esc(s.full_instruction_text || "")}</div>
          <div class="rule-meta">
            <span>id: ${esc(s.skill_id)}</span>
            <span>обновлено: ${fmtTs(s.last_updated)}</span>
          </div>
          <div class="rule-actions">
            <button class="btn sm danger" data-act="delete">удалить</button>
          </div>
        </div>
      </details>
    `).join("");

    initCollapsibleCards(box);
    box.querySelectorAll(".rule-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm(`Удалить скил «${id}»? Он также будет отвязан от всех персон, где был подключён.`)) return;
          try {
            await api(`/skills/${encodeURIComponent(id)}`, { method: "DELETE" });
            skillsCatalogCache = null;
            toast("Скил удалён", "ok");
            loadSkillsView();
          } catch (e) {
            toast("Ошибка: " + e.message, "err");
          }
        });
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

document.getElementById("btnCreateSkill").addEventListener("click", async () => {
  const skill_id = document.getElementById("nsId").value.trim();
  const trigger_description = document.getElementById("nsTrigger").value.trim();
  const full_instruction_text = document.getElementById("nsText").value.trim();
  if (!skill_id) { toast("Введите skill_id", "err"); return; }
  if (!trigger_description) { toast("Введите триггер", "err"); return; }
  try {
    await api("/skills", { method: "POST", body: { skill_id, trigger_description, full_instruction_text } });
    document.getElementById("nsId").value = "";
    document.getElementById("nsTrigger").value = "";
    document.getElementById("nsText").value = "";
    skillsCatalogCache = null;
    toast("Скил создан", "ok");
    loadSkillsView();
  } catch (e) {
    toast("Ошибка: " + e.message, "err");
  }
});

async function loadRules() {
  const box = document.getElementById("ruleList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    let rules = await api("/rules");
    const filter = document.getElementById("rulesStatusFilter").value;
    if (filter) rules = rules.filter((r) => r.status === filter);
    if (!rules.length) { box.innerHTML = `<div class="empty-state">Правил нет</div>`; return; }
    rules.sort((a, b) => b.created_at - a.created_at);
    box.innerHTML = rules.map((r) => `
      <details class="fold-item rule-card" data-id="${esc(r.id)}">
<summary class="rule-card-top">
          <div class="collapse-heading">
            <span class="badge-status ${esc(r.status)}">${esc(r.status)}</span>
            <span class="tag">${esc(r.scope)}${r.persona_id ? " · " + esc(r.persona_id) : ""}${r.space_id ? " · " + esc(r.space_id) : ""}</span>
            <span class="fold-preview">${esc(r.rule_text || "")}</span>
          </div>
          </summary>
        <div class="fold-body">
          <div class="rule-text">${esc(r.rule_text)}</div>
          ${r.trigger_context ? `<div style="color:var(--text-faint); font-size:11.5px; margin-bottom:6px;">из: ${esc(r.trigger_context)}</div>` : ""}
          <div class="rule-meta">
            <span>id: ${esc(r.id)}</span>
            <span>confidence: ${r.confidence}</span>
            <span>создано: ${fmtTs(r.created_at)}</span>
          </div>
          <div class="rule-actions">
          ${r.status === "draft" ? `<button class="btn sm" data-act="validate">validate</button>` : ""}
          ${r.status === "validated" ? `<button class="btn sm primary" data-act="activate">activate</button>` : ""}
          ${r.status === "active" ? `<button class="btn sm ghost" data-act="archive">archive</button>` : ""}
          ${r.status !== "rejected" && r.status !== "archived" ? `<button class="btn sm danger" data-act="reject">reject</button>` : ""}
          <button class="btn sm danger" data-act="delete">удалить</button>
          </div>
        </div>
      </details>
    `).join("");

    initCollapsibleCards(box);
    box.querySelectorAll(".rule-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const action = btn.dataset.act;
          if (action === "delete") {
            if (!confirm("Удалить это правило безвозвратно?")) return;
            try {
              await api(`/rules/${encodeURIComponent(id)}`, { method: "DELETE" });
              toast("Правило удалено", "ok");
              loadRules();
            } catch (e) {
              toast("Ошибка: " + e.message, "err");
            }
            return;
          }
          try {
            await api(`/rules/${encodeURIComponent(id)}/${action}`, { method: "POST" });
            toast("Готово", "ok");
            loadRules();
          } catch (e) {
            toast("Ошибка: " + e.message, "err");
          }
        });
      });
    });
  } catch (e) {
    box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`;
  }
}

async function loadFunctions() {
  const box = document.getElementById("functionList");
  box.innerHTML = `<div class="empty-state">Загрузка…</div>`;
  try {
    let funcs = await api("/functions");
    const filter = document.getElementById("functionsStatusFilter").value;
    if (filter) funcs = funcs.filter((f) => f.status === filter);
    funcs.sort((a, b) => b.created_at - a.created_at);
    if (!funcs.length) { box.innerHTML = `<div class="empty-state">Функций нет</div>`; return; }
    box.innerHTML = funcs.map((f) => {
      const impl = JSON.stringify(f.implementation || {}, null, 2);
      const params = JSON.stringify(f.parameters || {}, null, 2);
      return `
        <details class="fold-item rule-card" data-id="${esc(f.id)}">
<summary class="rule-card-top">
            <div class="collapse-heading">
              <span class="badge-status ${esc(f.status)}">${esc(f.status)}</span>
              <span class="tag">${esc(f.kind)} · ${esc(f.name)}</span>
              <span class="fold-preview">${esc(f.description || "")}</span>
            </div>
            </summary>
          <div class="fold-body">
            <div class="rule-text">${esc(f.description || "Без описания")}</div>
            <div class="grid grid-2" style="margin-top:8px;">
              <div><div class="section-title" style="font-size:11px;">parameters</div><pre class="mono" style="white-space:pre-wrap;">${esc(params)}</pre></div>
              <div><div class="section-title" style="font-size:11px;">implementation</div><pre class="mono" style="white-space:pre-wrap;">${esc(impl)}</pre></div>
            </div>
            <div class="rule-meta"><span>id: ${esc(f.id)}</span><span>создано: ${fmtTs(f.created_at)}</span></div>
            <div class="rule-actions">
              ${f.status === "draft" ? `<button class="btn sm" data-act="validate">проверить</button>` : ""}
              ${f.status === "validated" ? `<button class="btn sm primary" data-act="activate">✓ подтвердить и активировать</button>` : ""}
              ${f.status !== "rejected" && f.status !== "active" ? `<button class="btn sm danger" data-act="reject">отклонить</button>` : ""}
              ${f.status === "active" ? `<button class="btn sm danger" data-act="archive">отключить</button>` : ""}
              <button class="btn sm danger" data-act="delete">удалить</button>
            </div>
          </div>
        </details>`;
    }).join("");
    initCollapsibleCards(box);
    box.querySelectorAll(".rule-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const action = btn.dataset.act;
          if (action === "activate" && !confirm("Активировать эту функцию? После подтверждения ассистент сможет вызывать её в чате.")) return;
          if (action === "archive" && !confirm("Отключить эту функцию? Она перестанет передаваться модели.")) return;
          if (action === "delete") {
            if (!confirm("Удалить эту функцию безвозвратно? Она также будет отвязана от всех персон.")) return;
            try {
              await api(`/functions/${encodeURIComponent(id)}`, { method: "DELETE" });
              toast("Функция удалена", "ok");
              toolsCatalogCache = null;
              loadFunctions();
            } catch (e) { toast("Ошибка: " + e.message, "err"); }
            return;
          }
          try {
            await api(`/functions/${encodeURIComponent(id)}/${action}`, { method: "POST" });
            toast(action === "activate" ? "Функция активирована" : "Готово", "ok");
            toolsCatalogCache = null;
            loadFunctions();
          } catch (e) { toast("Ошибка: " + e.message, "err"); }
        });
      });
    });
  } catch (e) { box.innerHTML = `<div class="empty-state">Ошибка: ${esc(e.message)}</div>`; }
}

document.getElementById("functionsStatusFilter").addEventListener("change", loadFunctions);
document.getElementById("btnReloadFunctions").addEventListener("click", loadFunctions);
document.getElementById("btnCreateFunction").addEventListener("click", async () => {
  const name = document.getElementById("nfName").value.trim();
  if (!name) { toast("Укажите имя функции", "err"); return; }
  try {
    const parameters = JSON.parse(document.getElementById("nfParameters").value);
    const implementation = JSON.parse(document.getElementById("nfImplementation").value || "{}");
    await api("/functions", { method: "POST", body: {
      name,
      description: document.getElementById("nfDescription").value.trim(),
      kind: document.getElementById("nfKind").value,
      parameters, implementation,
    }});
    toast("Draft функции создан", "ok");
    ["nfName", "nfDescription", "nfImplementation"].forEach((id) => document.getElementById(id).value = "");
    document.getElementById("nfParameters").value = '{"type":"object","properties":{}}';
    loadFunctions();
  } catch (e) { toast("Ошибка: " + e.message, "err"); }
});

document.getElementById("rulesStatusFilter").addEventListener("change", loadRules);
document.getElementById("btnReloadRules").addEventListener("click", loadRules);

document.getElementById("btnCreateRule").addEventListener("click", async () => {
  const rule_text = document.getElementById("nrText").value.trim();
  if (!rule_text) { toast("Введите текст правила", "err"); return; }
  try {
    await api("/rules", {
      method: "POST",
      body: {
        rule_text,
        scope: document.getElementById("nrScope").value,
        persona_id: document.getElementById("nrPersona").value.trim() || null,
        space_id: document.getElementById("nrSpace").value.trim() || null,
        trigger_context: document.getElementById("nrTrigger").value.trim(),
      },
    });
    toast("Draft создан", "ok");
    ["nrText", "nrPersona", "nrSpace", "nrTrigger"].forEach((id) => document.getElementById(id).value = "");
    loadRules();
  } catch (e) {
    toast("Ошибка: " + e.message, "err");
  }
});

