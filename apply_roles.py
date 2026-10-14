#!/usr/bin/env python3
"""Разделение моделей по задачам: отдельная модель для ПЛАНИРОВАНИЯ и АНАЛИЗА инструментов
(финальный ответ — основная модель; суммаризация и проверка — свои модели, как раньше).
Настройки -> LLM -> «Модель для планирования пула». Пусто = основная модель.
Anchor-based, all-or-nothing per file, резервная копия <file>.pre-roles. Повторный запуск безопасен.
Запускать из корня проекта (не требует Laya-патчей)."""
import sys
from pathlib import Path

EDITS = {'pai/api_schemas.py': [('schema',
                         '    summarizer_model: str | None = None\n',
                         '    summarizer_model: str | None = None\n    planner_model: str | None = None\n')],
 'pai/llm_config.py': [('field',
                        '    summarizer_model: str = ""\n',
                        '    summarizer_model: str = ""\n'
                        '    # Model for PLANNING and ANALYSIS phases of the tool loop (tool pool choice). Empty = '
                        'main model.\n'
                        '    planner_model: str = ""\n'),
                       ('load',
                        '            summarizer_model=raw.get("summarizer_model", "") or "",\n',
                        '            summarizer_model=raw.get("summarizer_model", "") or "",\n'
                        '            planner_model=raw.get("planner_model", "") or "",\n'),
                       ('effective',
                        '    def effective_summarizer_model(self) -> str:\n',
                        '    def effective_planner_model(self) -> str:\n'
                        '        return self.planner_model or self.model\n'
                        '\n'
                        '    def effective_summarizer_model(self) -> str:\n')],
 'pai/orchestrator/llm_runner.py': [('phase client',
                                     '        llm = self.llm\n        request_log = dict(llm.request_params())\n',
                                     '        # Planning/analysis go to the planner model when one is set; the final '
                                     'answer stays on the main model.\n'
                                     '        llm = (getattr(self.orch, "planner_llm", None) if phase in ("planning", '
                                     '"analysis") else None) or self.llm\n'
                                     '        request_log = dict(llm.request_params())\n')],
 'pai/static/app.js': [('load',
                        '    summarizerModelPicker.state.selectedId = data.summarizer_model || "";\n',
                        '    document.getElementById("llmPlannerModelManual").value = data.planner_model || "";\n'
                        '    summarizerModelPicker.state.selectedId = data.summarizer_model || "";\n'),
                       ('save',
                        '      summarizer_model: document.getElementById("llmSummarizerModelManual").value.trim(),\n',
                        '      summarizer_model: document.getElementById("llmSummarizerModelManual").value.trim(),\n'
                        '      planner_model: document.getElementById("llmPlannerModelManual").value.trim(),\n')],
 'pai/static/index.html': [('planner field',
                            '          <div class="field field-wide">\n'
                            '            <label>Модель для суммаризации <span '
                            'class="opt-tag">опционально</span></label>\n',
                            '          <div class="field field-wide">\n'
                            '            <label>Модель для планирования пула <span '
                            'class="opt-tag">опционально</span></label>\n'
                            '            <div class="model-row">\n'
                            '              <input type="text" id="llmPlannerModelManual" placeholder="пусто = как '
                            'основная модель" />\n'
                            '            </div>\n'
                            '            <div class="field-hint">Вызовы планирования и анализа инструментов (дешёвая '
                            'быстрая модель с tool calling). Финальный ответ остаётся на основной модели.</div>\n'
                            '          </div>\n'
                            '          <div class="field field-wide">\n'
                            '            <label>Модель для суммаризации <span '
                            'class="opt-tag">опционально</span></label>\n')],
 'pai/web/builders.py': [('builder',
                          'def build_verifier_llm_client(config: LLMConfig) -> LLMClient:\n',
                          'def build_planner_llm_client(config: LLMConfig) -> LLMClient | None:\n'
                          '    """None when no separate planner model is set: the orchestrator then uses the main '
                          'model."""\n'
                          '    if not config.planner_model.strip() or config.planner_model.strip() == '
                          'config.model.strip():\n'
                          '        return None\n'
                          '    return build_llm_client(config, model=config.planner_model.strip())\n'
                          '\n'
                          '\n'
                          'def build_verifier_llm_client(config: LLMConfig) -> LLMClient:\n')],
 'pai/web/routes/settings.py': [('import',
                                 '    build_summarizer_llm_client,\n',
                                 '    build_summarizer_llm_client,\n    build_planner_llm_client,\n'),
                                ('startup',
                                 'def register(app: FastAPI) -> None:\n',
                                 'def register(app: FastAPI) -> None:\n'
                                 '    _orch0 = getattr(app.state, "orchestrator", None)\n'
                                 '    if _orch0 is not None:  # planner model is applied from the saved config at '
                                 'startup too\n'
                                 '        _orch0.planner_llm = '
                                 'build_planner_llm_client(app.state.llm_config_store.load())\n'),
                                ('payload',
                                 '            summarizer_model=(payload.summarizer_model if payload.summarizer_model '
                                 'is not None else current.summarizer_model).strip(),\n',
                                 '            summarizer_model=(payload.summarizer_model if payload.summarizer_model '
                                 'is not None else current.summarizer_model).strip(),\n'
                                 '            planner_model=(payload.planner_model if payload.planner_model is not '
                                 'None else current.planner_model).strip(),\n'),
                                ('build',
                                 '        new_verifier_llm = build_verifier_llm_client(new_config)\n',
                                 '        new_verifier_llm = build_verifier_llm_client(new_config)\n'
                                 '        new_planner_llm = build_planner_llm_client(new_config)\n'),
                                ('apply',
                                 '        return {\n            **new_config.masked(),\n',
                                 '        app.state.orchestrator.planner_llm = new_planner_llm\n'
                                 '        return {\n'
                                 '            **new_config.masked(),\n'),
                                ('response',
                                 '            "effective_verify_model": new_config.effective_verify_model(),\n',
                                 '            "effective_verify_model": new_config.effective_verify_model(),\n'
                                 '            "effective_planner_model": new_config.effective_planner_model(),\n')]}
MARK = {'pai/api_schemas.py': 'planner_model',
 'pai/llm_config.py': 'planner_model',
 'pai/orchestrator/llm_runner.py': 'planner_llm',
 'pai/static/app.js': 'llmPlannerModelManual',
 'pai/static/index.html': 'llmPlannerModelManual',
 'pai/web/builders.py': 'build_planner_llm_client',
 'pai/web/routes/settings.py': 'build_planner_llm_client,'}

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
        f.with_name(f.name + ".pre-roles").write_text(s, encoding="utf-8")
        for name, old, new in ed:
            s = s.replace(old, new, 1)
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(ed)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
