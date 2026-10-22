/* ===================== reasoning display ===================== */

// ensureReasoningBlock() expects an element exposing .querySelector(".md" / ".bubble");
// while a message is still being built (not yet attached) wrap bubble in a tiny shim.
function div_holder(div, bubble) {
  return { querySelector: (sel) => (sel === ".bubble" ? bubble : bubble.querySelector(sel)) };
}

function ensureReasoningBlock(assistantDiv) {
  let block = assistantDiv.querySelector(".reasoning-block");
  if (block) return block;
  block = document.createElement("div");
  const _collapsedByDefault = window.matchMedia("(max-width: 720px)").matches;
  block.className = "reasoning-block streaming" + (_collapsedByDefault ? " collapsed" : "");
  const head = document.createElement("div");
  head.className = "reasoning-block-head";
  head.innerHTML = `<span class="chevron">▾</span><span>Размышление модели</span>`;
  const body = document.createElement("div");
  body.className = "reasoning-block-body";
  head.addEventListener("click", () => block.classList.toggle("collapsed"));
  block.appendChild(head);
  block.appendChild(body);
  // Reasoning always precedes the final answer, so it goes above the
  // markdown content — but after any attachment thumbnails.
  const mdEl = assistantDiv.querySelector(".md");
  assistantDiv.querySelector(".bubble").insertBefore(block, mdEl);
  return block;
}

/* ===================== chat send ===================== */

let chatTurnStartedAt = 0;
let chatFirstTokenAt = null;

let currentTurn = null; // { id, ctl, stopped } while a chat turn is streaming

// Shows image_search hits as cards under the assistant message. Only Wikimedia Commons
// upload URLs are accepted, and everything is built with DOM APIs (no innerHTML from data).
function renderImageGallery(container, items) {
  const imgs = (Array.isArray(items) ? items : []).filter(
    (it) => it && typeof it.image_url === "string" && /^https:\/\/upload\.wikimedia\.org\//.test(it.image_url)
  );
  if (!imgs.length) return;
  const wrap = document.createElement("div");
  wrap.className = "img-gallery";
  for (const it of imgs.slice(0, 10)) {
    const fig = document.createElement("figure");
    fig.className = "img-card";
    const link = document.createElement("a");
    link.href = /^https:\/\//.test(it.page_url || "") ? it.page_url : it.image_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const img = document.createElement("img");
    img.src = it.image_url;
    img.alt = String(it.title || "").slice(0, 200);
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => fig.remove());  // broken thumbnail: drop the card
    link.appendChild(img);
    const cap = document.createElement("figcaption");
    cap.textContent = [it.title, it.license, it.author].filter(Boolean).join(" · ");
    fig.append(link, cap);
    wrap.appendChild(fig);
  }
  container.appendChild(wrap);
}

function stopCurrentTurn() {
  const t = currentTurn;
  if (!t || t.stopped) return;
  t.stopped = true;
  document.getElementById("btnStop").disabled = true;
  const base = (getApiBase() || "").replace(/\/$/, "");
  // tell the server first (stops LLM/tool work at the next step), then drop the stream locally
  fetch(base + "/chat/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ turn_id: t.id }),
  }).catch(() => {});
  t.ctl.abort();
}
document.getElementById("btnStop").addEventListener("click", stopCurrentTurn);

async function sendChat(override) {
  if (document.getElementById("btnSend").disabled) return;  // Ctrl+Enter used to bypass the disabled button
  chatTurnStartedAt = performance.now();
  chatFirstTokenAt = null;
  const input = document.getElementById("chatInput");
  const fromQuick = typeof override === "string";
  const text = (fromQuick ? override : input.value).trim();
  if (!text && !pendingAttachments.length && !pendingFiles.length) return;
  renderQuickReplies([]);
  const persona_id = document.getElementById("chatPersona").value || "brain";
  const space_id = currentChatSpace();
  const images = pendingAttachments.slice();
  const attachedFiles = pendingFiles.slice();

  const userDiv = appendMessage(
    "user", text, `${persona_id} · ${space_id} · ${fmtTs(Date.now() / 1000)}`, images,
  );
  appendFileChips(userDiv, attachedFiles);
  if (!fromQuick) {
    input.value = "";
    input.style.height = "auto";
  }
  pendingAttachments = [];
  pendingFiles = [];
  renderAttachmentPreviews();

  const btn = document.getElementById("btnSend");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span>`;
  setTokSpeed("генерация…");
  const turnId = (crypto.randomUUID ? crypto.randomUUID() : `t${Date.now()}${Math.random().toString(36).slice(2)}`);
  const abortCtl = new AbortController();
  currentTurn = { id: turnId, ctl: abortCtl, stopped: false };
  const stopBtn = document.getElementById("btnStop");
  stopBtn.disabled = false;
  stopBtn.hidden = false;

  const base = getApiBase();
  let assistantDiv = null;
  let mdEl = null;
  let reasoningBlock = null;
  let reasoningBodyEl = null;
  let toolTraceEl = null;
  let liveToolEls = {}; // name -> most recent open <div class="tool-call live">
  let fullText = "";
  let fullReasoning = "";
  let finalVerification = null;
  let firstChunkAt = null;
  let lastChunkAt = null;
  let chunkCount = 0;

  function ensureAssistantDiv() {
    if (!assistantDiv) {
      assistantDiv = appendMessage("assistant", "", `${persona_id} · печатает…`, null, null, { scroll: false });
      assistantDiv.classList.add("streaming");
      mdEl = assistantDiv.querySelector(".md");
    }
    return assistantDiv;
  }

  function ensureToolTraceEl() {
    ensureAssistantDiv();
    if (!toolTraceEl) {
      toolTraceEl = document.createElement("div");
      toolTraceEl.className = "tool-trace";
      assistantDiv.parentNode.insertBefore(toolTraceEl, assistantDiv);
      // Tool calls hidden in the chat UI. Full request/response details
      // live on the Action Log page. For local debugging, set
      // localStorage.pai.showToolCalls = "1" and reload.
      const hideCb = document.getElementById("hideToolCalls");
      const hide = hideCb ? hideCb.checked : localStorage.getItem("pai.hideToolCalls") !== "0";
      if (hide) toolTraceEl.style.display = "none";
    }
    return toolTraceEl;
  }

  assistantDiv = appendMessage("assistant", "", `${persona_id} · печатает…`, null, null, { scroll: false });
    assistantDiv.classList.add("streaming");
    mdEl = assistantDiv.querySelector(".md");
  try {
    let streamBase = getApiBase();
    if (streamBase && streamBase.endsWith("/")) streamBase = streamBase.slice(0, -1);
    const res = await fetch(streamBase + "/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, persona_id, space_id, images, file_ids: attachedFiles.map((f) => f.id), turn_id: turnId }),
      signal: abortCtl.signal,
    });
    if (!res.ok || !res.body) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
      throw new Error(detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let finalTrace = [];
    let finalPreview = null;
    let finalCacheStatus = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // Normalize SSE line endings so LF/CRLF providers behave identically.
      buf = buf.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      const parts = buf.split("\n\n");
      buf = parts.pop(); // last event may be incomplete
      for (const part of parts) {
        const dataLines = part
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim());
        if (!dataLines.length) continue;
        let evt;
        try { evt = JSON.parse(dataLines.join("\n")); } catch (_) { continue; }

        if (evt.type === "error") {
          throw new Error(evt.message || "ошибка потока");
        }
        if (evt.type === "stopped") {
          break;
        }
        if (evt.type === "tool_call") {
          const trace = ensureToolTraceEl();
          const key = evt.name + "::" + JSON.stringify(evt.arguments);
          liveToolEls[key] = renderLiveToolCall(trace, evt.name, evt.arguments);
          continue;
        }
        if (evt.type === "tool_result") {
          const key = evt.name + "::" + JSON.stringify(evt.arguments);
          resolveLiveToolCall(liveToolEls[key], evt.result);
          delete liveToolEls[key];

          if (evt.name === "image_search" && evt.result) {
            renderImageGallery(ensureAssistantDiv(), evt.result.results);
          }

          // После propose_function сервер уже сохранил draft в functions.json.
          // Немедленно перечитываем /functions, чтобы draft появился в UI
          // без ручного обновления страницы.
          if (evt.name === "propose_function" && evt.result && evt.result.created) {
            const functionView = document.getElementById("view-functions");
            if (functionView && functionView.classList.contains("active")) {
              await loadFunctions();
            }
          }
          continue;
        }
        if (evt.type === "delta_reset") {
          // the model streamed a preamble and then asked for tools: drop it
          fullText = "";
          if (mdEl) mdEl.innerHTML = "";
          continue;
        }
        if (evt.type === "check_failed") {
          // an answer that failed the answer check never stays in the chat: keep it in reasoning
          ensureAssistantDiv();
          if (!reasoningBlock) {
            reasoningBlock = ensureReasoningBlock(assistantDiv);
            reasoningBodyEl = reasoningBlock.querySelector(".reasoning-block-body");
          }
          const plan = (evt.fix_plan || []).map((x, i) => `${i + 1}. ${x}`).join("\n");
          const note = `— Ответ не прошёл проверку (попытка ${evt.attempt}) —\n${evt.text}` +
            (evt.report ? `\nОтчёт проверки: ${evt.report}` : "") +
            (plan ? `\nПлан исправления:\n${plan}` : "");
          fullReasoning += (fullReasoning ? "\n\n" : "") + note;
          reasoningBodyEl.textContent = fullReasoning;
          fullText = "";
          if (mdEl) mdEl.innerHTML = "";
          continue;
        }
        if (evt.type === "reasoning_delta") {
          ensureAssistantDiv();
          if (!reasoningBlock) {
            reasoningBlock = ensureReasoningBlock(assistantDiv);
            reasoningBodyEl = reasoningBlock.querySelector(".reasoning-block-body");
          }
          fullReasoning += evt.text;
          reasoningBodyEl.textContent = fullReasoning;
          const wrap = document.getElementById("chatMessages");
          wrap.scrollTop = wrap.scrollHeight;
          // Allow the browser to paint before processing another buffered SSE event.
          await new Promise(requestAnimationFrame);
          continue;
        }
        if (evt.type === "delta") {
          ensureAssistantDiv();
          if (reasoningBlock) reasoningBlock.classList.remove("streaming");
          const now = performance.now();
          if (firstChunkAt === null) firstChunkAt = now;
          if (chatFirstTokenAt === null) chatFirstTokenAt = now;
          lastChunkAt = now;
          chunkCount++;
          fullText += evt.text;
          mdEl.innerHTML = renderMarkdown(fullText);

          if (firstChunkAt !== null && lastChunkAt > firstChunkAt) {
            const elapsedS = (lastChunkAt - firstChunkAt) / 1000;
            const toks = estTokens(fullText);
            const tps = elapsedS > 0 ? (toks / elapsedS) : 0;
            setTokSpeed(`${tps.toFixed(1)} ток/с`);
          }

          // reader.read() can resolve immediately for already-buffered chunks.
          // Force a frame boundary so each delta becomes visibly incremental.
          await new Promise(requestAnimationFrame);
        }
        if (evt.type === "tool_trace") {
          finalTrace = evt.trace || [];
        }
        if (evt.type === "cache_status") {
          finalCacheStatus = evt.status || null;
        }
        if (evt.type === "done") {
          finalPreview = evt.live_context_preview;
          if (evt.turn_usage) renderChatUsage(evt.turn_usage, finalPreview, chatFirstTokenAt != null ? (chatFirstTokenAt - chatTurnStartedAt) / 1000 : null);
          renderChatTokUsage(evt.turn_usage || null, finalPreview);
          if (evt.cache_status) finalCacheStatus = evt.cache_status;
          if (evt.reply) fullText = evt.reply;  // authoritative final text
          if (evt.verification) finalVerification = evt.verification;
          if (!fullReasoning && evt.reasoning) {
            fullReasoning = evt.reasoning;
            // reasoning that never arrived as deltas: render it now (was silently dropped before)
            reasoningBlock = ensureReasoningBlock(ensureAssistantDiv());
            reasoningBodyEl = reasoningBlock.querySelector(".reasoning-block-body");
            reasoningBodyEl.textContent = fullReasoning;
          }
        }
      }
    }

    if (reasoningBlock) {
      reasoningBlock.classList.remove("streaming");
      if (!fullReasoning.trim()) reasoningBlock.remove();
    }

    if (!assistantDiv) {
      assistantDiv = appendMessage("assistant", fullText, `${persona_id}`);
      mdEl = assistantDiv.querySelector(".md");
    } else {
      mdEl.innerHTML = renderMarkdown(fullText);
      assistantDiv.classList.remove("streaming");
      const toolNote = finalTrace.length ? ` · ${finalTrace.length} вызов(ов) инструментов` : "";
      const metaEl = assistantDiv.querySelector(".meta");
      if (metaEl) metaEl.textContent = `${persona_id}${toolNote}`;
    }
    if (assistantDiv) attachCopyButton(assistantDiv, () => fullText);
    renderVerificationBlock(assistantDiv, finalVerification);
    // quick replies: the checker's suggestions for the user's likely next message (last attempt)
    const lastCheck = finalVerification && finalVerification.length ? finalVerification[finalVerification.length - 1] : null;
    renderQuickReplies(lastCheck ? lastCheck.suggested_replies : []);
    // tool calls were already rendered live during the stream; nothing more to append here
    chatHistory.push({ role: "assistant", tool_trace: finalTrace });
    if (finalPreview) renderBudget(finalPreview);
    if (finalPreview && !document.getElementById("cuInput")?.textContent) renderChatUsage(null, finalPreview, null);
    if (finalPreview) renderChatTokUsage(null, finalPreview);
    if (finalCacheStatus) renderCacheStatus(finalCacheStatus);
    refreshHeaderStack();
  } catch (e) {
    if (currentTurn && currentTurn.stopped) {
      // user pressed stop: keep whatever was already streamed, mark it as stopped
      if (assistantDiv) {
        assistantDiv.classList.remove("streaming");
        const metaEl = assistantDiv.querySelector(".meta");
        if (metaEl) metaEl.textContent = `${persona_id} · остановлено`;
        if (mdEl && !fullText && !fullReasoning) mdEl.innerHTML = renderMarkdown("⏹ Остановлено");
      }
      toast("Запрос остановлен");
      return;
    }
    toast("Ошибка чата: " + e.message, "err");
    if (assistantDiv) {
      assistantDiv.classList.remove("streaming");
      const metaEl = assistantDiv.querySelector(".meta");
      if (metaEl) metaEl.textContent = `${persona_id} · ошибка`;
      if (!fullText && !fullReasoning) mdEl.innerHTML = renderMarkdown("⚠ " + e.message);
    } else {
      appendMessage("assistant", "⚠ " + e.message, "ошибка");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Отправить";
    setTokSpeed("");
    stopBtn.hidden = true;
    stopBtn.disabled = false;
    currentTurn = null;
  }
}

// Under the reply: the last check's verdict when it did not pass (report, what is missing, fix plan),
// or the reason the check did not run. Nothing is shown when the reply passed.
function renderVerificationBlock(div, verdicts) {
  if (!div || !verdicts || !verdicts.length) return;
  div.querySelectorAll(".check-block").forEach((el) => el.remove());
  const last = verdicts[verdicts.length - 1];
  if (!last || last.status === "pass" || last.ok === true) return;
  const box = document.createElement("div");
  if (last.status === "error") {
    box.className = "check-block error";
    box.innerHTML = `<div class="check-title">Проверка ответа не выполнена</div>` +
      `<div class="check-text">${esc(last.error || "неизвестная ошибка")}</div>`;
  } else {
    const steps = (last.fix_plan || []).map((x) => `<li>${esc(x)}</li>`).join("");
    box.className = "check-block fail";
    box.innerHTML = `<div class="check-title">⚠ Ответ не прошёл проверку (проверок: ${verdicts.length})</div>` +
      `<div class="check-text"><b>Отчёт:</b> ${esc(last.report || "—")}</div>` +
      (last.missing ? `<div class="check-text"><b>Не хватает:</b> ${esc(last.missing)}</div>` : "") +
      (steps ? `<div class="check-text"><b>План исправления:</b><ol>${steps}</ol></div>` : "");
  }
  div.appendChild(box);
}

document.getElementById("btnSend").addEventListener("click", sendChat);
document.getElementById("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendChat();
  }
  // plain Enter (and Shift+Enter) just insert a newline — default textarea behavior
});
document.getElementById("chatInput").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(140, e.target.scrollHeight) + "px";
});

/* ===================== reasoning toggle (chat toolbar) ===================== */

// Reasoning is a provider-level generation setting (like temperature), edited
// only on the Settings page (llmReasoningEnabled / llmReasoningEffort).

/* ===================== chat history auto-load ===================== */

let historyLoadToken = 0;
async function loadChatHistory() {
  const persona_id = document.getElementById("chatPersona").value || "brain";
  const space_id = currentChatSpace();
  const myToken = ++historyLoadToken;
  const wrap = document.getElementById("chatMessages");
  wrap.innerHTML = `<div class="empty-state">Загрузка истории…</div>`;
  chatHistory.length = 0;
  try {
    const res = await api(`/chat/history?space_id=${encodeURIComponent(space_id)}&limit=200`);
    if (myToken !== historyLoadToken) return; // a newer load superseded this one
    wrap.innerHTML = "";
    if (!res.messages || !res.messages.length) {
      wrap.innerHTML = `<div class="empty-state">История пуста для «${esc(space_id)}» — начните диалог.</div>`;
      return;
    }
    res.messages.forEach((m) => {
      // Skip persisted tool/skill results — they live in memory as
      // level-0 nodes for the assistant to retrieve via get_fragment,
      // but they are internal state, not part of the visible dialogue.
      if (m.text && m.text.startsWith("[Skill/tool execution result]")) return;
      if (m.origin === "service") {
        appendServiceEvent(m, persona_id);
        return;
      }
      const meta = m.occurred_at ? `${persona_id} · ${fmtTs(m.occurred_at)}` : persona_id;
      appendMessage(m.role, m.text, meta, null, m.reasoning);
    });
    wrap.scrollTop = wrap.scrollHeight;
  } catch (e) {
    if (myToken !== historyLoadToken) return;
    wrap.innerHTML = `<div class="empty-state">Не удалось загрузить историю: ${esc(e.message)}</div>`;
  }
}
document.getElementById("chatPersona").addEventListener("change", () => {
  updatePersonaMenuLabel();
  populateChatSpaces().then(loadChatHistory);
});
document.getElementById("chatSpace").addEventListener("change", (e) => {
  const persona_id = document.getElementById("chatPersona").value || "brain";
  try { localStorage.setItem(chatSpaceKey(persona_id), e.target.value); } catch (_) { /* storage unavailable */ }
  loadChatHistory();
});

