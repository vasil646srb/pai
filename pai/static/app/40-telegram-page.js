/* ===================== Telegram page ===================== */
const TG_CHAN = { public: "публичный", private: "приватный", group: "группа", owner: "владелец" };
let tgOptions = { tools: [], spaces: [] };
function tgChannel(d, key) { return (d.channels || []).find((c) => c.key === key) || { chat_id: "", mode: "" }; }
function tgChecks(name, values, checked) {
  return values.map((v) => `<label class="tg-check"><input type="checkbox" name="${name}" value="${esc(v)}"${checked.includes(v) ? " checked" : ""}> ${esc(v)}</label>`).join("");
}
function tgFill(d) {
  document.getElementById("tgTokenHint").textContent = d.token_set ? `сохранён: ${d.token_preview}` : "токен не задан";
  const pub = tgChannel(d, "public"), prv = tgChannel(d, "private"), grp = tgChannel(d, "group");
  document.getElementById("tgPublicChat").value = pub.chat_id || "";
  document.getElementById("tgPublicMode").value = pub.mode || "approve";
  document.getElementById("tgPrivateChat").value = prv.chat_id || "";
  document.getElementById("tgPrivateMode").value = prv.mode || "auto";
  document.getElementById("tgGroupChat").value = grp.chat_id || "";
  document.getElementById("tgGroupMode").value = grp.mode || "approve";
  document.getElementById("tgAutoReply").checked = !!d.group_auto_reply;
  document.getElementById("tgEnabled").checked = d.enabled !== false;
  const p = d.policy || {};
  document.getElementById("tgOwnerId").value = p.owner_id || "";
  document.getElementById("tgOwnerCommands").checked = !!p.owner_commands;
  document.getElementById("tgOwnerSpace").value = p.owner_space || "personal";
  const conf = Number(p.jev_min_confidence ?? 0.6);
  document.getElementById("tgJevConf").value = conf;
  document.getElementById("tgJevConfVal").textContent = conf.toFixed(2);
  document.querySelectorAll('input[name="tgKind"]').forEach((cb) => { cb.checked = (p.jev_reply_kinds || []).includes(cb.value); });
  const sz = p.text_sizes || {};
  document.getElementById("tgSizeOwner").value = sz.owner || "normal";
  document.getElementById("tgSizePublic").value = sz.public || "normal";
  document.getElementById("tgSizePrivate").value = sz.private || "long";
  document.getElementById("tgSizeGroup").value = sz.group || "short";
  tgRenderOptions(p);
}
function tgRenderOptions(p) {
  document.getElementById("tgGroupTools").innerHTML = tgChecks("tgTool", tgOptions.tools || [], p.group_tools || []);
  const spaces = [...new Set([...(tgOptions.spaces || []), ...(p.group_spaces || []), "personal"])].filter((s) => s !== "telegram-group");
  document.getElementById("tgGroupSpaces").innerHTML = tgChecks("tgSpace", spaces, p.group_spaces || []);
  document.getElementById("tgSpaces").innerHTML = (tgOptions.spaces || []).map((s) => `<option value="${esc(s)}">`).join("");
}
function tgPolicyBody() {
  const checked = (n) => [...document.querySelectorAll(`input[name="${n}"]:checked`)].map((c) => c.value);
  return {
    owner_id: document.getElementById("tgOwnerId").value.trim(),
    owner_commands: document.getElementById("tgOwnerCommands").checked,
    owner_space: document.getElementById("tgOwnerSpace").value.trim() || "personal",
    group_tools: checked("tgTool"),
    group_spaces: checked("tgSpace"),
    jev_min_confidence: parseFloat(document.getElementById("tgJevConf").value),
    jev_reply_kinds: checked("tgKind"),
    text_sizes: {
      owner: document.getElementById("tgSizeOwner").value,
      public: document.getElementById("tgSizePublic").value,
      private: document.getElementById("tgSizePrivate").value,
      group: document.getElementById("tgSizeGroup").value,
    },
  };
}
async function tgLoad() {
  try {
    tgOptions = await api("/telegram/options");
    tgFill(await api("/telegram/settings"));
  } catch (e) { document.getElementById("tgStatus").textContent = "ошибка: " + e.message; }
}
async function tgSetEnabled(ev) {
  const st = document.getElementById("tgStatus"), box = ev.target;
  try {
    const d = await api("/telegram/settings", { method: "PUT", body: { enabled: box.checked } });
    tgFill(d); st.textContent = box.checked ? "Telegram включён" : "Telegram выключен";
  } catch (e) { box.checked = !box.checked; st.textContent = "не сохранено: " + e.message; }
}
async function tgSave() {
  const st = document.getElementById("tgStatus");
  const body = {
    token: document.getElementById("tgToken").value.trim(),
    group_auto_reply: document.getElementById("tgAutoReply").checked,
    channels: [
      { key: "public", chat_id: document.getElementById("tgPublicChat").value.trim(), mode: document.getElementById("tgPublicMode").value },
      { key: "private", chat_id: document.getElementById("tgPrivateChat").value.trim(), mode: document.getElementById("tgPrivateMode").value },
      { key: "group", chat_id: document.getElementById("tgGroupChat").value.trim(), mode: document.getElementById("tgGroupMode").value },
    ],
    policy: tgPolicyBody(),
  };
  try {
    const d = await api("/telegram/settings", { method: "PUT", body });
    document.getElementById("tgToken").value = "";
    tgFill(d); st.textContent = "сохранено";
  } catch (e) { st.textContent = "не сохранено: " + e.message; }
}
async function tgCheck() {
  const out = document.getElementById("tgCheckOut");
  out.textContent = "проверяю…";
  try {
    const d = await api("/telegram/check", { method: "POST" });
    out.textContent = `бот @${d.bot}\n` + d.channels.map((c) => `${c.ok ? "✓" : "✗"} ${c.title}: ${c.detail}`).join("\n");
  } catch (e) { out.textContent = "ошибка: " + e.message; }
}
function tgFold(text, preview) {
  const p = (preview || "").replace(/\s+/g, " ").slice(0, 90);
  return `<summary><span class="al-note">${esc(text)}</span> <span class="fold-preview">${esc(p)}${p.length >= 90 ? "…" : ""}</span></summary>`;
}
function tgItemHtml(i, withButtons) {
  const when = new Date((i.created || 0) * 1000).toLocaleString("ru-RU");
  const btns = withButtons
    ? `<div class="llm-settings-actions"><button class="btn primary sm" data-tg="approve" data-id="${esc(i.id)}">Опубликовать</button>`
      + `<button class="btn ghost sm" data-tg="reject" data-id="${esc(i.id)}">Отклонить</button></div>`
    : "";
  const status = withButtons ? "" : `<span class="mono">${esc(i.status)}</span>` + (i.error ? ` · ${esc(i.error)}` : "");
  return `<details class="fold-item">${tgFold(`${when} · ${TG_CHAN[i.channel] || i.channel}`, i.text)}`
    + `<div class="al-note">${status}</div><pre class="mono al-pre">${esc(i.text)}</pre>${btns}</details>`;
}
function tgInboxHtml(m) {
  const when = new Date((m.date || 0) * 1000).toLocaleString("ru-RU");
  const who = m.from || "?";
  const flags = (m.read ? "" : " · <b>новое</b>") + (m.reply_status ? ` · ответ: ${esc(m.reply_status)}` : "");
  return `<details class="fold-item">${tgFold(`${when} · ${TG_CHAN[m.channel] || m.channel} · ${who}`, m.text)}`
    + `<div class="al-note">${flags}${m.error ? " · " + esc(m.error) : ""}</div><pre class="mono al-pre">${esc(m.text)}</pre></details>`;
}
async function tgQueueLoad() {
  const q = document.getElementById("tgQueue"), h = document.getElementById("tgHistory");
  try {
    const d = await api("/telegram/queue");
    q.innerHTML = d.pending.length ? d.pending.map((i) => tgItemHtml(i, true)).join("") : `<div class="empty-state">нет ожидающих постов</div>`;
    h.innerHTML = d.history.length ? d.history.map((i) => tgItemHtml(i, false)).join("") : `<div class="empty-state">пусто</div>`;
  } catch (e) { q.innerHTML = `<div class="empty-state">ошибка: ${esc(e.message)}</div>`; }
}
async function tgQueueAct(ev) {
  const b = ev.target.closest("button[data-tg]");
  if (!b) return;
  ev.preventDefault();
  try { await api(`/telegram/queue/${encodeURIComponent(b.dataset.id)}/${b.dataset.tg}`, { method: "POST" }); }
  catch (e) { alert("не выполнено: " + e.message); }
  tgQueueLoad();
}
async function tgInboxLoad() {
  const box = document.getElementById("tgInbox");
  try {
    const d = await api("/telegram/inbox");
    box.innerHTML = d.recent.length ? d.recent.map(tgInboxHtml).join("") : `<div class="empty-state">сообщений нет</div>`;
  } catch (e) { box.innerHTML = `<div class="empty-state">ошибка: ${esc(e.message)}</div>`; }
}
async function tgPollNow() {
  const st = document.getElementById("tgStatus");
  try { const d = await api("/telegram/poll", { method: "POST" }); st.textContent = `новых сообщений: ${d.new}`; }
  catch (e) { st.textContent = "не проверено: " + e.message; }
  tgInboxLoad();
}
async function tgProcessNow() {
  const st = document.getElementById("tgStatus");
  try {
    const d = await api("/telegram/process", { method: "POST" });
    st.textContent = `группа: обработано ${d.processed} из ${d.checked || 0}${d.reason ? " (" + d.reason + ")" : ""}`;
  } catch (e) { st.textContent = "не обработано: " + e.message; }
  tgInboxLoad(); tgQueueLoad();
}
async function tgProcessOwner() {
  const st = document.getElementById("tgStatus");
  try {
    const d = await api("/telegram/process_owner", { method: "POST" });
    st.textContent = `команды владельца: выполнено ${d.processed}${d.reason ? " (" + d.reason + ")" : ""}`;
  } catch (e) { st.textContent = "не выполнено: " + e.message; }
  tgInboxLoad();
}
function tgInit() {
  if (!document.getElementById("tgSave") || document.getElementById("tgSave").dataset.bound) return;
  document.getElementById("tgSave").dataset.bound = "1";
  document.getElementById("tgSave").addEventListener("click", tgSave);
  document.getElementById("tgCheck").addEventListener("click", tgCheck);
  document.getElementById("tgEnabled").addEventListener("change", tgSetEnabled);
  document.getElementById("tgQueueRefresh").addEventListener("click", tgQueueLoad);
  document.getElementById("tgQueue").addEventListener("click", tgQueueAct);
  document.getElementById("tgPoll").addEventListener("click", tgPollNow);
  document.getElementById("tgProcess").addEventListener("click", tgProcessNow);
  document.getElementById("tgProcessOwner").addEventListener("click", tgProcessOwner);
  document.getElementById("tgJevConf").addEventListener("input", (e) => {
    document.getElementById("tgJevConfVal").textContent = Number(e.target.value).toFixed(2);
  });
  tgLoad(); tgQueueLoad(); tgInboxLoad();
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tgInit);
else tgInit();

