#!/usr/bin/env python3
"""СБОРКА 9. Переключатель «Использовать Jev для проверки ответа» (страница настроек, рядом с ризонингом),
 + Jev решает проверку ответа (без LLM при уверенности), + shadow-пара Jev/LLM в логе (verify_pair).
Порядок: apply_laya.py -> apply_laya_ui.py -> apply_laya_pool.py -> apply_laya_verify.py.
Anchor-based, all-or-nothing per file, резервная копия <file>.pre-laya-verify. Повторный запуск безопасен."""
import sys
from pathlib import Path

EDITS = {'pai/orchestrator/dialog.py': [('import',
                                 'from ..laya import get_laya, log_shadow, laya_pool_shadow, last_user_text\n',
                                 'from ..laya import get_laya, log_shadow, laya_pool_shadow, last_user_text, '
                                 'laya_verify_pair\n'),
                                ('gate acts on switch',
                                 '        if (laya.acts and level <= 3 and r["answers"].label == "answers"',
                                 '        if (laya.acts_verify and level <= 3 and r["answers"].label == "answers"'),
                                ('remember jev verdict',
                                 '        log_shadow(self.storage, "verify_gate", space_id, {"strictness": level}, '
                                 'r)\n',
                                 '        log_shadow(self.storage, "verify_gate", space_id, {"strictness": level}, r)\n'
                                 '        self._jev_last = r\n'),
                                ('pair after LLM check',
                                 '            verdict = self._verify_answer(space_id, user_text, final_content)\n',
                                 '            verdict = self._verify_answer(space_id, user_text, final_content)\n'
                                 '            laya_verify_pair(self.storage, space_id, getattr(self, "_jev_last", '
                                 'None), verdict,\n'
                                 '                             _strictness_level(getattr(self, "verify_strictness", '
                                 '3)))\n'
                                 '            self._jev_last = None\n')],
 'pai/static/app.js': [('jev toggle js',
                        '/* ===================== reasoning display ===================== */\n',
                        '/* ===================== Jev answer-check switch (settings) ===================== */\n'
                        'async function jevToggleLoad() {\n'
                        '  const box = document.getElementById("jevVerifyToggle");\n'
                        '  const st = document.getElementById("jevVerifyStatus");\n'
                        '  try {\n'
                        '    const d = await api("/laya/settings");\n'
                        '    box.checked = !!d.use_jev_verify;\n'
                        '    box.disabled = !d.available && !d.use_jev_verify;\n'
                        '    st.textContent = d.available ? "" : (d.reason || "недоступно");\n'
                        '  } catch (e) {\n'
                        '    st.textContent = "ошибка: " + e.message;\n'
                        '  }\n'
                        '}\n'
                        'async function jevToggleSave(ev) {\n'
                        '  const box = ev.target;\n'
                        '  const st = document.getElementById("jevVerifyStatus");\n'
                        '  try {\n'
                        '    const d = await api("/laya/settings", { method: "PUT", body: { use_jev_verify: '
                        'box.checked } });\n'
                        '    box.checked = !!d.use_jev_verify;\n'
                        '    st.textContent = box.checked ? "Jev включён для проверки" : "проверка только LLM";\n'
                        '  } catch (e) {\n'
                        '    box.checked = !box.checked;\n'
                        '    st.textContent = "не сохранено: " + e.message;\n'
                        '  }\n'
                        '}\n'
                        'function jevToggleInit() {\n'
                        '  const box = document.getElementById("jevVerifyToggle");\n'
                        '  if (!box || box.dataset.bound) return;\n'
                        '  box.dataset.bound = "1";\n'
                        '  box.addEventListener("change", jevToggleSave);\n'
                        '  jevToggleLoad();\n'
                        '}\n'
                        'if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", '
                        'jevToggleInit);\n'
                        'else jevToggleInit();\n'
                        '\n'
                        '/* ===================== reasoning display ===================== */\n')],
 'pai/static/index.html': [('toggle block',
                            '        <div class="llm-settings-actions">\n',
                            '        <div class="section-title sub">Проверка ответа (Jev)</div>\n'
                            '        <div class="llm-settings-grid">\n'
                            '          <div class="field">\n'
                            '            <label class="reasoning-toggle" style="font-size:13px;">\n'
                            '              <input type="checkbox" id="jevVerifyToggle" />\n'
                            '              Использовать Jev для проверки ответа\n'
                            '            </label>\n'
                            '            <div class="field-hint">Jev решает «прошло / не прошло» по существу и языку. '
                            'Если уверен, LLM-проверка пропускается. Если не уверен, проверяет LLM. Текст сообщений '
                            'уходит в OpenRouter.</div>\n'
                            '            <div class="field-hint" id="jevVerifyStatus"></div>\n'
                            '          </div>\n'
                            '        </div>\n'
                            '        <div class="llm-settings-actions">\n')],
 'pai/web/routes/system.py': [('settings routes',
                               '    @app.get("/laya/status")\n',
                               '    # Jev verify switch (settings page). Persisted in <data_dir>/laya_settings.json, '
                               'applied to the live client at once.\n'
                               '    from ...laya import get_laya, laya_settings_path, load_laya_settings, '
                               'save_laya_settings\n'
                               '\n'
                               '    _laya_settings_file = laya_settings_path(app.state.llm_config_store.path.parent)\n'
                               '    get_laya().use_for_verify = '
                               'bool(load_laya_settings(_laya_settings_file).get("use_jev_verify", False))\n'
                               '\n'
                               '    @app.get("/laya/settings")\n'
                               '    def laya_settings_get():\n'
                               '        lay = get_laya()\n'
                               '        has_key = bool(lay._key()) if getattr(lay, "remote", False) else True\n'
                               '        reason = ""\n'
                               '        if not lay.enabled:\n'
                               '            reason = "выключено (PAI_LAYA_MODE=off)"\n'
                               '        elif not has_key:\n'
                               '            reason = "нет API-ключа: сохраните OpenRouter-ключ в настройках LLM"\n'
                               '        elif not lay.ready:\n'
                               '            reason = "Laya ещё не готова"\n'
                               '        return {"use_jev_verify": lay.use_for_verify, "available": not reason, '
                               '"reason": reason,\n'
                               '                "provider": "jev" if getattr(lay, "remote", False) else "laya"}\n'
                               '\n'
                               '    @app.put("/laya/settings")\n'
                               '    def laya_settings_put(body: dict):\n'
                               '        val = bool(body.get("use_jev_verify", False))\n'
                               '        get_laya().use_for_verify = val\n'
                               '        save_laya_settings(_laya_settings_file, {"use_jev_verify": val})\n'
                               '        return laya_settings_get()\n'
                               '\n'
                               '    @app.get("/laya/status")\n')]}
MARK = {'pai/orchestrator/dialog.py': 'laya_verify_pair',
 'pai/static/app.js': 'jevToggleInit',
 'pai/static/index.html': 'jevVerifyToggle',
 'pai/web/routes/system.py': 'laya/settings'}

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
        f.with_name(f.name + ".pre-laya-verify").write_text(s, encoding="utf-8")
        for name, old, new in ed:
            s = s.replace(old, new, 1)
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(ed)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
