#!/usr/bin/env python3
"""СБОРКА 12. Telegram: группа (читать и отвечать) + проверка новых сообщений.
- группа как третий канал (настройки, режим публикации, privacy mode в инструкции);
- фоновый опрос getUpdates (PAI_TELEGRAM_POLL_SEC, по умолчанию 60; 0 = выкл) и кнопка «Проверить новые сообщения»;
- инструмент telegram_read (новые сообщения, помечаются прочитанными); telegram_post принимает канал group.
Требует СБОРКИ 11 (apply_telegram.py уже применён). pai/telegram.py из архива заменяет v11.
Запускать из корня проекта. Anchor-based, резервная копия <file>.pre-telegram-groups. Повторный запуск безопасен."""
import sys
from pathlib import Path

EDITS = {'pai/static/app.js': [('save group',
                        '      { key: "private", chat_id: document.getElementById("tgPrivateChat").value.trim(), mode: '
                        'document.getElementById("tgPrivateMode").value },\n',
                        '      { key: "private", chat_id: document.getElementById("tgPrivateChat").value.trim(), mode: '
                        'document.getElementById("tgPrivateMode").value },\n'
                        '      { key: "group", chat_id: document.getElementById("tgGroupChat").value.trim(), mode: '
                        'document.getElementById("tgGroupMode").value },\n'),
                       ('fill group',
                        '  document.getElementById("tgPrivateMode").value = prv.mode || "auto";\n',
                        '  document.getElementById("tgPrivateMode").value = prv.mode || "auto";\n'
                        '  const grp = tgChannel(d, "group");\n'
                        '  document.getElementById("tgGroupChat").value = grp.chat_id || "";\n'
                        '  document.getElementById("tgGroupMode").value = grp.mode || "approve";\n'),
                       ('inbox js',
                        '/* ===================== reasoning display ===================== */\n',
                        'async function tgInboxLoad() {\n'
                        '  const box = document.getElementById("tgInbox");\n'
                        '  try {\n'
                        '    const d = await api("/telegram/inbox");\n'
                        '    box.innerHTML = d.recent.length\n'
                        '      ? d.recent.map((m) => `<div class="card" style="margin:8px 0;"><div '
                        'class="al-note">${new Date((m.date || 0) * 1000).toLocaleString("ru-RU")} · ${esc(m.channel)} '
                        '· ${esc(m.from || "?")}${m.read ? "" : " · <b>новое</b>"}</div><pre class="mono '
                        'al-pre">${esc(m.text)}</pre></div>`).join("")\n'
                        '      : `<div class="empty-state">сообщений нет</div>`;\n'
                        '  } catch (e) { box.innerHTML = `<div class="empty-state">ошибка: ${esc(e.message)}</div>`; '
                        '}\n'
                        '}\n'
                        'async function tgPollNow() {\n'
                        '  const st = document.getElementById("tgStatus");\n'
                        '  try { const d = await api("/telegram/poll", { method: "POST" }); st.textContent = `новых '
                        'сообщений: ${d.new}`; }\n'
                        '  catch (e) { st.textContent = "не проверено: " + e.message; }\n'
                        '  tgInboxLoad();\n'
                        '}\n'
                        '\n'
                        '/* ===================== reasoning display ===================== */\n'),
                       ('bind inbox',
                        '  tgLoad(); tgQueueLoad();\n',
                        '  tgLoad(); tgQueueLoad(); tgInboxLoad();\n'
                        '  document.getElementById("tgPoll").addEventListener("click", tgPollNow);\n')],
 'pai/static/index.html': [('group fields',
                            '        <div class="llm-settings-actions">\n'
                            '          <button class="btn primary" id="tgSave">',
                            '        <div class="field"><label>Группа (@username или -100…)</label>\n'
                            '          <input type="text" id="tgGroupChat" placeholder="-100…" /></div>\n'
                            '        <div class="field"><label>Группа: публикации</label>\n'
                            '          <select id="tgGroupMode"><option value="approve">подтверждение</option><option '
                            'value="auto">сразу</option></select>\n'
                            '          <div class="field-hint">Бот читает сообщения группы. Чтобы видеть все '
                            'сообщения, а не только команды и упоминания, отключите privacy mode в @BotFather '
                            '(/setprivacy → Disable) и добавьте бота в группу заново.</div></div>\n'
                            '        <div class="llm-settings-actions">\n'
                            '          <button class="btn primary" id="tgSave">'),
                           ('inbox card',
                            '      <div class="card">\n'
                            '        <div class="section-title sub">Очередь на подтверждение</div>\n',
                            '      <div class="card">\n'
                            '        <div class="section-title sub">Новые сообщения (группа и каналы для '
                            'чтения)</div>\n'
                            '        <div class="llm-settings-actions"><button class="btn ghost sm" '
                            'id="tgPoll">Проверить новые сообщения</button></div>\n'
                            '        <div id="tgInbox"><div class="empty-state">загрузка…</div></div>\n'
                            '      </div>\n'
                            '      <div class="card">\n'
                            '        <div class="section-title sub">Очередь на подтверждение</div>\n')],
 'pai/tools/dispatcher.py': [('read handler',
                              '    def dispatch(self, name, arguments, persona=None):\n',
                              '    def _tool_telegram_read(self, arguments, persona):\n'
                              '        from ..telegram import TelegramError, read_from_tool\n'
                              '        try:\n'
                              '            return read_from_tool(str(arguments.get("channel", "all") or "all"))\n'
                              '        except TelegramError as e:\n'
                              '            return {"tool_failed": True, "error": str(e), "tool": "telegram_read"}\n'
                              '\n'
                              '    def dispatch(self, name, arguments, persona=None):\n')],
 'pai/tools/schemas.py': [('post enum group',
                           '"enum": ["public", "private"], "description": "Which channel."',
                           '"enum": ["public", "private", "group"], "description": "Which channel."'),
                          ('read tool',
                           'TOOL_SCHEMAS.extend(PERSONA_TOOL_SCHEMAS)\n',
                           'TOOL_SCHEMAS.extend(PERSONA_TOOL_SCHEMAS)\n'
                           'TOOL_SCHEMAS.append({\n'
                           '    "type": "function",\n'
                           '    "function": {\n'
                           '        "name": "telegram_read",\n'
                           '        "description": "Check the owner\'s Telegram group and reading channels for new '
                           'messages. Returns the "\n'
                           '                        "unread ones and marks them read. Use it when asked about new '
                           'messages or before answering in the group.",\n'
                           '        "parameters": {"type": "object", "properties": {\n'
                           '            "channel": {"type": "string", "enum": ["all", "group", "public", "private"], '
                           '"description": "Which chat to check."},\n'
                           '        }, "required": []},\n'
                           '    },\n'
                           '})\n')],
 'pai/web/routes/settings.py': [('inbox routes',
                                 '    @app.get("/settings/llm")\n',
                                 '    @app.get("/telegram/inbox")\n'
                                 '    def tg_inbox():\n'
                                 '        return get_services().inbox_view()\n'
                                 '\n'
                                 '    @app.post("/telegram/poll")\n'
                                 '    def tg_poll():\n'
                                 '        return _tg(lambda: get_services().poll())\n'
                                 '\n'
                                 '    @app.get("/settings/llm")\n')]}
MARK = {'pai/static/app.js': 'tgInboxLoad',
 'pai/static/index.html': 'id="tgInbox"',
 'pai/tools/dispatcher.py': '_tool_telegram_read',
 'pai/tools/schemas.py': 'telegram_read',
 'pai/web/routes/settings.py': '/telegram/inbox'}

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
        f.with_name(f.name + ".pre-telegram-groups").write_text(s, encoding="utf-8")
        for name, old, new in ed:
            s = s.replace(old, new, 1)
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(ed)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
