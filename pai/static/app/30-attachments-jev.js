/* ===================== image attachments ===================== */

// Pending attachments for the next outgoing message: array of data URLs
// (already base64-encoded, ready to drop straight into the request body
// and into an <img src>). Cleared after each send.
let pendingAttachments = [];
let pendingFiles = [];  // uploaded files: {id, name, mime, has_text, note}

function renderAttachmentPreviews() {
  const container = document.getElementById("chatAttachments");
  container.innerHTML = "";
  pendingAttachments.forEach((dataUrl, i) => {
    const thumb = document.createElement("div");
    thumb.className = "attachment-thumb";
    const img = document.createElement("img");
    img.src = dataUrl;
    thumb.appendChild(img);
    const rm = document.createElement("button");
    rm.className = "remove";
    rm.type = "button";
    rm.textContent = "×";
    rm.title = "Убрать";
    rm.addEventListener("click", () => {
      pendingAttachments.splice(i, 1);
      renderAttachmentPreviews();
    });
    thumb.appendChild(rm);
    container.appendChild(thumb);
  });
  pendingFiles.forEach((f, i) => {
    const chip = document.createElement("div");
    chip.className = "file-chip";
    chip.textContent = `📄 ${f.name}${f.has_text ? "" : " · без текста"}`;
    const rm = document.createElement("button");
    rm.className = "remove";
    rm.type = "button";
    rm.textContent = "×";
    rm.title = "Убрать";
    rm.addEventListener("click", () => {
      pendingFiles.splice(i, 1);
      renderAttachmentPreviews();
    });
    chip.appendChild(rm);
    container.appendChild(chip);
  });
}

function appendFileChips(div, files) {
  if (!div || !files || !files.length) return;
  const wrap = document.createElement("div");
  wrap.className = "file-chips";
  files.forEach((f) => {
    const chip = document.createElement("span");
    chip.className = "file-chip";
    chip.textContent = `📄 ${f.name}`;
    wrap.appendChild(chip);
  });
  div.appendChild(wrap);
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

document.getElementById("btnAttachImage").addEventListener("click", () => {
  document.getElementById("chatImageInput").click();
});

document.getElementById("btnAttachFile").addEventListener("click", () => {
  document.getElementById("chatFileInput").click();
});

document.getElementById("chatFileInput").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  e.target.value = "";
  const base = (getApiBase() || "").replace(/\/$/, "");
  for (const file of files) {
    if (file.size > 20 * 1024 * 1024) {
      toast(`${file.name}: больше 20 МБ`, "err");
      continue;
    }
    const fd = new FormData();
    fd.append("file", file, file.name);
    try {
      const res = await fetch(base + "/files", { method: "POST", body: fd });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
        throw new Error(detail);
      }
      const meta = await res.json();
      pendingFiles.push(meta);
      if (meta.note) toast(`${meta.name}: ${meta.note}`);
    } catch (err) {
      toast(`Не удалось загрузить ${file.name}: ${err.message}`, "err");
    }
  }
  renderAttachmentPreviews();
});

document.getElementById("chatImageInput").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  e.target.value = ""; // allow re-selecting the same file next time
  for (const file of files) {
    if (!file.type.startsWith("image/")) continue;
    try {
      const dataUrl = await readFileAsDataUrl(file);
      pendingAttachments.push(dataUrl);
    } catch (err) {
      toast("Не удалось прочитать изображение: " + err.message, "err");
    }
  }
  renderAttachmentPreviews();
});

// Paste an image straight from the clipboard into the chat input.
document.getElementById("chatInput").addEventListener("paste", async (e) => {
  const items = Array.from(e.clipboardData ? e.clipboardData.items : []);
  const imageItems = items.filter((it) => it.type && it.type.startsWith("image/"));
  if (!imageItems.length) return;
  e.preventDefault();
  for (const it of imageItems) {
    const file = it.getAsFile();
    if (!file) continue;
    try {
      const dataUrl = await readFileAsDataUrl(file);
      pendingAttachments.push(dataUrl);
    } catch (err) {
      toast("Не удалось прочитать изображение: " + err.message, "err");
    }
  }
  renderAttachmentPreviews();
});

/* ===================== Jev answer-check switch (settings) ===================== */
async function jevToggleLoad() {
  const box = document.getElementById("jevVerifyToggle");
  const st = document.getElementById("jevVerifyStatus");
  try {
    const d = await api("/laya/settings");
    box.checked = !!d.use_jev_verify;
    box.disabled = !d.available && !d.use_jev_verify;
    st.textContent = d.available ? "" : (d.reason || "недоступно");
  } catch (e) {
    st.textContent = "ошибка: " + e.message;
  }
}
async function jevToggleSave(ev) {
  const box = ev.target;
  const st = document.getElementById("jevVerifyStatus");
  try {
    const d = await api("/laya/settings", { method: "PUT", body: { use_jev_verify: box.checked } });
    box.checked = !!d.use_jev_verify;
    st.textContent = box.checked ? "Jev включён для проверки" : "проверка только LLM";
  } catch (e) {
    box.checked = !box.checked;
    st.textContent = "не сохранено: " + e.message;
  }
}
function jevToggleInit() {
  const box = document.getElementById("jevVerifyToggle");
  if (!box || box.dataset.bound) return;
  box.dataset.bound = "1";
  box.addEventListener("change", jevToggleSave);
  jevToggleLoad();
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", jevToggleInit);
else jevToggleInit();

