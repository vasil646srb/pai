#!/usr/bin/env bash
#
# deploy.sh — быстрое развёртывание pai на арендованном GPU-сервере (Clore.ai
# и подобные). Один сценарий "с нуля": проверяет окружение, ставит то, чего
# не хватает, и запускает vLLM + pai (через scripts/start_clore.sh).
#
# Рекомендованный Docker-образ на Clore.ai:
#   vllm/vllm-openai:latest
#   (официальный образ vLLM-проекта; версия в нём — актуальный релиз vLLM,
#    OpenAI-совместимый сервер и все CUDA/torch-зависимости уже собраны —
#    не нужно вручную разбираться с версиями CUDA/torch через pip)
#
#   При заказе/настройке контейнера на Clore укажите override entrypoint на
#   `bash`, иначе контейнер попытается сам запустить `vllm serve` без
#   аргументов и без вашего кода внутри.
#
# Использование:
#   cd ~/pai && bash deploy.sh                  # полный запуск (vLLM + pai)
#   bash deploy.sh --skip-vllm                   # только pai (vLLM уже поднят)
#   bash deploy.sh --dry-run                      # только проверки, ничего не запускать
#   bash deploy.sh --stop                         # остановить оба процесса
#   VLLM_MODEL=... bash deploy.sh                 # другая модель (см. переменные ниже)
#
# Переменные окружения — те же, что у scripts/start_clore.sh (VLLM_MODEL,
# VLLM_QUANTIZATION, VLLM_PORT, PAI_PORT, PAI_LLM_BASE_URL, ...) — deploy.sh
# их не переопределяет, только прокидывает дальше.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

c_ok() { echo -e "\033[32m$*\033[0m"; }
c_warn() { echo -e "\033[33m$*\033[0m"; }
c_err() { echo -e "\033[31m$*\033[0m"; }
c_info() { echo -e "\033[36m$*\033[0m"; }
step() { echo; c_info "▸ $*"; }

# pip в свежих Debian/Ubuntu-образах (PEP 668) отказывает без
# --break-system-packages вне venv — пробуем как есть, при этой конкретной
# ошибке повторяем с флагом. В одноразовом контейнере на арендованном GPU
# это безопасно.
pip_install() {
    local errfile
    errfile="$(mktemp)"
    if pip install -q "$@" 2>"$errfile"; then
        rm -f "$errfile"
        return 0
    fi
    if grep -q "externally-managed-environment" "$errfile" 2>/dev/null; then
        rm -f "$errfile"
        pip install -q --break-system-packages "$@"
        return $?
    fi
    cat "$errfile" >&2
    rm -f "$errfile"
    return 1
}

DRY_RUN=false
ARGS=()
for a in "$@"; do
    if [[ "$a" == "--dry-run" ]]; then
        DRY_RUN=true
    else
        ARGS+=("$a")
    fi
done

echo "======================================================"
echo " pai — deploy"
echo " Docker-образ (рекомендуемый): vllm/vllm-openai:latest"
echo "======================================================"

# ---- 1. базовые бинарники ----
step "Проверяю базовые инструменты"
MISSING=()
for bin in python3 pip curl nvidia-smi; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        MISSING+=("$bin")
    fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    c_warn "Не найдено: ${MISSING[*]}"
    if [[ " ${MISSING[*]} " == *" nvidia-smi "* ]]; then
        c_warn "nvidia-smi отсутствует — либо не тот образ (нет GPU-драйверов), либо GPU не проброшена в контейнер."
    fi
    if [[ " ${MISSING[*]} " == *" python3 "* || " ${MISSING[*]} " == *" pip "* ]]; then
        c_err "python3/pip обязательны — без них дальше не пойдёт. Проверьте образ контейнера."
        exit 1
    fi
else
    c_ok "python3, pip, curl, nvidia-smi — на месте"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader
fi

# ---- 2. vLLM ----
step "Проверяю vLLM"
if python3 -c "import vllm" >/dev/null 2>&1; then
    VER="$(python3 -c "import vllm; print(vllm.__version__)" 2>/dev/null || echo "?")"
    c_ok "vLLM уже установлен (версия $VER) — вероятно, вы в образе vllm/vllm-openai, это ожидаемо"
else
    c_warn "vLLM не найден в текущем окружении."
    if $DRY_RUN; then
        c_warn "[dry-run] пропускаю установку: pip install vllm"
    else
        c_info "Ставлю vLLM (может занять несколько минут)…"
        pip_install vllm || { c_err "Не удалось установить vLLM. Рекомендуется образ vllm/vllm-openai:latest, где он уже есть."; exit 1; }
        c_ok "vLLM установлен"
    fi
fi

# ---- 3. зависимости pai ----
step "Проверяю зависимости pai (fastapi/uvicorn)"
if python3 -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    c_ok "fastapi/uvicorn уже установлены"
else
    if $DRY_RUN; then
        c_warn "[dry-run] пропускаю установку: pip install fastapi uvicorn[standard]"
    else
        c_info "Ставлю fastapi/uvicorn…"
        pip_install fastapi "uvicorn[standard]" || { c_err "Не удалось поставить fastapi/uvicorn"; exit 1; }
        c_ok "Установлено"
    fi
fi

# ---- 4. права на скрипты ----
step "Проверяю права на запуск"
chmod +x "$ROOT_DIR"/scripts/*.sh 2>/dev/null || true
c_ok "scripts/*.sh — исполняемые"

# ---- 5. запуск ----
if $DRY_RUN; then
    echo
    c_ok "[dry-run] Все проверки пройдены. Реальный запуск не выполнялся."
    c_info "Для запуска: bash deploy.sh"
    exit 0
fi

step "Запускаю vLLM + pai (scripts/start_clore.sh ${ARGS[*]:-})"
exec bash "$ROOT_DIR/scripts/start_clore.sh" "${ARGS[@]:-}"
