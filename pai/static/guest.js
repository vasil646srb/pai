// Public visitor page. Does not load app.js, so the owner's token is never sent from here.
(() => {
  const $ = (id) => document.getElementById(id);
  const KEY_ID = "pai.guestId";
  const KEY_ROOMS = "pai.guestRooms";   // {roomId: token} on this device only
  const MAX_IMAGES = 3;
  const MAX_IMAGE_BYTES = 4.5 * 1024 * 1024;

  function randomId() {
    const a = new Uint8Array(12);
    crypto.getRandomValues(a);
    return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  function lsGet(k) { try { return localStorage.getItem(k) || ""; } catch (_) { return ""; } }
  function lsSet(k, v) { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch (_) {} }
  const gid = lsGet(KEY_ID) || (() => { const id = randomId(); lsSet(KEY_ID, id); return id; })();

  const roomTokens = {
    all() { try { return JSON.parse(lsGet(KEY_ROOMS) || "{}"); } catch (_) { return {}; } },
    get(id) { return this.all()[id] || ""; },
    set(id, token) {
      const m = this.all();
      if (token) m[id] = token; else delete m[id];
      lsSet(KEY_ROOMS, Object.keys(m).length ? JSON.stringify(m) : "");
    },
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;  // text only: replies are never rendered as HTML
    return e;
  }
  function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = () => reject(new Error("Не удалось прочитать файл"));
      r.readAsDataURL(file);
    });
  }
  async function getJson(url, headers) {
    const res = await fetch(url, { headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const e = new Error(data.detail || `Ошибка ${res.status}`); e.status = res.status; throw e; }
    return data;
  }

  // Assistant reply: markdown (escaped by renderMarkdown in md.js), or plain text for visitors' own messages.
  function bodyNode(role, text) {
    if (role.startsWith("bot") && !role.includes("pending")) {
      const md = el("div", "md");
      md.innerHTML = renderMarkdown(text);
      return md;
    }
    return document.createTextNode(text);
  }

  // One chat block: used for the shared chat and for each private room.
  // opts: { historyUrl, streamUrl, headers(), onAuthLost() }
  function chatBlock(container, opts) {
    container.textContent = "";
    const box = el("div", "g-chat");
    const log = el("div", "guest-log");
    const previews = el("div", "guest-previews");
    const hint = el("div", "guest-hint");
    const form = el("form", "g-composer");
    const attach = el("button", "chat-attach-btn", "🖼");
    attach.type = "button";
    attach.title = "Прикрепить картинку";
    const file = el("input");
    file.type = "file"; file.accept = "image/*"; file.multiple = true; file.hidden = true;
    const text = el("textarea");
    text.rows = 1; text.maxLength = 2000; text.placeholder = "Вопрос…";
    text.addEventListener("input", () => { text.style.height = "auto"; text.style.height = Math.min(text.scrollHeight, 140) + "px"; });
    text.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
    const send = el("button", "btn primary", "Отправить");
    send.type = "submit";
    form.append(attach, file, text, send);
    box.append(log, previews, hint, form);
    container.append(box);

    const images = [];
    function addMsg(role, body) {
      const d = el("div", "g-msg " + role);
      const bubble = el("div", "bubble");
      bubble.appendChild(bodyNode(role, body));
      d.appendChild(bubble);
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      return d;
    }
    // Live status while the assistant works: "Ищу в интернете…" etc.
    function setStatus(msgEl, label) {
      const bubble = msgEl.querySelector(".bubble");
      bubble.textContent = "";
      const st = el("span", "g-status");
      st.append(el("span", "g-dots"), el("span", "", label));
      bubble.appendChild(st);
      log.scrollTop = log.scrollHeight;
    }
    function setReply(msgEl, reply) {
      const bubble = msgEl.querySelector(".bubble");
      bubble.textContent = "";
      bubble.appendChild(bodyNode("bot", reply));
      msgEl.classList.remove("pending");
    }
    function renderPreviews() {
      previews.textContent = "";
      images.forEach((_, i) => {
        const chip = el("span", "file-chip", `🖼 ${i + 1}`);
        const rm = el("button", "remove", "×");
        rm.type = "button";
        rm.addEventListener("click", () => { images.splice(i, 1); renderPreviews(); });
        chip.appendChild(rm);
        previews.appendChild(chip);
      });
    }
    function authLost() { opts.onAuthLost(); }

    async function load() {
      try {
        const data = await getJson(opts.historyUrl(), opts.headers());
        log.textContent = "";
        (data.messages || []).forEach((m) => addMsg(m.role, m.text));
      } catch (e) {
        if (e.status === 401) return authLost();
        hint.textContent = "⚠ " + e.message;
      }
    }

    attach.addEventListener("click", () => file.click());
    file.addEventListener("change", async (e) => {
      const files = Array.from(e.target.files || []);
      e.target.value = "";
      for (const f of files) {
        if (images.length >= MAX_IMAGES) { hint.textContent = `Не больше ${MAX_IMAGES} картинок`; break; }
        if (!f.type.startsWith("image/")) continue;
        if (f.size > MAX_IMAGE_BYTES) { hint.textContent = `${f.name}: больше 4.5 МБ`; continue; }
        images.push(await readAsDataUrl(f));
      }
      renderPreviews();
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = text.value.trim();
      if (!body && !images.length) return;
      const toSend = images.splice(0);
      renderPreviews();
      text.value = "";
      text.style.height = "";
      hint.textContent = "";
      addMsg("me", body || "🖼 картинка");
      const pending = addMsg("bot pending", "");
      setStatus(pending, "Думаю…");
      send.disabled = true;
      try {
        const res = await fetch(opts.streamUrl(), {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, opts.headers()),
          body: JSON.stringify({ guest_id: gid, text: body, images: toSend }),
        });
        if (res.status === 401) { pending.remove(); return authLost(); }
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || `Ошибка ${res.status}`);
        }
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        let reply = null;
        let remainingHour = null;
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          const parts = buf.split("\n\n");
          buf = parts.pop();
          for (const part of parts) {
            const line = part.split("\n").find((l) => l.startsWith("data:"));
            if (!line) continue;
            let evt;
            try { evt = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
            if (evt.type === "status") setStatus(pending, evt.text);
            else if (evt.type === "error") throw new Error(evt.message || "ошибка");
            else if (evt.type === "done") { reply = evt.reply; remainingHour = evt.remaining_hour; }
          }
        }
        if (reply === null) throw new Error("Помощник не ответил. Попробуйте позже.");
        setReply(pending, reply || "(пустой ответ)");
        if (remainingHour !== null) hint.textContent = `Осталось сообщений в этом часу: ${remainingHour}`;
      } catch (err) {
        pending.classList.remove("pending");
        pending.classList.add("err");
        pending.querySelector(".bubble").textContent = "⚠ " + err.message;
      } finally {
        send.disabled = false;
      }
    });

    return { load };
  }

  // ---- shared chat
  const commonBlock = chatBlock($("paneCommon"), {
    historyUrl: () => "/guest/common/messages",
    streamUrl: () => "/guest/chat/stream",
    headers: () => ({}),
    onAuthLost: () => {},
  });
  commonBlock.load();

  // ---- rooms
  const roomsView = $("roomsView");
  const roomView = $("roomView");
  let openRoomId = null;

  function showRooms(msg) {
    openRoomId = null;
    roomView.hidden = true;
    roomView.textContent = "";
    roomsView.hidden = false;
    $("roomMsg").textContent = msg || "";
    renderRooms();
  }

  function openRoom(room, token) {
    openRoomId = room.id;
    roomsView.hidden = true;
    roomView.hidden = false;
    roomView.textContent = "";
    const head = el("div", "g-room-head");
    const back = el("button", "btn ghost sm", "← Комнаты");
    back.type = "button";
    back.addEventListener("click", () => showRooms(""));
    head.append(el("strong", "", room.name), back);
    const box = el("div");
    roomView.append(head, box);
    const block = chatBlock(box, {
      historyUrl: () => `/guest/rooms/${room.id}/messages`,
      streamUrl: () => `/guest/rooms/${room.id}/chat/stream`,
      headers: () => ({ "X-Room-Token": roomTokens.get(room.id) }),
      onAuthLost: () => { roomTokens.set(room.id, ""); showRooms("Сессия комнаты истекла, войдите снова."); },
    });
    block.load();
  }

  async function renderRooms() {
    const list = $("roomsList");
    list.textContent = "";
    try {
      const data = await getJson("/guest/rooms", {});
      if (!data.rooms.length) list.appendChild(el("div", "guest-hint", "Комнат пока нет."));
      data.rooms.forEach((room) => {
        const row = el("div", "g-room");
        row.appendChild(el("span", "name", room.name));
        if (roomTokens.get(room.id)) {
          const open = el("button", "btn ghost sm", "Открыть");
          open.type = "button";
          open.addEventListener("click", () => openRoom(room, roomTokens.get(room.id)));
          row.appendChild(open);
        } else {
          const pw = el("input", "g-field");
          pw.type = "password"; pw.placeholder = "Пароль"; pw.autocomplete = "off";
          const go = el("button", "btn ghost sm", "Войти");
          go.type = "button";
          const err = el("div", "guest-hint");
          err.style.flexBasis = "100%";
          go.addEventListener("click", async () => {
            err.textContent = "";
            try {
              const res = await fetch(`/guest/rooms/${room.id}/unlock`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password: pw.value }),
              });
              const data2 = await res.json().catch(() => ({}));
              if (!res.ok) throw new Error(data2.detail || `Ошибка ${res.status}`);
              roomTokens.set(room.id, data2.token);
              openRoom(room, data2.token);
            } catch (e) { err.textContent = "⚠ " + e.message; }
          });
          row.append(pw, go, err);
        }
        list.appendChild(row);
      });
    } catch (e) {
      list.appendChild(el("div", "guest-hint", "⚠ " + e.message));
    }
  }

  $("roomCreate").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("roomMsg");
    msg.textContent = "";
    try {
      const res = await fetch("/guest/rooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guest_id: gid, name: $("roomName").value.trim(), password: $("roomPassword").value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Ошибка ${res.status}`);
      roomTokens.set(data.id, data.token);
      $("roomName").value = "";
      $("roomPassword").value = "";
      openRoom({ id: data.id, name: data.name }, data.token);
    } catch (err) {
      msg.textContent = "⚠ " + err.message;
    }
  });

  // ---- owner message
  $("msgForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("msgStatus");
    const btn = $("btnMsgSend");
    btn.disabled = true;
    status.textContent = "";
    try {
      const res = await fetch("/guest/inbox", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guest_id: gid, name: $("msgName").value.trim(), text: $("msgText").value.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Ошибка ${res.status}`);
      $("msgText").value = "";
      status.textContent = "Отправлено. Спасибо!";
    } catch (err) {
      status.textContent = "⚠ " + err.message;
    } finally {
      btn.disabled = false;
    }
  });

  // ---- theme: same key and switch as the owner UI
  const themeBtn = $("themeToggle");
  function applyThemeIcon() {
    themeBtn.textContent = document.documentElement.getAttribute("data-theme") === "dark" ? "☀️" : "🌙";
  }
  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    lsSet("pai.theme", next);
    applyThemeIcon();
  });
  applyThemeIcon();

  // ---- tabs
  function tab(which) {
    const panes = { common: "paneCommon", rooms: "paneRooms", msg: "paneMsg" };
    for (const [k, id] of Object.entries(panes)) $(id).hidden = k !== which;
    $("tabCommon").classList.toggle("active", which === "common");
    $("tabRooms").classList.toggle("active", which === "rooms");
    $("tabMsg").classList.toggle("active", which === "msg");
    if (which === "rooms") { if (openRoomId) { roomsView.hidden = true; roomView.hidden = false; } else showRooms(""); }
  }
  $("tabCommon").addEventListener("click", () => tab("common"));
  $("tabRooms").addEventListener("click", () => tab("rooms"));
  $("tabMsg").addEventListener("click", () => tab("msg"));
})();
