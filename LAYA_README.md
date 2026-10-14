# Laya в PAI — СБОРКА 9 — установка поверх ТЕКУЩЕГО проекта

Из корня проекта (где лежит pai/):
    unzip -o laya_integration_v2.zip        # кладёт ТОЛЬКО новые файлы: pai/laya.py, pai/laya_runtime.py, tests_laya/, apply_laya.py
    python apply_laya.py .                  # правит ваши текущие app.py, dialog.py, guest.py, system.py на месте

apply_laya.py: правит по точным якорям, ничего не затирает; перед правкой делает <файл>.pre-laya;
если якорь не найден — файл не трогает и пишет какой. Повторный запуск безопасен.

Env (необязательны): PAI_LAYA_MODE=shadow|on|off (по умолчанию shadow), PAI_LAYA_URL=http://127.0.0.1:8090,
PAI_LAYA_AUTOSTART=1, PAI_LAYA_AUTOINSTALL=1, PAI_LAYA_VERSION=1.3.3, PAI_LAYA_DIR, PAI_LAYA_CLI, PAI_LAYA_TIMEOUT=2.
Без API-ключа, сервер только на 127.0.0.1. Лог: <data_dir>/laya.log, статус: GET /laya/status.
Тесты: python -m pytest -q tests_laya
Shadow-лог: SELECT datetime(ts,'unixepoch'), name, response FROM action_log WHERE kind='laya' ORDER BY ts DESC LIMIT 50;

## v3: busy / бэкенд / таймауты
- Медленный сервер (wasm) больше не считается «чужим»: состояние `busy`, без мигания ready/failed.
- PAI_LAYA_BACKEND=wasm  — бэкенд для запускаемого проектом сервера (нужен на Termux, если native не работает).
- PAI_LAYA_PROBE_TIMEOUT=60  — сколько ждать ответа при проверке (с).
- PAI_LAYA_POLL=30  — период проверки (с).
- Клиентский PAI_LAYA_TIMEOUT для wasm поставьте 10 (по умолчанию 2 — мало для wasm).

## v4: строки Laya в «Лог действий»
После apply_laya.py:  python apply_laya_ui.py .
- фильтр «laya», бейдж, краткая строка (метка и вероятность), раскрытие: запрос, вероятности по каждому вопросу, отступ, confidence, задержка, факт старого пути.
- laya.py пишет latency_seconds в action_log.

## v5: диагностика «почему нет записей laya»
GET /laya/status теперь содержит:
- breaker_open, timeout
- stats: calls (ушло в Laya), ok, fail, skipped_not_ready, last_ok, last_error, last_error_at
Записи в action_log пишутся только при успешном ответе. Ошибки видны в stats.last_error и в логе приложения.
Ошибка записи в action_log теперь WARNING (раньше глушилась на DEBUG).

Когда запросов нет (stats.calls=0), Laya вызывается только:
- verify_gate: если включена проверка ответа (PAI_VERIFY не 0) и настроена проверочная модель;
- announce: короткий ответ-обещание без вызова инструмента;
- guest_guard: только в /guest, не в /chat/stream.

## v6: провайдер Jev (облако, OpenRouter)
Переключение: PAI_DECISION_PROVIDER=jev (по умолчанию laya — локальный сервер).
Нужны: OPENROUTER_API_KEY (или PAI_JEV_API_KEY). Без ключа — выключено.
Опции: PAI_JEV_MODEL=jev-1.13, PAI_JEV_URL=https://openrouter.ai/api, PAI_LAYA_TIMEOUT (для Jev по умолчанию 10).
Локальный сервер не запускается. Текст сообщений уходит в OpenRouter.
Все вопросы идут как choice, criteria -> {label: label}. Ответ: если нет probabilities, вероятность выбранной метки = 1.0.
Формат ответа Jev для choice проверяется первым живым вызовом: смотрите /laya/status -> stats.last_error.

## СБОРКА 7: Jev по умолчанию
- PAI_DECISION_PROVIDER по умолчанию jev. Для локальной Laya: PAI_DECISION_PROVIDER=laya.
- Ключ Jev: PAI_JEV_API_KEY, иначе OpenRouter-ключ из настроек LLM в веб-интерфейсе (<data_dir>/llm_config.json).
  Ключ читается при каждом вызове: смена в UI действует сразу. Работает, если LLM-провайдер в настройках — OpenRouter.
- Без ключа: stats.skipped_not_ready и last_error «нет API-ключа».
- Тесты: 22 шт. (python -m pytest -q tests_laya)

## СБОРКА 8: Jev как shadow-голос за пулы инструментов
Порядок: unzip -> python apply_laya.py . -> python apply_laya_ui.py . -> python apply_laya_pool.py .
- На каждом сообщении (итерация 0 планировщика) Jev отвечает yes/no по каждому инструменту (до 40).
- Вызов в фоновом потоке, задержка ответа не меняется.
- Запись в action_log: kind='laya', name='pool_plan'. В response.actual: llm_pool, jev_yes, match, truncated.
- Решения планировщика Jev не меняет. Режим on для этого шага не реализован.
- Вызовы идут в OpenRouter, в каждом запросе описания инструментов и текст сообщения.
- Тесты: 25 шт.

## СБОРКА 9: Jev для проверки ответа (вариант B) + переключатель в настройках
Порядок: unzip -> apply_laya.py -> apply_laya_ui.py -> apply_laya_pool.py -> apply_laya_verify.py
- Настройки -> блок ризонинга -> «Использовать Jev для проверки ответа». Сохраняется сразу, без кнопки «Сохранить»,
  в <data_dir>/laya_settings.json, применяется к работающему процессу.
- Когда переключатель включён, Jev решает «прошло / не прошло» (по существу и языку). Уверенный pass пропускает LLM-проверку.
  Иначе вызывается LLM-проверка, как раньше (она же пишет fix_plan и suggested_replies).
- Режим PAI_LAYA_MODE=on тоже включает это поведение; переключатель нужен, чтобы включить только проверку ответа.
- Пара в логе: kind='laya', name='verify_pair': actual.llm_status, jev_pass, agree. Пишется на каждую LLM-проверку, на которую был вердикт Jev.
- GET/PUT /laya/settings. Тесты: 29 шт.
