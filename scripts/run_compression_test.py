#!/usr/bin/env python3
"""Тестовый диалог для проверки, как работает сжатие памяти (RAPTOR-like
tree) и полнотекстовый поиск по архиву на реалистичном наборе фактов: дни
рождения членов семьи, их текущий возраст, кто где работает и какой сейчас
год.

Скрипт НЕ вызывает Orchestrator напрямую в процессе — он поднимает
настоящий HTTP-сервер pai (тот же FastAPI app, что и в проде, через
uvicorn) на localhost и общается с ним только через реальные HTTP-запросы
к /chat, /memory/compress, /memory/search, /memory/stats — то есть тестирует
именно API-контракт, которым пользуется веб-клиент (app.js) и любой внешний
интегратор, а не внутренние методы оркестратора.

По умолчанию сервер поднимается на MockLLM (детерминированная заглушка без
сети/ключей — специально, чтобы тест был воспроизводим и не требовал
настоящего провайдера) в отдельном потоке этого же процесса. Если нужно
погонять на реальном агенте — есть два режима:

  --against-url http://localhost:8080
      Не поднимать свой сервер вообще, слать запросы на уже запущенный
      pai-сервер (например тот, что вы запустили через run_server.py с
      настоящим LLM-провайдером в Settings). Тогда ответы на контрольные
      вопросы будут настоящими, а не эхо-заглушкой.

  --real-llm
      Поднять свой сервер, но с реальным LLM-клиентом из переменных
      окружения (см. pai/app.py: build_llm_from_env()) вместо MockLLM.

Запуск:
    cd pai
    python3 scripts/run_compression_test.py
    python3 scripts/run_compression_test.py --seed 7 --noise-ratio 0.6
    python3 scripts/run_compression_test.py --against-url http://localhost:8080
    python3 scripts/run_compression_test.py --real-llm
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # чтобы `import pai.*` работал при запуске напрямую

from family_dialog_generator import build_events, expected_facts, CURRENT_YEAR


SPACE_ID = "personal"
PERSONA_ID = "brain"


# ===================== HTTP-клиент к API pai (stdlib-only) =====================

class ApiClient:
    """Тонкий HTTP-клиент на urllib — намеренно без requests/httpx, чтобы
    скрипт не тянул лишних зависимостей помимо тех, что уже нужны серверу.
    Каждый метод — реальный HTTP-запрос к работающему pai-серверу."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {detail}") from e

    def health(self) -> dict:
        return self._request("GET", "/health")

    def chat(self, text: str, persona_id: str = PERSONA_ID, space_id: str = SPACE_ID) -> dict:
        return self._request("POST", "/chat", {"text": text, "persona_id": persona_id, "space_id": space_id})

    def memory_stats(self) -> dict:
        return self._request("GET", "/memory/stats")

    def memory_compress(self, space_id: str = SPACE_ID, force_top: bool = False) -> dict:
        return self._request("POST", "/memory/compress", {"space_id": space_id, "force_top": force_top})

    def memory_search(self, query: str, space_id: Optional[str] = SPACE_ID, limit: int = 5) -> dict:
        return self._request("POST", "/memory/search", {"query": query, "space_id": space_id, "limit": limit})

    def wait_until_up(self, attempts: int = 50, delay: float = 0.1) -> None:
        last_err = None
        for _ in range(attempts):
            try:
                self.health()
                return
            except Exception as e:  # noqa: BLE001 — сервер ещё не поднялся, это ожидаемо
                last_err = e
                time.sleep(delay)
        raise RuntimeError(f"Сервер не поднялся за отведённое время: {last_err}")


# ===================== управление собственным тестовым сервером =====================

class LocalServer:
    """Поднимает настоящий pai FastAPI app через uvicorn в отдельном потоке
    этого же процесса, на свободном порту, с изолированной временной (или
    указанной) БД. Останавливается по .stop()."""

    def __init__(self, data_dir: Path, use_real_llm: bool = False, port: int = 0):
        import socket
        import uvicorn

        from pai.app import create_app, build_llm_from_env
        from pai.llm import MockLLM
        from pai.storage import Storage

        self.data_dir = data_dir
        self.storage = Storage(data_dir)
        llm = build_llm_from_env() if use_real_llm else MockLLM()
        self.app = create_app(data_dir=str(data_dir), llm=llm, storage=self.storage)

        if port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
        self.port = port

        config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10)
        self.storage.close()


# ===================== отчётность (форматирование, общее для обоих режимов) =====================

def fmt_level_counts(by_level: dict) -> str:
    if not by_level:
        return "(пусто)"
    # ключи из JSON приходят строками ("0", "1", ...), из compression_ratio — int'ами;
    # нормализуем перед сортировкой, чтобы работать в обоих режимах одинаково
    items = sorted(((int(k), v) for k, v in by_level.items()), key=lambda kv: kv[0])
    return ", ".join(f"L{lvl}={cnt}" for lvl, cnt in items)


def run(args: argparse.Namespace) -> int:
    events = build_events(seed=args.seed, noise_ratio=args.noise_ratio)
    facts = expected_facts(year=CURRENT_YEAR)

    server: Optional[LocalServer] = None
    cleanup_dir: Optional[str] = None

    if args.against_url:
        base_url = args.against_url
        print(f"=== Используем уже запущенный сервер: {base_url} ===")
    else:
        if args.keep_db:
            data_dir = Path(args.keep_db)
            data_dir.mkdir(parents=True, exist_ok=True)
        else:
            cleanup_dir = tempfile.mkdtemp(prefix="pai_compression_test_")
            data_dir = Path(cleanup_dir)
        server = LocalServer(data_dir, use_real_llm=args.real_llm)
        server.start()
        base_url = server.base_url
        print(f"=== Поднят локальный тестовый сервер: {base_url} "
              f"(LLM: {'реальный, из окружения' if args.real_llm else 'MockLLM'}) ===")

    api = ApiClient(base_url)
    try:
        api.wait_until_up()
    except Exception as e:
        print(f"Не удалось достучаться до сервера: {e}", file=sys.stderr)
        if server:
            server.stop()
        return 2

    try:
        return _run_against_api(api, events, facts, args)
    finally:
        if server:
            server.stop()
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            elif args.keep_db:
                print(f"\nБаза сохранена в {args.keep_db} (--keep-db) — можно открыть в Memory Explorer (веб-UI pai).")


def _run_against_api(api: ApiClient, events, facts: dict, args: argparse.Namespace) -> int:
    print(f"\n=== Тест-диалог через HTTP API: {len(events)} сообщений "
          f"({sum(1 for e in events if e.fact_key)} фактических, "
          f"{sum(1 for e in events if e.fact_key is None)} шумовых), seed={args.seed} ===\n")

    # ---------- фаза 1: прогон диалога пачками через POST /chat ----------
    batch = []
    batch_no = 0
    for i, ev in enumerate(events, 1):
        api.chat(ev.text, persona_id=PERSONA_ID, space_id=SPACE_ID)
        batch.append(ev)

        if len(batch) >= args.batch_size or i == len(events):
            batch_no += 1
            stats_before = api.memory_stats()
            print(f"--- Пачка {batch_no}: сообщения {i - len(batch) + 1}..{i} ---")
            print(f"  Дерево до сжатия:  {fmt_level_counts(stats_before['by_level'])}  "
                  f"(всего узлов: {stats_before['total_nodes']})")

            if not args.no_compress:
                result = api.memory_compress(space_id=SPACE_ID, force_top=False)
                stats_after = api.memory_stats()
                print(f"  Создано саммари-узлов в этой пачке: {result['count']}")
                print(f"  Дерево после сжатия: {fmt_level_counts(stats_after['by_level'])}  "
                      f"(всего узлов: {stats_after['total_nodes']})")
            batch = []
            print()

    if args.force_top:
        print("=== Принудительное финальное сжатие до единого корня (POST /memory/compress force_top=true) ===")
        result = api.memory_compress(space_id=SPACE_ID, force_top=True)
        stats_final = api.memory_stats()
        print(f"  Создано саммари-узлов: {result['count']}")
        print(f"  Итоговое дерево: {fmt_level_counts(stats_final['by_level'])}  "
              f"(всего узлов: {stats_final['total_nodes']})\n")

    final_stats = api.memory_stats()
    compression = final_stats.get("compression", {})
    print("=== Итоговая статистика дерева памяти (GET /memory/stats) ===")
    print(f"  Уровни: {compression.get('levels')}")
    print(f"  Узлов на level 0 (сырые сообщения): {compression.get('raw_nodes')}")
    print(f"  Узлов всего: {final_stats.get('total_nodes')}\n")

    # ---------- фаза 2: проверка поиска по архиву через POST /memory/search ----------
    print("=== Проверка POST /memory/search: находится ли каждый факт полнотекстовым поиском ===")
    found = 0
    missed = []
    for ev in events:
        if ev.fact_key is None:
            continue
        query = _query_terms_for(ev)
        resp = api.memory_search(query, space_id=SPACE_ID, limit=5)
        results = resp.get("results", [])
        hit = bool(results)
        if hit:
            found += 1
        else:
            missed.append((ev.fact_key, ev.text, query))

    total_facts = sum(1 for e in events if e.fact_key)
    print(f"  Найдено: {found}/{total_facts} ({found / total_facts:.0%})")
    if missed:
        print("  Не найдено:")
        for key, text, query in missed:
            print(f"    - [{key}] запрос='{query}'  исходный текст: {text!r}")
    print()

    # ---------- фаза 3: контрольные вопросы через POST /chat ----------
    print("=== Контрольные вопросы через POST /chat (реальный HTTP-запрос к агенту) ===")
    control_questions = [
        "Какой сейчас год?",
        "Сколько лет маме?",
        "Когда день рождения у брата?",
        "Где работает жена?",
        "Сколько лет сестре и где она учится?",
    ]
    for q in control_questions:
        resp = api.chat(q, persona_id=PERSONA_ID, space_id=SPACE_ID)
        preview = resp.get("live_context_preview", {})
        tool_trace = resp.get("tool_trace", [])
        print(f"  Q: {q}")
        print(f"  A: {resp.get('reply')}")
        if tool_trace:
            calls = ", ".join(f"{t['name']}({t['arguments']})" for t in tool_trace)
            print(f"  вызовы инструментов: {calls}")
        print(f"  live_context tokens (system/user/reply): "
              f"{preview.get('system_prompt_tokens_est')}/"
              f"{preview.get('user_tokens_est')}/"
              f"{preview.get('reply_tokens_est')}")
        print()

    print("=== Ожидаемые факты (эталон для ручной/автоматической сверки ответов агента) ===")
    for key, value in sorted(facts.items()):
        print(f"  {key:20s} = {value}")

    return 0 if not missed else 1


def _query_terms_for(ev) -> str:
    """Достаёт из события 1-2 ключевых слова для поискового запроса —
    имитация того, какие термины модель обычно кладёт в search_archive
    (не весь текст целиком, а суть: имя/отношение + тип факта)."""
    key = ev.fact_key or ""
    subject, fact_type = (key.split(".") + [""])[:2]
    words = [w.strip(",.–—()") for w in ev.text.split()]
    capitalized = [w for w in words if w[:1].isupper() and len(w) > 2]
    name_hint = max(capitalized, key=len) if capitalized else subject
    type_hint = {"birthday": "день рождения", "age": "лет", "job": "работает", "year": "год"}.get(fact_type, fact_type)
    return f"{name_hint} {type_hint}".strip()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=42, help="seed генератора диалога (по умолчанию 42)")
    p.add_argument("--noise-ratio", type=float, default=1.2,
                   help="сколько шумовых реплик на одну фактическую, в среднем (по умолчанию 1.2)")
    p.add_argument("--batch-size", type=int, default=6,
                   help="сколько сообщений в пачке между промежуточными сжатиями (по умолчанию 6)")
    p.add_argument("--no-compress", action="store_true",
                   help="не сжимать между пачками — посмотреть на чистый линейный рост level-0")
    p.add_argument("--force-top", action="store_true", default=True,
                   help="в конце схлопнуть дерево до единого корня (по умолчанию включено)")
    p.add_argument("--no-force-top", dest="force_top", action="store_false")
    p.add_argument("--keep-db", type=str, default=None,
                   help="каталог, куда сохранить SQLite базу вместо временной (для просмотра в Memory Explorer). "
                        "Игнорируется вместе с --against-url.")
    p.add_argument("--real-llm", action="store_true",
                   help="поднять собственный тестовый сервер с реальным LLM-клиентом из переменных окружения "
                        "вместо MockLLM (см. pai/app.py: build_llm_from_env())")
    p.add_argument("--against-url", type=str, default=None,
                   help="не поднимать свой сервер — слать запросы на уже работающий pai-сервер "
                        "(например http://localhost:8080). Тогда --real-llm/--keep-db игнорируются: "
                        "конфигурация LLM и хранилища берётся от того сервера.")
    args = p.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
