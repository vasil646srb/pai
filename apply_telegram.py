#!/usr/bin/env python3
"""СБОРКА 11. Telegram: публичный канал (пост через подтверждение) и приватный (сразу).
Страница «Telegram»: токен бота (в секретах), chat_id и режим каналов, проверка прав, очередь подтверждений.
Инструмент telegram_post для персон (включить в Персонах). Нужны pai/telegram.py (уже в архиве).
Запускать из корня проекта. Anchor-based, all-or-nothing, резервная копия <file>.pre-telegram. Повторный запуск безопасен."""
import sys
from pathlib import Path

EDITS = {'pai/static/app.js': [('telegram js',
                        '/* ===================== reasoning display ===================== */\n',
                        '/* ===================== Telegram page ===================== */\n'
                        'function tgChannel(d, key) { return (d.channels || []).find((c) => c.key === key) || { '
                        'chat_id: "", mode: "" }; }\n'
                        'function tgFill(d) {\n'
                        '  document.getElementById("tgTokenHint").textContent = d.token_set ? `сохранён: '
                        '${d.token_preview}` : "токен не задан";\n'
                        '  const pub = tgChannel(d, "public"), prv = tgChannel(d, "private");\n'
                        '  document.getElementById("tgPublicChat").value = pub.chat_id || "";\n'
                        '  document.getElementById("tgPublicMode").value = pub.mode || "approve";\n'
                        '  document.getElementById("tgPrivateChat").value = prv.chat_id || "";\n'
                        '  document.getElementById("tgPrivateMode").value = prv.mode || "auto";\n'
                        '}\n'
                        'async function tgLoad() {\n'
                        '  try { tgFill(await api("/telegram/settings")); }\n'
                        '  catch (e) { document.getElementById("tgStatus").textContent = "ошибка: " + e.message; }\n'
                        '}\n'
                        'async function tgSave() {\n'
                        '  const st = document.getElementById("tgStatus");\n'
                        '  const body = {\n'
                        '    token: document.getElementById("tgToken").value.trim(),\n'
                        '    channels: [\n'
                        '      { key: "public", chat_id: document.getElementById("tgPublicChat").value.trim(), mode: '
                        'document.getElementById("tgPublicMode").value },\n'
                        '      { key: "private", chat_id: document.getElementById("tgPrivateChat").value.trim(), mode: '
                        'document.getElementById("tgPrivateMode").value },\n'
                        '    ],\n'
                        '  };\n'
                        '  try {\n'
                        '    const d = await api("/telegram/settings", { method: "PUT", body });\n'
                        '    document.getElementById("tgToken").value = "";\n'
                        '    tgFill(d); st.textContent = "сохранено";\n'
                        '  } catch (e) { st.textContent = "не сохранено: " + e.message; }\n'
                        '}\n'
                        'async function tgCheck() {\n'
                        '  const out = document.getElementById("tgCheckOut");\n'
                        '  out.textContent = "проверяю…";\n'
                        '  try {\n'
                        '    const d = await api("/telegram/check", { method: "POST" });\n'
                        '    out.textContent = `бот @${d.bot}\\n` + d.channels.map((c) => `${c.ok ? "✓" : "✗"} '
                        '${c.title}: ${c.detail}`).join("\\n");\n'
                        '  } catch (e) { out.textContent = "ошибка: " + e.message; }\n'
                        '}\n'
                        'function tgItemHtml(i, withButtons) {\n'
                        '  const when = new Date((i.created || 0) * 1000).toLocaleString("ru-RU");\n'
                        '  const btns = withButtons\n'
                        '    ? `<div class="llm-settings-actions"><button class="btn primary sm" data-tg="approve" '
                        'data-id="${esc(i.id)}">Опубликовать</button>`\n'
                        '      + `<button class="btn ghost sm" data-tg="reject" '
                        'data-id="${esc(i.id)}">Отклонить</button></div>`\n'
                        '    : "";\n'
                        '  const status = withButtons ? "" : ` · <span class="mono">${esc(i.status)}</span>` + '
                        '(i.error ? ` · ${esc(i.error)}` : "");\n'
                        '  return `<div class="card" style="margin:8px 0;"><div class="al-note">${when} · '
                        '${esc(i.channel)}${status}</div>`\n'
                        '    + `<pre class="mono al-pre">${esc(i.text)}</pre>${btns}</div>`;\n'
                        '}\n'
                        'async function tgQueueLoad() {\n'
                        '  const q = document.getElementById("tgQueue"), h = document.getElementById("tgHistory");\n'
                        '  try {\n'
                        '    const d = await api("/telegram/queue");\n'
                        '    q.innerHTML = d.pending.length ? d.pending.map((i) => tgItemHtml(i, true)).join("") : '
                        '`<div class="empty-state">нет ожидающих постов</div>`;\n'
                        '    h.innerHTML = d.history.length ? d.history.map((i) => tgItemHtml(i, false)).join("") : '
                        '`<div class="empty-state">пусто</div>`;\n'
                        '  } catch (e) { q.innerHTML = `<div class="empty-state">ошибка: ${esc(e.message)}</div>`; }\n'
                        '}\n'
                        'async function tgQueueAct(ev) {\n'
                        '  const b = ev.target.closest("button[data-tg]");\n'
                        '  if (!b) return;\n'
                        '  try { await api(`/telegram/queue/${encodeURIComponent(b.dataset.id)}/${b.dataset.tg}`, { '
                        'method: "POST" }); }\n'
                        '  catch (e) { alert("не выполнено: " + e.message); }\n'
                        '  tgQueueLoad();\n'
                        '}\n'
                        'function tgInit() {\n'
                        '  if (!document.getElementById("tgSave") || document.getElementById("tgSave").dataset.bound) '
                        'return;\n'
                        '  document.getElementById("tgSave").dataset.bound = "1";\n'
                        '  document.getElementById("tgSave").addEventListener("click", tgSave);\n'
                        '  document.getElementById("tgCheck").addEventListener("click", tgCheck);\n'
                        '  document.getElementById("tgQueueRefresh").addEventListener("click", tgQueueLoad);\n'
                        '  document.getElementById("tgQueue").addEventListener("click", tgQueueAct);\n'
                        '  tgLoad(); tgQueueLoad();\n'
                        '}\n'
                        'if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", '
                        'tgInit);\n'
                        'else tgInit();\n'
                        '\n'
                        '/* ===================== reasoning display ===================== */\n')],
 'pai/static/index.html': [('nav item',
                            '    <div class="nav-item" data-view="settings">',
                            '    <div class="nav-item" data-view="telegram"><span class="ico">▸</span>Telegram</div>\n'
                            '    <div class="nav-item" data-view="settings">'),
                           ('view',
                            '    <section class="view" id="view-skills">\n',
                            '    <section class="view" id="view-telegram">\n'
                            '      <div class="page-head"><h1>Telegram</h1>\n'
                            '        <p class="page-sub">Публикации бота в каналы. Публичный канал: пост уходит только '
                            'после вашего подтверждения. Приватный: сразу.</p></div>\n'
                            '      <div class="card">\n'
                            '        <div class="field"><label>Токен бота (BotFather)</label>\n'
                            '          <input type="password" id="tgToken" placeholder="вставьте новый токен, чтобы '
                            'заменить" autocomplete="off" />\n'
                            '          <div class="field-hint" id="tgTokenHint"></div></div>\n'
                            '        <div class="field"><label>Публичный канал (@username или -100…)</label>\n'
                            '          <input type="text" id="tgPublicChat" placeholder="@my_channel" /></div>\n'
                            '        <div class="field"><label>Публичный: режим</label>\n'
                            '          <select id="tgPublicMode"><option value="approve">подтверждение</option><option '
                            'value="auto">сразу</option></select></div>\n'
                            '        <div class="field"><label>Приватный канал (@username или -100…)</label>\n'
                            '          <input type="text" id="tgPrivateChat" placeholder="-100…" /></div>\n'
                            '        <div class="field"><label>Приватный: режим</label>\n'
                            '          <select id="tgPrivateMode"><option value="auto">сразу</option><option '
                            'value="approve">подтверждение</option></select></div>\n'
                            '        <div class="llm-settings-actions">\n'
                            '          <button class="btn primary" id="tgSave">Сохранить</button>\n'
                            '          <button class="btn ghost" id="tgCheck">Проверить бота и каналы</button></div>\n'
                            '        <p class="page-sub" id="tgStatus"></p>\n'
                            '        <pre class="mono al-pre" id="tgCheckOut"></pre>\n'
                            '      </div>\n'
                            '      <div class="card">\n'
                            '        <div class="section-title sub">Очередь на подтверждение</div>\n'
                            '        <div id="tgQueue"><div class="empty-state">загрузка…</div></div>\n'
                            '        <button class="btn ghost sm" id="tgQueueRefresh">Обновить</button>\n'
                            '        <div class="section-title sub" style="margin-top:14px;">История</div>\n'
                            '        <div id="tgHistory"></div>\n'
                            '      </div>\n'
                            '    </section>\n'
                            '\n'
                            '    <section class="view" id="view-skills">\n')],
 'pai/tools/dispatcher.py': [('tool handler',
                              '    def dispatch(self, name, arguments, persona=None):\n',
                              '    def _tool_telegram_post(self, arguments, persona):\n'
                              '        from ..telegram import TelegramError, post_from_tool\n'
                              '        try:\n'
                              '            return post_from_tool(str(arguments.get("channel", "")), '
                              'str(arguments.get("text", "")))\n'
                              '        except TelegramError as e:\n'
                              '            return {"tool_failed": True, "error": str(e), "tool": "telegram_post"}\n'
                              '\n'
                              '    def dispatch(self, name, arguments, persona=None):\n')],
 'pai/tools/schemas.py': [('tool schema',
                           'TOOL_SCHEMAS.extend(PERSONA_TOOL_SCHEMAS)\n',
                           'TOOL_SCHEMAS.extend(PERSONA_TOOL_SCHEMAS)\n'
                           'TOOL_SCHEMAS.append({\n'
                           '    "type": "function",\n'
                           '    "function": {\n'
                           '        "name": "telegram_post",\n'
                           '        "description": "Publish a text post to one of the owner\'s Telegram channels. '
                           '\'private\' is the owner\'s "\n'
                           '                        "private channel and goes out at once. \'public\' is the public '
                           'channel: the post is queued "\n'
                           '                        "and goes out only after the owner approves it, so tell the owner '
                           'it is waiting. "\n'
                           '                        "Plain text, up to 4000 characters.",\n'
                           '        "parameters": {"type": "object", "properties": {\n'
                           '            "channel": {"type": "string", "enum": ["public", "private"], "description": '
                           '"Which channel."},\n'
                           '            "text": {"type": "string", "description": "Post text."},\n'
                           '        }, "required": ["channel", "text"]},\n'
                           '    },\n'
                           '})\n')],
 'pai/web/routes/settings.py': [('telegram routes',
                                 '    @app.get("/settings/llm")\n',
                                 '    from ...telegram import TelegramError, get_services, init_services\n'
                                 '\n'
                                 '    init_services(app.state.secret_store, app.state.llm_config_store.path.parent)\n'
                                 '\n'
                                 '    def _tg(fn):\n'
                                 '        try:\n'
                                 '            return fn()\n'
                                 '        except TelegramError as e:\n'
                                 '            raise HTTPException(400, str(e)) from e\n'
                                 '\n'
                                 '    @app.get("/telegram/settings")\n'
                                 '    def tg_settings_get():\n'
                                 '        return get_services().public_view()\n'
                                 '\n'
                                 '    @app.put("/telegram/settings")\n'
                                 '    def tg_settings_put(body: dict):\n'
                                 '        def run():\n'
                                 '            s = get_services()\n'
                                 '            if body.get("token"):\n'
                                 '                s.set_token(str(body["token"]))\n'
                                 '            if isinstance(body.get("channels"), list):\n'
                                 '                s.save_channels(body["channels"])\n'
                                 '            return s.public_view()\n'
                                 '        return _tg(run)\n'
                                 '\n'
                                 '    @app.post("/telegram/check")\n'
                                 '    def tg_check():\n'
                                 '        return _tg(lambda: get_services().check())\n'
                                 '\n'
                                 '    @app.get("/telegram/queue")\n'
                                 '    def tg_queue():\n'
                                 '        return get_services().queue_view()\n'
                                 '\n'
                                 '    @app.post("/telegram/queue/{item_id}/{action}")\n'
                                 '    def tg_queue_act(item_id: str, action: str):\n'
                                 '        if action not in ("approve", "reject"):\n'
                                 '            raise HTTPException(404, "неизвестное действие")\n'
                                 '        return _tg(lambda: get_services()._act(item_id, approve=(action == '
                                 '"approve")))\n'
                                 '\n'
                                 '    @app.get("/settings/llm")\n')]}
MARK = {'pai/static/app.js': 'tgInit',
 'pai/static/index.html': 'data-view="telegram"',
 'pai/tools/dispatcher.py': '_tool_telegram_post',
 'pai/tools/schemas.py': 'telegram_post',
 'pai/web/routes/settings.py': '/telegram/settings'}

def main(root="."):
    root = Path(root); bad = 0
    for rel, ed in EDITS.items():
        f = root / rel
        if not f.is_file():
            print(f"SKIP  {rel}: no such file"); bad += 1; continue
        s = f.read_text(encoding="utf-8")
        if MARK[rel] in s:
            print(f"OK    {rel}: already patched"); continue
        miss = [name for name, old, new in ed if s.count(old) != 1]
        if miss:
            print(f"FAIL  {rel}: anchors not found exactly once: {miss} (file untouched)"); bad += 1; continue
        f.with_name(f.name + ".pre-telegram").write_text(s, encoding="utf-8")
        for name, old, new in ed:
            s = s.replace(old, new, 1)
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(ed)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
