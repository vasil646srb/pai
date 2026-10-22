/* ===================== SETTINGS ===================== */

document.getElementById("apiBaseInput").value = getApiBase();
/* ===================== secrets (owner only; values are write-only) ===================== */
async function loadSecrets() {
  const list = document.getElementById("secretList");
  try {
    const data = await api("/secrets");
    list.textContent = "";
    if (!data.names.length) list.innerHTML = `<div class="empty-state">Секретов пока нет</div>`;
    data.names.forEach((name) => {
      const row = document.createElement("div");
      row.className = "settings-row";
      row.innerHTML = `<code>{{secret:${esc(name)}}}</code>`;
      const del = document.createElement("button");
      del.className = "btn ghost sm";
      del.textContent = "Удалить";
      del.addEventListener("click", async () => {
        try { await api(`/secrets/${encodeURIComponent(name)}`, { method: "DELETE" }); loadSecrets(); }
        catch (e) { toast("Ошибка: " + e.message, "err"); }
      });
      row.appendChild(del);
      list.appendChild(row);
    });
  } catch (e) {
    list.textContent = "⚠ " + e.message;
  }
}

document.getElementById("btnSecretSave").addEventListener("click", async () => {
  const name = document.getElementById("secretName").value.trim();
  const value = document.getElementById("secretValue").value;
  const msg = document.getElementById("secretMsg");
  try {
    await api(`/secrets/${encodeURIComponent(name)}`, { method: "PUT", body: { value } });
    document.getElementById("secretValue").value = "";
    msg.textContent = `Сохранено: {{secret:${name}}}`;
    loadSecrets();
  } catch (e) {
    msg.textContent = "⚠ " + e.message;
  }
});
loadSecrets();

document.getElementById("btnSaveSettings").addEventListener("click", async () => {
  setApiBase(document.getElementById("apiBaseInput").value.trim());
  toast("Сохранено");
  await checkConnection();
  init();
});
document.getElementById("btnTestConn").addEventListener("click", async () => {
  const statusEl = document.getElementById("settingsStatus");
  statusEl.textContent = "Проверка…";
  const ok = await checkConnection();
  statusEl.textContent = ok ? "✓ соединение установлено" : "✗ не удалось подключиться";
});

/* ---------- LLM provider settings ---------- */

function renderLlmStatusPill(data) {
  const pill = document.getElementById("llmStatusPill");
  if (!pill) return;
  if (data.using_mock) {
    pill.className = "llm-status-pill off";
    pill.textContent = "используется заглушка (MockLLM) — провайдер не настроен";
  } else if (data.active) {
    pill.className = "llm-status-pill ok";
    pill.textContent = `активно: ${data.model || "модель не указана"}`;
  } else {
    pill.className = "llm-status-pill off";
    pill.textContent = "не настроено";
  }
}

function fmtPrice(v) {
  if (v == null) return "—";
  // Sub-$1/1M is the common case for small/open models — show enough
  // precision to distinguish e.g. $0.15 from $0.20 without noisy trailing zeros.
  return v < 1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;
}

function fmtCtx(v) {
  if (!v) return "—";
  return v >= 1000 ? `${Math.round(v / 1000)}k` : String(v);
}

// Model-picker factory: builds an independent picker (state + DOM wiring)
// bound to a given set of element ids. Used twice — once for the main
// assistant model, once for the summarizer model — so the two pickers
// don't share selection/sort/filter state.
function makeModelPicker(ids) {
  const state = {
    models: [],       // raw list from /settings/llm/models
    sortKey: "id",
    sortDir: "asc",    // "asc" | "desc"
    query: "",
    onlyPriced: false,
    selectedId: "",
  };

  function getRows() {
    const q = state.query.trim().toLowerCase();
    let rows = state.models.filter((m) => {
      if (state.onlyPriced && m.price_prompt_per_1m == null && m.price_completion_per_1m == null) return false;
      if (!q) return true;
      return m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q);
    });
    const { sortKey, sortDir } = state;
    const dir = sortDir === "asc" ? 1 : -1;
    rows = rows.slice().sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      // Unknown price/context always sorts last regardless of direction —
      // otherwise ascending-price sort would put "unpriced" ahead of "cheap",
      // which reads as backwards to the user.
      const aMissing = av == null;
      const bMissing = bv == null;
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;
      if (bMissing) return -1;
      if (sortKey === "id") return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });
    return rows;
  }

  function renderHead() {
    document.querySelectorAll(`#${ids.head} .mt-col`).forEach((el) => {
      const key = el.dataset.sort;
      const ico = el.querySelector(".sort-ico");
      if (key === state.sortKey) {
        el.classList.add("sorted");
        ico.textContent = state.sortDir === "asc" ? "▲" : "▼";
      } else {
        el.classList.remove("sorted");
        ico.textContent = "";
      }
    });
  }

  function renderBody() {
    const body = document.getElementById(ids.body);
    const rows = getRows();
    if (!rows.length) {
      body.innerHTML = `<div class="model-table-empty">Ничего не найдено</div>`;
      return;
    }
    body.innerHTML = rows.map((m) => {
      const selected = m.id === state.selectedId ? " selected" : "";
      const pIn = m.price_prompt_per_1m;
      const pOut = m.price_completion_per_1m;
      return `
        <div class="model-row-item${selected}" data-id="${esc(m.id)}">
          <span class="mr-id" title="${esc(m.id)}">${esc(m.id)}</span>
          <span class="mr-price${pIn == null ? " unknown" : ""}">${fmtPrice(pIn)}</span>
          <span class="mr-price${pOut == null ? " unknown" : ""}">${fmtPrice(pOut)}</span>
          <span class="mr-ctx">${fmtCtx(m.context_length)}</span>
        </div>`;
    }).join("");
    body.querySelectorAll(".model-row-item").forEach((el) => {
      el.addEventListener("click", () => select(el.dataset.id));
    });
  }

  function select(id) {
    state.selectedId = id;
    document.getElementById(ids.manual).value = id;
    renderBody();
  }

  document.getElementById(ids.head).addEventListener("click", (e) => {
    const col = e.target.closest(".mt-col");
    if (!col) return;
    const key = col.dataset.sort;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDir = "asc"; // cheapest / smallest-context first by default
    }
    renderHead();
    renderBody();
  });
  document.getElementById(ids.search).addEventListener("input", (e) => {
    state.query = e.target.value;
    renderBody();
  });
  document.getElementById(ids.filterPriced).addEventListener("change", (e) => {
    state.onlyPriced = e.target.checked;
    renderBody();
  });
  // Manual field opens/closes the picker; typing directly still works for
  // providers where the user already knows the exact model id.
  document.getElementById(ids.manual).addEventListener("click", () => {
    const picker = document.getElementById(ids.picker);
    if (state.models.length) picker.hidden = !picker.hidden;
  });

  return {
    state,
    renderHead,
    renderBody,
    select,
    async load(baseUrl, apiKeyField, hintEl) {
      const picker = document.getElementById(ids.picker);
      hintEl.textContent = "Загрузка списка моделей…";
      const params = new URLSearchParams({ base_url: baseUrl });
      if (apiKeyField) params.set("api_key", apiKeyField); // else: backend falls back to the saved key
      try {
        const data = await api(`/settings/llm/models?${params.toString()}`);
        const models = data.models || [];
        state.models = models;
        state.query = "";
        document.getElementById(ids.search).value = "";
        const withPrice = models.filter((m) => m.price_prompt_per_1m != null || m.price_completion_per_1m != null).length;
        picker.hidden = models.length === 0;
        renderHead();
        renderBody();
        hintEl.textContent = models.length
          ? `Загружено моделей: ${models.length}${withPrice ? ` · с ценой: ${withPrice}` : " · провайдер не сообщает цены"}`
          : "Провайдер не вернул список моделей.";
      } catch (e) {
        hintEl.textContent = "Ошибка: " + e.message;
      }
    },
  };
}

const modelPicker = makeModelPicker({
  head: "modelTableHead", body: "modelTableBody", manual: "llmModelManual",
  picker: "modelPicker", search: "modelSearch", filterPriced: "modelFilterPriced",
});
const summarizerModelPicker = makeModelPicker({
  head: "modelTableHeadSummarizer", body: "modelTableBodySummarizer", manual: "llmSummarizerModelManual",
  picker: "modelPickerSummarizer", search: "modelSearchSummarizer", filterPriced: "modelFilterPricedSummarizer",
});
const verifyModelPicker = makeModelPicker({
  head: "modelTableHeadVerify", body: "modelTableBodyVerify", manual: "llmVerifyModelManual",
  picker: "modelPickerVerify", search: "modelSearchVerify", filterPriced: "modelFilterPricedVerify",
});
const STRICTNESS_LABELS = {
  1: "1 · легко", 2: "2 · мягко", 3: "3 · средне", 4: "4 · строго", 5: "5 · очень строго",
};
function setStrictnessLabel() {
  const v = document.getElementById("llmVerifyStrictness").value;
  document.getElementById("llmVerifyStrictnessLabel").textContent = STRICTNESS_LABELS[v] || v;
}
document.getElementById("llmVerifyStrictness").addEventListener("input", setStrictnessLabel);

function setMaxChecksLabel() {
  const n = Number(document.getElementById("llmVerifyMaxChecks").value) || 2;
  const txt = n === 1 ? "1 · только проверка" : `${n} · до ${n - 1} переделок`;
  document.getElementById("llmVerifyMaxChecksLabel").textContent = txt;
}
document.getElementById("llmVerifyMaxChecks").addEventListener("input", setMaxChecksLabel);

const visionModelPicker = makeModelPicker({
  head: "modelTableHeadVision", body: "modelTableBodyVision", manual: "llmVisionModelManual",
  picker: "modelPickerVision", search: "modelSearchVision", filterPriced: "modelFilterPricedVision",
});
// Legacy alias — some code below still refers to the main picker's state
// directly by its old name.
const modelPickerState = modelPicker.state;

async function loadLlmSettings() {
  try {
    const data = await api("/settings/llm");
    document.getElementById("llmBaseUrl").value = data.base_url || "";
    document.getElementById("llmSiteUrl").value = data.site_url || "";
    document.getElementById("llmSiteName").value = data.site_name || "";
    document.getElementById("llmApiKey").value = "";
    document.getElementById("llmApiKey").placeholder = data.api_key_set
      ? `сохранён: ${data.api_key_preview} (оставьте пустым, чтобы не менять)`
      : "не задан";
    document.getElementById("llmApiKeyHint").textContent = data.api_key_set
      ? "Ключ хранится на сервере и не показывается полностью. Впишите новый, чтобы заменить."
      : "";
    modelPickerState.selectedId = data.model || "";
    document.getElementById("llmModelManual").value = data.model || "";
    document.getElementById("llmVerifyStrictness").value = data.verify_strictness ?? 3;
    document.getElementById("llmVerifyMaxChecks").value = data.verify_max_checks ?? 2;
    setMaxChecksLabel();
    setStrictnessLabel();
    visionModelPicker.state.selectedId = data.vision_model || "";
    document.getElementById("llmVisionModelManual").value = data.vision_model || "";
    verifyModelPicker.state.selectedId = data.verify_model || "";
    document.getElementById("llmVerifyModelManual").value = data.verify_model || "";
    document.getElementById("llmVerifyModelManual").placeholder = data.verify_model
      ? ""
      : `пусто = как для суммаризации (сейчас: ${data.effective_verify_model || "—"})`;
    document.getElementById("llmPlannerModelManual").value = data.planner_model || "";
    summarizerModelPicker.state.selectedId = data.summarizer_model || "";
    document.getElementById("llmSummarizerModelManual").value = data.summarizer_model || "";
    document.getElementById("llmSummarizerModelManual").placeholder = data.summarizer_model
      ? ""
      : `пусто = как основная (сейчас: ${data.effective_summarizer_model || "—"})`;
    document.getElementById("llmTemperature").value = data.temperature ?? 0.3;
    document.getElementById("llmMaxTokens").value = data.max_tokens ?? 1024;
    document.getElementById("llmTopP").value = data.top_p ?? "";
    document.getElementById("llmContextWindow").value = data.context_window ?? 8192;
    document.getElementById("llmMaxToolIterations").value = data.max_tool_iterations ?? 10;
    document.getElementById("llmCompressionTriggerPct").value = data.compression_trigger_pct ?? 50;
    document.getElementById("llmTopK").value = data.top_k ?? "";
    document.getElementById("llmFrequencyPenalty").value = data.frequency_penalty ?? "";
    document.getElementById("llmPresencePenalty").value = data.presence_penalty ?? "";
    document.getElementById("llmReasoningEnabled").checked = !!data.reasoning_enabled;
    document.getElementById("llmReasoningEffort").value = data.reasoning_effort || "medium";
    renderLlmStatusPill(data);
  } catch (e) {
    const statusEl = document.getElementById("llmSettingsStatus");
    if (statusEl) statusEl.textContent = "Не удалось загрузить настройки: " + e.message;
  }
}

async function loadLlmModels() {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  await modelPicker.load(baseUrl, apiKeyField, hint);
}
document.getElementById("btnLoadModels").addEventListener("click", loadLlmModels);

async function loadSummarizerModels() {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  // Same provider/base URL as the main model — reuses the shared models endpoint.
  await summarizerModelPicker.load(baseUrl, apiKeyField, hint);
}
document.getElementById("btnLoadModelsSummarizer").addEventListener("click", loadSummarizerModels);
document.getElementById("btnLoadModelsVerify").addEventListener("click", async () => {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  await verifyModelPicker.load(baseUrl, apiKeyField, hint);
});
document.getElementById("btnLoadModelsVision").addEventListener("click", async () => {
  const hint = document.getElementById("llmModelsHint");
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();
  const apiKeyField = document.getElementById("llmApiKey").value;
  if (!baseUrl) {
    hint.textContent = "Сначала укажите base URL.";
    return;
  }
  await visionModelPicker.load(baseUrl, apiKeyField, hint);
});
document.getElementById("btnClearVisionModel").addEventListener("click", () => {
  visionModelPicker.state.selectedId = "";
  document.getElementById("llmVisionModelManual").value = "";
  visionModelPicker.renderBody();
});
document.getElementById("btnClearVerifyModel").addEventListener("click", () => {
  verifyModelPicker.state.selectedId = "";
  document.getElementById("llmVerifyModelManual").value = "";
  document.getElementById("llmVerifyModelManual").placeholder = "пусто = как для суммаризации";
  verifyModelPicker.renderBody();
});
document.getElementById("btnClearSummarizerModel").addEventListener("click", () => {
  summarizerModelPicker.state.selectedId = "";
  document.getElementById("llmSummarizerModelManual").value = "";
  document.getElementById("llmSummarizerModelManual").placeholder = "пусто = как основная модель";
  summarizerModelPicker.renderBody();
});

document.getElementById("btnSaveLlmSettings").addEventListener("click", async () => {
  const statusEl = document.getElementById("llmSettingsStatus");
  const btn = document.getElementById("btnSaveLlmSettings");
  const model = document.getElementById("llmModelManual").value.trim();
  const baseUrl = document.getElementById("llmBaseUrl").value.trim();

  if (!baseUrl || !model) {
    statusEl.textContent = "Укажите base URL и модель.";
    return;
  }

  const tempRaw = document.getElementById("llmTemperature").value.trim();
  const maxTokensRaw = document.getElementById("llmMaxTokens").value.trim();
  const topPRaw = document.getElementById("llmTopP").value.trim();
  const contextWindowRaw = document.getElementById("llmContextWindow").value.trim();
  const maxToolIterationsRaw = document.getElementById("llmMaxToolIterations").value.trim();
  const compressionTriggerPctRaw = document.getElementById("llmCompressionTriggerPct").value.trim();
  const topKRaw = document.getElementById("llmTopK").value.trim();
  const freqPenaltyRaw = document.getElementById("llmFrequencyPenalty").value.trim();
  const presPenaltyRaw = document.getElementById("llmPresencePenalty").value.trim();
  
  if (tempRaw && (Number(tempRaw) < 0 || Number(tempRaw) > 2)) {
    statusEl.textContent = "Temperature должна быть в диапазоне 0–2.";
    return;
  }
  if (maxTokensRaw && Number(maxTokensRaw) < 1) {
    statusEl.textContent = "Max tokens должен быть положительным числом.";
    return;
  }
  if (topPRaw && (Number(topPRaw) < 0 || Number(topPRaw) > 1)) {
    statusEl.textContent = "Top P должен быть в диапазоне 0–1.";
    return;
  }
  if (contextWindowRaw && Number(contextWindowRaw) < 1024) {
    statusEl.textContent = "Context window должен быть не менее 1024 токенов.";
    return;
  }
  if (maxToolIterationsRaw && Number(maxToolIterationsRaw) < 1) {
    statusEl.textContent = "Max tool iterations должен быть не менее 1.";
    return;
  }
  if (topKRaw && Number(topKRaw) < 1) {
    statusEl.textContent = "Top K должен быть положительным числом.";
    return;
  }
  if (freqPenaltyRaw && (Number(freqPenaltyRaw) < -2 || Number(freqPenaltyRaw) > 2)) {
    statusEl.textContent = "Frequency penalty должен быть в диапазоне -2 до 2.";
    return;
  }
  if (presPenaltyRaw && (Number(presPenaltyRaw) < -2 || Number(presPenaltyRaw) > 2)) {
    statusEl.textContent = "Presence penalty должен быть в диапазоне -2 до 2.";
    return;
  }

  btn.disabled = true;
  statusEl.textContent = "Сохранение и применение…";
  try {
    const payload = {
      base_url: baseUrl,
      model: model,
      summarizer_model: document.getElementById("llmSummarizerModelManual").value.trim(),
      planner_model: document.getElementById("llmPlannerModelManual").value.trim(),
      verify_model: document.getElementById("llmVerifyModelManual").value.trim(),
      vision_model: document.getElementById("llmVisionModelManual").value.trim(),
      verify_strictness: Number(document.getElementById("llmVerifyStrictness").value),
      verify_max_checks: Number(document.getElementById("llmVerifyMaxChecks").value),
      site_url: document.getElementById("llmSiteUrl").value.trim(),
      site_name: document.getElementById("llmSiteName").value.trim(),
      reasoning_enabled: document.getElementById("llmReasoningEnabled").checked,
      reasoning_effort: document.getElementById("llmReasoningEffort").value,
    };
    if (tempRaw) payload.temperature = Number(tempRaw);
    if (maxTokensRaw) payload.max_tokens = Number(maxTokensRaw);
    if (topPRaw) {
      payload.top_p = Number(topPRaw);
    } else {
      payload.clear_top_p = true; // empty field = explicitly unset, not "leave unchanged"
    }
    if (contextWindowRaw) payload.context_window = Number(contextWindowRaw);
    if (maxToolIterationsRaw) payload.max_tool_iterations = Number(maxToolIterationsRaw);
    if (compressionTriggerPctRaw) payload.compression_trigger_pct = Number(compressionTriggerPctRaw);
    if (topKRaw) {
      payload.top_k = Number(topKRaw);
    } else {
      payload.clear_top_k = true;
    }
    if (freqPenaltyRaw) {
      payload.frequency_penalty = Number(freqPenaltyRaw);
    } else {
      payload.clear_frequency_penalty = true;
    }
    if (presPenaltyRaw) {
      payload.presence_penalty = Number(presPenaltyRaw);
    } else {
      payload.clear_presence_penalty = true;
    }
    const apiKeyField = document.getElementById("llmApiKey").value;
    if (apiKeyField) payload.api_key = apiKeyField; // blank = keep the stored key server-side
    const data = await api("/settings/llm", { method: "PUT", body: payload });
    statusEl.textContent = "✓ применено — новые сообщения в чате пойдут через этот провайдер.";
    document.getElementById("llmVisionModelManual").value = data.vision_model || "";
    document.getElementById("llmVerifyModelManual").value = data.verify_model || "";
    document.getElementById("llmVerifyModelManual").placeholder = data.verify_model
      ? ""
      : `пусто = как для суммаризации (сейчас: ${data.effective_verify_model || "—"})`;
    document.getElementById("llmVisionModelManual").value = data.vision_model || "";
    document.getElementById("llmVerifyStrictness").value = data.verify_strictness ?? 3;
    document.getElementById("llmVerifyMaxChecks").value = data.verify_max_checks ?? 2;
    setMaxChecksLabel();
    setStrictnessLabel();
    document.getElementById("llmSummarizerModelManual").value = data.summarizer_model || "";
    document.getElementById("llmSummarizerModelManual").placeholder = data.summarizer_model
      ? ""
      : `пусто = как основная (сейчас: ${data.effective_summarizer_model || "—"})`;
    document.getElementById("llmTemperature").value = data.temperature ?? 0.3;
    document.getElementById("llmMaxTokens").value = data.max_tokens ?? 1024;
    document.getElementById("llmTopP").value = data.top_p ?? "";
    document.getElementById("llmContextWindow").value = data.context_window ?? 8192;
    document.getElementById("llmMaxToolIterations").value = data.max_tool_iterations ?? 10;
    document.getElementById("llmCompressionTriggerPct").value = data.compression_trigger_pct ?? 50;
    document.getElementById("llmTopK").value = data.top_k ?? "";
    document.getElementById("llmFrequencyPenalty").value = data.frequency_penalty ?? "";
    document.getElementById("llmPresencePenalty").value = data.presence_penalty ?? "";
    document.getElementById("llmReasoningEnabled").checked = !!data.reasoning_enabled;
    document.getElementById("llmReasoningEffort").value = data.reasoning_effort || "medium";
    renderLlmStatusPill(data);
    document.getElementById("llmApiKey").value = "";
    document.getElementById("llmApiKey").placeholder = data.api_key_set
      ? `сохранён: ${data.api_key_preview} (оставьте пустым, чтобы не менять)`
      : "не задан";
    toast("Настройки LLM сохранены");
    loadCacheStatus();
  } catch (e) {
    statusEl.textContent = "Ошибка: " + e.message;
    toast("Не удалось сохранить настройки LLM: " + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

