# Personal AI Assistant (pai)

## Language / Язык

- [English](#english)
- [Русский](#русский)

---

## English

### About the Project

PAI is a personal AI assistant with hierarchical memory, custom functions, and tools.

### Key Features

- **Hierarchical Memory** — tree structure (roots → children → leaves) with full-text search and export/import (WAL → JSONL)
- **Personas** — different roles (tutor, work, mentor, etc.) with independent memory spaces and tool permissions
- **Tools** — built-in (web_search, calculate_time_delta) and user-defined Python functions
- **Self-generated rules & Skills** — behavior configurable via rules and skills
- **API** — REST API for chat, memory, personas, tools, functions, rules, and external services

### Quick Start

```bash
# Install dependencies
cd /data/data/com.termux/files/home/downloads/go_pai
pip install -r requirements.txt

# Start the server (FastAPI + Uvicorn)
uvicorn pai.app:app --host 127.0.0.1 --port 8000

# Access UI
# http://127.0.0.1:8000

# Tests
python -m pytest tests
```

### Environment

- `PAI_DATA_DIR` — data directory (default: `./pai_data`)
- `PAI_TIMEZONE` — IANA timezone (e.g. `Europe/Belgrade`; default: system)
- `PAI_TAIL_LENGTH` — number of chat messages in the prompt tail (default: 10)
- `PAI_LLM_*` — LLM settings (see `pai/app.py`)

### Architecture

- **Memory** — SQLite with WAL mode, export/import via JSONL
- **Orchestrator** — tool pool planning, iteration limits, error handling
- **Tool Dispatcher** — execution of tools (built-in and user-defined) with timeouts
- **API Layer** — FastAPI endpoints for all entities

### Iterations

- Iteration 1 — memory backup/restore (export/import WAL → JSONL)
- Iteration 2 — graceful exit when tool call limit is exhausted
- Iteration 6 — `web_search` tool (internet search via DuckDuckGo)
- Iteration 7 — `tool_permissions` changes via API
- Iteration 8 — pool of independent tool calls (parallel execution, planning)

### Custom Functions

User-defined Python functions are stored as `draft` and require owner validation and activation. Activated functions appear in tool schemas and can be invoked by the model. Code runs in a separate process with a timeout (default: 30 sec).

### API Endpoints

- `/chat`, `/chat/stream` — LLM chat
- `/memory/*` — hierarchical storage (tree, roots, children, search, export/import)
- `/personas/*` — personas, tool_permissions
- `/tools/*` — tools (web_search, calculate_time_delta, etc.)
- `/skills/*` — Skills (self-generated behavior)
- `/functions/*` — custom functions
- `/rules/*` — self-generated rules
- `/services/*` — external services
- `/mcp/*` — Model Context Protocol

### License

MIT

---

## Русский

### О проекте

PAI — персональный AI-ассистент с иерархической памятью, кастомными функциями и инструментами.

### Основные возможности

- **Иерархическая память** — древовидная структура (roots → children → leaves) с полнотекстовым поиском и экспортом/импортом (WAL → JSONL)
- **Персоны** — разные роли (tutor, work, mentor, etc.) с независимыми пространствами памяти и правами на инструменты
- **Инструменты** — встроенные (web_search, calculate_time_delta) и пользовательские Python-функции
- **Self-generated rules & Skills** — поведение, настраиваемое через правила и навыки
- **API** — REST API для чата, памяти, персон, инструментов, функций, правил и внешних сервисов

### Быстрый старт

```bash
# Установка зависимостей
cd /data/data/com.termux/files/home/downloads/go_pai
pip install -r requirements.txt

# Запуск сервера (FastAPI + Uvicorn)
uvicorn pai.app:app --host 127.0.0.1 --port 8000

# Доступ к UI
# http://127.0.0.1:8000

# Тесты
python -m pytest tests
```

### Окружение

- `PAI_DATA_DIR` — директория с данными (по умолчанию `./pai_data`)
- `PAI_TIMEZONE` — часовой пояс в формате IANA (например, `Europe/Belgrade`; по умолчанию системный)
- `PAI_TAIL_LENGTH` — количество сообщений чата в tail-части промпта (по умолчанию 10)
- `PAI_LLM_*` — настройки LLM (см. `pai/app.py`)

### Архитектура

- **Memory** — SQLite с WAL-режимом, экспорт/импорт через JSONL
- **Orchestrator** — планирование tool pools, ограничение итераций, обработка ошибок
- **Tool Dispatcher** — выполнение инструментов (встроенных и пользовательских) с таймаутами
- **API Layer** — FastAPI эндпоинты для всех сущностей

### Итерации

- Итерация 1 — бэкап/восстановление памяти (export/import WAL → JSONL)
- Итерация 2 — корректное завершение при исчерпании лимита вызовов инструментов
- Итерация 6 — инструмент `web_search` (поиск в интернете через DuckDuckGo)
- Итерация 7 — изменение `tool_permissions` у персоны через API
- Итерация 8 — пул независимых tool calls (параллельное выполнение, планирование)

### Пользовательские функции

Пользовательские Python-функции хранятся как `draft` и требуют проверки и активации владельцем. Активированные функции попадают в tool schemas и могут быть вызваны моделью. Код запускается в отдельном процессе с таймаутом (по умолчанию 30 сек).

### API эндпоинты

- `/chat`, `/chat/stream` — диалог с LLM
- `/memory/*` — иерархическое хранилище (tree, roots, children, search, export/import)
- `/personas/*` — персоны, tool_permissions
- `/tools/*` — инструменты (web_search, calculate_time_delta и др.)
- `/skills/*` — Skills (self-generated behavior)
- `/functions/*` — кастомные функции
- `/rules/*` — self-generated rules
- `/services/*` — внешние сервисы
- `/mcp/*` — Model Context Protocol

### Лицензия

MIT
