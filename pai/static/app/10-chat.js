/* ===================== CHAT ===================== */

let personasCache = [];
const chatHistory = []; // {role, text, tool_trace, usage}

/* Memory space of the active persona. A persona with allow_spaces
   (e.g. work -> ["work","shared"]) is rejected by the server (403) in any
   other space, so the chat must talk to the persona's own space; personas
   with no restriction use "personal". */
function chatSpaceFor(personaId) {
  const p = personasCache.find((x) => x.persona_id === personaId);
  if (!p) return "personal";
  const allow = p.allow_spaces || [], deny = p.deny_spaces || [];
  if (!allow.length) return deny.includes("personal") ? "default" : "personal";
  if (allow.includes("personal") && !deny.includes("personal")) return "personal";
  return allow.find((sp) => !deny.includes(sp)) || "personal";
}
// The memory space the chat talks to. Chosen in #chatSpace; the choice is remembered per persona
// in this browser only (a per-viewer convenience). The server still enforces allow/deny lists.
function currentChatSpace() {
  const sel = document.getElementById("chatSpace");
  if (sel && sel.value) return sel.value;
  return chatSpaceFor(document.getElementById("chatPersona").value || "brain");
}

const chatSpaceKey = (personaId) => `pai.chatSpace.${personaId}`;

async function populateChatSpaces() {
  const sel = document.getElementById("chatSpace");
  if (!sel) return;
  const persona_id = document.getElementById("chatPersona").value || "brain";
  const p = personasCache.find((x) => x.persona_id === persona_id);
  const allow = (p && p.allow_spaces) || [];
  const deny = (p && p.deny_spaces) || [];
  let options;
  if (allow.length) {
    options = allow.filter((sp) => !deny.includes(sp));
  } else {
    // unrestricted persona: any space that exists in memory is available
    let known = [];
    try { known = Object.keys(await api("/spaces")); } catch (_) { known = []; }
    options = ["personal", ...known].filter((sp, i, a) => a.indexOf(sp) === i && !deny.includes(sp));
  }
  let preferred = null;
  try { preferred = localStorage.getItem(chatSpaceKey(persona_id)); } catch (_) { /* storage unavailable */ }
  const value = (preferred && options.includes(preferred)) ? preferred : chatSpaceFor(persona_id);
  if (!options.includes(value)) options.unshift(value);
  sel.innerHTML = options.map((sp) => `<option value="${esc(sp)}">${esc(sp)}</option>`).join("");
  sel.value = value;
}

async function loadPersonasForChat() {
  try {
    personasCache = await api("/personas");
  } catch (e) {
    toast("Не удалось загрузить персоны: " + e.message, "err");
    return;
  }
  const sel = document.getElementById("chatPersona");
  const prev = sel.value;  // keep the user's selection when the list is refreshed
  sel.innerHTML = personasCache.map((p) => `<option value="${esc(p.persona_id)}">${esc(p.name)}</option>`).join("");
  if (prev && personasCache.some((p) => p.persona_id === prev)) sel.value = prev;
  updatePersonaMenuLabel();
  await populateChatSpaces();
}

function updatePersonaMenuLabel() {
  const sel = document.getElementById("chatPersona");
  if (!sel) return;
  const opt = sel.options[sel.selectedIndex];
  if (opt) sel.title = opt.textContent;
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* fall through to the legacy path */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
  ta.remove();
  return ok;
}

function attachCopyButton(msgDiv, getText) {
  // Re-attach on every call: a streamed message's button must read the final text.
  msgDiv.querySelectorAll(".msg-copy").forEach((el) => el.remove());
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-copy";
  btn.title = "Копировать сообщение";
  btn.setAttribute("aria-label", "Копировать сообщение");
  btn.textContent = "⧉";
  btn.addEventListener("click", async () => {
    const ok = await copyText(getText());
    btn.textContent = ok ? "✓" : "✗";
    setTimeout(() => { btn.textContent = "⧉"; }, 1200);
  });
  msgDiv.appendChild(btn);
}

function renderQuickReplies(list) {
  const box = document.getElementById("quickReplies");
  box.innerHTML = "";
  (list || []).filter(Boolean).slice(0, 2).forEach((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn sm quick-reply";
    b.textContent = t;
    b.addEventListener("click", () => sendChat(t));
    box.appendChild(b);
  });
  box.hidden = box.children.length === 0;
}

// opts.scroll (default true): scroll the chat to the bottom after appending.
// Streamed assistant answers pass scroll:false so the reader keeps their place in the history.
function appendMessage(role, text, meta, attachments, reasoning, opts = {}) {
  const wrap = document.getElementById("chatMessages");
  const empty = wrap.querySelector(".empty-state");
  if (empty) empty.remove();

  const div = document.createElement("div");
  div.className = "msg " + role;
  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (attachments && attachments.length) {
    const att = document.createElement("div");
    att.className = "attachments";
    attachments.forEach((dataUrl) => {
      const img = document.createElement("img");
      img.src = dataUrl;
      att.appendChild(img);
    });
    bubble.appendChild(att);
  }

  if (role === "assistant") {
    const md = document.createElement("div");
    md.className = "md";
    md.innerHTML = renderMarkdown(text);
    bubble.appendChild(md);
    if (reasoning && reasoning.trim()) {
      // finished (history) message: show the saved reasoning, collapsed
      const blk = ensureReasoningBlock(div_holder(div, bubble));
      blk.classList.remove("streaming");
      blk.classList.add("collapsed");
      blk.querySelector(".reasoning-block-body").textContent = reasoning;
    }
  } else if (text) {
    const textNode = document.createElement("div");
    textNode.textContent = text;
    bubble.appendChild(textNode);
  }
  div.appendChild(bubble);

  if (meta) {
    const m = document.createElement("div");
    m.className = "meta";
    m.textContent = meta;
    div.appendChild(m);
  }

  attachCopyButton(div, () => text);
  wrap.appendChild(div);
  if (opts.scroll !== false) wrap.scrollTop = wrap.scrollHeight;
  return div;
}

// A service run is an event, not part of the user's dialogue: rendered apart, with its source.
function appendServiceEvent(m, persona_id) {
  const wrap = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = "msg service-event " + (m.role === "assistant" ? "assistant" : "user");
  const label = document.createElement("div");
  label.className = "service-event-label";
  const who = m.role === "assistant" ? "результат" : "задание";
  label.textContent = `Служба «${m.origin_name || "без названия"}» · ${who}`;
  div.appendChild(label);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (m.role === "assistant") {
    const md = document.createElement("div");
    md.className = "md";
    md.innerHTML = renderMarkdown(m.text || "");
    bubble.appendChild(md);
  } else {
    bubble.textContent = m.text || "";
  }
  div.appendChild(bubble);
  if (m.occurred_at) {
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${persona_id} · ${fmtTs(m.occurred_at)} · событие службы`;
    div.appendChild(meta);
  }
  attachCopyButton(div, () => m.text || "");
  wrap.appendChild(div);
}

function renderLiveToolCall(container, name, args) {
  const c = document.createElement("div");
  c.className = "tool-call live";
  c.dataset.tool = name;
  c.innerHTML = `<span class="tool-spin"></span><span class="tname">${esc(name)}</span>(${esc(JSON.stringify(args))})<span class="tool-live-label"> · выполняется…</span>`;
  container.appendChild(c);
  const wrap = document.getElementById("chatMessages");
  wrap.scrollTop = wrap.scrollHeight;
  return c;
}

function resolveLiveToolCall(el, result) {
  if (!el) return;
  el.classList.remove("live");
  const label = el.querySelector(".tool-live-label");
  if (label) label.remove();
  const spinner = el.querySelector(".tool-spin");
  if (spinner) spinner.remove();
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(result, null, 2);
  pre.style.display = "none";
  el.appendChild(pre);
  el.addEventListener("click", () => { pre.style.display = pre.style.display === "none" ? "block" : "none"; });
}

function appendToolTrace(div, trace) {
  if (!trace || !trace.length) return;
  const tt = document.createElement("div");
  tt.className = "tool-trace";
  trace.forEach((call) => {
    const c = document.createElement("div");
    c.className = "tool-call";
    c.innerHTML = `<span class="tname">${esc(call.name)}</span>(${esc(JSON.stringify(call.arguments))})`;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(call.result, null, 2);
    pre.style.display = "none";
    c.appendChild(pre);
    c.addEventListener("click", () => { pre.style.display = pre.style.display === "none" ? "block" : "none"; });
    tt.appendChild(c);
  });
  div.appendChild(tt);
}

