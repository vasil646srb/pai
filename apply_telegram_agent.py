#!/usr/bin/env python3
"""СБОРКА 13. Telegram: фоновая обработка новых сообщений группы.
Jev классифицирует сообщение (нужен ли ответ, спам ли). Если нужен ответ, персона brain пишет его,
ответ идёт в группу через очередь подтверждения (или сразу, если режим группы «авто»).
Страница: переключатель автоответов, «Обработать сейчас», форма «Написать в канал или группу».
Требует сборки 12 и файлов pai/telegram.py + pai/telegram_agent.py из архива.
Запускать из корня проекта. Anchor-based, резервная копия <file>.pre-telegram-agent. Повторный запуск безопасен."""
import sys
from pathlib import Path

EDITS = {'pai/static/app.js': [('save auto',
                        '    token: document.getElementById("tgToken").value.trim(),\n',
                        '    token: document.getElementById("tgToken").value.trim(),\n'
                        '    group_auto_reply: document.getElementById("tgAutoReply").checked,\n'),
                       ('fill auto',
                        '  document.getElementById("tgGroupMode").value = grp.mode || "approve";\n',
                        '  document.getElementById("tgGroupMode").value = grp.mode || "approve";\n'
                        '  document.getElementById("tgAutoReply").checked = !!d.group_auto_reply;\n'),
                       ('send+process js',
                        '/* ===================== reasoning display ===================== */\n',
                        'async function tgSendManual() {\n'
                        '  const st = document.getElementById("tgSendStatus");\n'
                        '  const channel = document.getElementById("tgSendChannel").value;\n'
                        '  const text = document.getElementById("tgSendText").value.trim();\n'
                        '  if (!text) { st.textContent = "текст пустой"; return; }\n'
                        '  try {\n'
                        '    const d = await api("/telegram/send", { method: "POST", body: { channel, text } });\n'
                        '    st.textContent = d.status === "sent" ? "опубликовано"\n'
                        '      : d.status === "pending" ? "в очереди на подтверждение"\n'
                        '      : `ошибка: ${d.error || d.status}`;\n'
                        '    if (d.status !== "failed") document.getElementById("tgSendText").value = "";\n'
                        '    tgQueueLoad();\n'
                        '  } catch (e) { st.textContent = "не отправлено: " + e.message; }\n'
                        '}\n'
                        'async function tgProcessNow() {\n'
                        '  const st = document.getElementById("tgStatus");\n'
                        '  try {\n'
                        '    const d = await api("/telegram/process", { method: "POST" });\n'
                        '    st.textContent = `обработано: ${d.processed} из ${d.checked || 0}${d.reason ? " (" + '
                        'd.reason + ")" : ""}`;\n'
                        '  } catch (e) { st.textContent = "не обработано: " + e.message; }\n'
                        '  tgInboxLoad(); tgQueueLoad();\n'
                        '}\n'
                        '\n'
                        '/* ===================== reasoning display ===================== */\n'),
                       ('bind send+process',
                        '  tgLoad(); tgQueueLoad(); tgInboxLoad();\n',
                        '  tgLoad(); tgQueueLoad(); tgInboxLoad();\n'
                        '  document.getElementById("tgSend").addEventListener("click", tgSendManual);\n'
                        '  document.getElementById("tgProcess").addEventListener("click", tgProcessNow);\n')],
 'pai/static/index.html': [('auto reply checkbox',
                            '        <div class="llm-settings-actions">\n'
                            '          <button class="btn primary" id="tgSave">',
                            '        <div class="field"><label class="reasoning-toggle" style="font-size:13px;">\n'
                            '          <input type="checkbox" id="tgAutoReply" /> Отвечать в группе '
                            'автоматически</label>\n'
                            '          <div class="field-hint">Новые сообщения классифицирует Jev. Если нужен ответ, '
                            'пишет персона brain. Ответ идёт в группу через очередь, если режим группы '
                            '«подтверждение».</div></div>\n'
                            '        <div class="llm-settings-actions">\n'
                            '          <button class="btn primary" id="tgSave">'),
                           ('process button',
                            '        <div class="llm-settings-actions"><button class="btn ghost sm" '
                            'id="tgPoll">Проверить новые сообщения</button></div>\n',
                            '        <div class="llm-settings-actions"><button class="btn ghost sm" '
                            'id="tgPoll">Проверить новые сообщения</button>\n'
                            '          <button class="btn ghost sm" id="tgProcess">Обработать сейчас</button></div>\n'),
                           ('send card',
                            '      <div class="card">\n'
                            '        <div class="section-title sub">Новые сообщения (группа и каналы для '
                            'чтения)</div>\n',
                            '      <div class="card">\n'
                            '        <div class="section-title sub">Написать в канал или группу</div>\n'
                            '        <div class="field"><label>Куда</label>\n'
                            '          <select id="tgSendChannel"><option value="private">приватный '
                            '(сразу)</option><option value="public">публичный (через подтверждение)</option><option '
                            'value="group">группа (через подтверждение)</option></select></div>\n'
                            '        <div class="field"><label>Текст</label><textarea id="tgSendText" rows="4" '
                            'placeholder="до 4000 символов"></textarea></div>\n'
                            '        <div class="llm-settings-actions"><button class="btn primary" '
                            'id="tgSend">Отправить</button></div>\n'
                            '        <p class="page-sub" id="tgSendStatus"></p>\n'
                            '      </div>\n'
                            '      <div class="card">\n'
                            '        <div class="section-title sub">Новые сообщения (группа и каналы для '
                            'чтения)</div>\n')],
 'pai/web/routes/settings.py': [('wiring',
                                 '    init_services(app.state.secret_store, app.state.llm_config_store.path.parent)\n',
                                 '    init_services(app.state.secret_store, app.state.llm_config_store.path.parent)\n'
                                 '\n'
                                 '    from ...telegram import send_manual, set_classifier, set_responder\n'
                                 '    from ...telegram_agent import classify, make_responder\n'
                                 '\n'
                                 '    set_classifier(classify)  # Jev: does a group message need an answer?\n'
                                 '    set_responder(make_responder(app.state.orchestrator))  # persona brain writes '
                                 'the answer\n'),
                                ('auto flag',
                                 '            if isinstance(body.get("channels"), list):\n'
                                 '                s.save_channels(body["channels"])\n',
                                 '            if isinstance(body.get("channels"), list):\n'
                                 '                s.save_channels(body["channels"])\n'
                                 '            if "group_auto_reply" in body:\n'
                                 '                s.set_auto_reply(bool(body["group_auto_reply"]))\n'),
                                ('send+process routes',
                                 '    @app.get("/telegram/inbox")\n',
                                 '    @app.post("/telegram/send")\n'
                                 '    def tg_send(body: dict):\n'
                                 '        return _tg(lambda: send_manual(str(body.get("channel", "")), '
                                 'str(body.get("text", ""))))\n'
                                 '\n'
                                 '    @app.post("/telegram/process")\n'
                                 '    def tg_process():\n'
                                 '        return _tg(lambda: get_services().process_group())\n'
                                 '\n'
                                 '    @app.get("/telegram/inbox")\n')]}
MARK = {'pai/static/app.js': 'tgProcessNow',
 'pai/static/index.html': 'id="tgAutoReply"',
 'pai/web/routes/settings.py': 'telegram_agent'}

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
        f.with_name(f.name + ".pre-telegram-agent").write_text(s, encoding="utf-8")
        for name, old, new in ed:
            s = s.replace(old, new, 1)
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(ed)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
