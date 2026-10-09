#!/usr/bin/env bash
#
# Запуск связки vLLM (inference) + pai (оркестратор) в одном контейнере
# (Clore.ai не поддерживает docker-compose — один контейнер на заказ).
#
# Использование:
#   cd ~/pai && bash scripts/start_clore.sh
#   VLLM_MODEL=... bash scripts/start_clore.sh          # другая модель
#   bash scripts/start_clore.sh --skip-vllm              # если vLLM уже поднят отдельно
#   bash scripts/start_clore.sh --stop                   # остановить оба процесса
#
# Логи: ./logs/vllm.log и ./logs/pai.log (полные, ничего не обрезается)
# PID-файлы: ./logs/vllm.pid и ./logs/pai.pid

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

VLLM_LOG="$LOG_DIR/vllm.log"
PAI_LOG="$LOG_DIR/pai.log"
VLLM_PID_FILE="$LOG_DIR/vllm.pid"
PAI_PID_FILE="$LOG_DIR/pai.pid"

# ---- настройки (переопределяются через переменные окружения) ----
VLLM_MODEL="${VLLM_MODEL:-cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ}"
VLLM_QUANTIZATION="${VLLM_QUANTIZATION:-}"
VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-22528}"
VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.90}"
VLLM_TOOL_PARSER="${VLLM_TOOL_PARSER:-hermes}"

PAI_HOST="${PAI_HOST:-0.0.0.0}"
PAI_PORT="${PAI_PORT:-8080}"
PAI_DATA_DIR="${PAI_DATA_DIR:-$ROOT_DIR/pai_data}"

VLLM_READY_TIMEOUT="${VLLM_READY_TIMEOUT:-600}"  # сек — первая загрузка весов может быть долгой

# ---- цвета для консоли (не для логов) ----
c_ok() { echo -e "\033[32m$*\033[0m"; }
c_warn() { echo -e "\033[33m$*\033[0m"; }
c_err() { echo -e "\033[31m$*\033[0m"; }
c_info() { echo -e "\033[36m$*\033[0m"; }

timestamp() { date "+%Y-%m-%d %H:%M:%S"; }

# ---- остановка ----
stop_all() {
    for pf in "$VLLM_PID_FILE" "$PAI_PID_FILE"; do
        if [[ -f "$pf" ]]; then
            pid="$(cat "$pf")"
            if kill -0 "$pid" 2>/dev/null; then
                c_warn "[$(timestamp)] останавливаю PID $pid ($(basename "$pf" .pid))"
                kill "$pid" 2>/dev/null
                sleep 2
                kill -9 "$pid" 2>/dev/null || true
            fi
            rm -f "$pf"
        fi
    done
    c_ok "Остановлено."
}

if [[ "${1:-}" == "--stop" ]]; then
    stop_all
    exit 0
fi

SKIP_VLLM=false
if [[ "${1:-}" == "--skip-vllm" ]]; then
    SKIP_VLLM=true
fi

# ---- проверка, что уже не запущено ----
for pf in "$VLLM_PID_FILE" "$PAI_PID_FILE"; do
    if [[ -f "$pf" ]] && kill -0 "$(cat "$pf")" 2>/dev/null; then
        c_err "Процесс из $pf уже запущен (PID $(cat "$pf")). Сначала: bash scripts/start_clore.sh --stop"
        exit 1
    fi
done

trap 'echo; c_warn "Прерывание — процессы продолжают работать в фоне."; c_info "Логи: tail -f $VLLM_LOG $PAI_LOG"; exit 0' INT

# ================= 1. vLLM =================

if [[ "$SKIP_VLLM" == false ]]; then
    c_info "[$(timestamp)] проверяю свободную память GPU…"
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | tee -a "$VLLM_LOG"
    fi

    c_info "[$(timestamp)] запускаю vLLM: $VLLM_MODEL (quant=$VLLM_QUANTIZATION, port=$VLLM_PORT)"
    {
        echo "===== $(timestamp) vLLM start ====="
        echo "model=$VLLM_MODEL quantization=$VLLM_QUANTIZATION max_model_len=$VLLM_MAX_MODEL_LEN gpu_util=$VLLM_GPU_UTIL tool_parser=$VLLM_TOOL_PARSER"
    } >> "$VLLM_LOG"

    VLLM_QUANT_ARGS=()
    if [[ -n "$VLLM_QUANTIZATION" ]]; then
        VLLM_QUANT_ARGS=(--quantization "$VLLM_QUANTIZATION")
    fi

    nohup python3 -m vllm.entrypoints.openai.api_server \
        --model "$VLLM_MODEL" \
        "${VLLM_QUANT_ARGS[@]}" \
        --port "$VLLM_PORT" \
        --enable-auto-tool-choice \
        --tool-call-parser "$VLLM_TOOL_PARSER" \
        --max-model-len "$VLLM_MAX_MODEL_LEN" \
        --gpu-memory-utilization "$VLLM_GPU_UTIL" \
        >> "$VLLM_LOG" 2>&1 &

    echo $! > "$VLLM_PID_FILE"
    c_info "vLLM PID: $(cat "$VLLM_PID_FILE") — лог: $VLLM_LOG"

    c_info "[$(timestamp)] жду готовности vLLM (до ${VLLM_READY_TIMEOUT}s)…"
    elapsed=0
    ready=false
    while [[ $elapsed -lt $VLLM_READY_TIMEOUT ]]; do
        if ! kill -0 "$(cat "$VLLM_PID_FILE")" 2>/dev/null; then
            c_err "vLLM упал при старте. Последние строки лога:"
            tail -n 40 "$VLLM_LOG"
            exit 1
        fi
        if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 5
        elapsed=$((elapsed + 5))
        printf "."
    done
    echo

    if [[ "$ready" != true ]]; then
        c_err "vLLM не ответил за ${VLLM_READY_TIMEOUT}s. Последние строки лога:"
        tail -n 40 "$VLLM_LOG"
        exit 1
    fi
    c_ok "[$(timestamp)] vLLM готов: http://127.0.0.1:${VLLM_PORT}/v1"
else
    c_warn "--skip-vllm: пропускаю запуск vLLM, ожидаю, что он уже слушает порт $VLLM_PORT"
fi

# ================= 2. pai =================

if [[ ! -d "$ROOT_DIR/pai" ]]; then
    c_err "Не найден каталог пакета pai/ рядом со скриптом ($ROOT_DIR/pai). Запускайте из корня проекта."
    exit 1
fi

if ! python3 -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    c_info "[$(timestamp)] ставлю зависимости pai (fastapi/uvicorn)…"
    if ! pip install -q fastapi "uvicorn[standard]" >> "$PAI_LOG" 2>&1; then
        if grep -q "externally-managed-environment" "$PAI_LOG" 2>/dev/null; then
            pip install -q --break-system-packages fastapi "uvicorn[standard]" >> "$PAI_LOG" 2>&1
        fi
    fi
fi

export PAI_LLM_BASE_URL="${PAI_LLM_BASE_URL:-http://127.0.0.1:${VLLM_PORT}/v1}"
export PAI_LLM_MODEL="${PAI_LLM_MODEL:-$VLLM_MODEL}"

c_info "[$(timestamp)] запускаю pai: http://${PAI_HOST}:${PAI_PORT} (LLM: $PAI_LLM_BASE_URL, модель: $PAI_LLM_MODEL)"
{
    echo "===== $(timestamp) pai start ====="
    echo "PAI_LLM_BASE_URL=$PAI_LLM_BASE_URL PAI_LLM_MODEL=$PAI_LLM_MODEL PAI_DATA_DIR=$PAI_DATA_DIR"
} >> "$PAI_LOG"

cd "$ROOT_DIR"
PAI_HOST="$PAI_HOST" PAI_PORT="$PAI_PORT" PAI_DATA_DIR="$PAI_DATA_DIR" \
    nohup python3 run_server.py >> "$PAI_LOG" 2>&1 &

echo $! > "$PAI_PID_FILE"
c_info "pai PID: $(cat "$PAI_PID_FILE") — лог: $PAI_LOG"

sleep 3
if ! kill -0 "$(cat "$PAI_PID_FILE")" 2>/dev/null; then
    c_err "pai упал при старте. Последние строки лога:"
    tail -n 40 "$PAI_LOG"
    exit 1
fi

if curl -sf "http://127.0.0.1:${PAI_PORT}/health" >/dev/null 2>&1; then
    c_ok "[$(timestamp)] pai готов: http://127.0.0.1:${PAI_PORT}  (клиент — в браузере на этот же адрес)"
else
    c_warn "pai запущен (PID жив), но /health пока не отвечает — проверьте $PAI_LOG"
fi

echo
c_ok "Всё поднято. Полезные команды:"
echo "  tail -f $VLLM_LOG            # логи vLLM"
echo "  tail -f $PAI_LOG             # логи pai"
echo "  bash scripts/start_clore.sh --stop   # остановить оба процесса"
echo "  ssh -L ${PAI_PORT}:localhost:${PAI_PORT} -p <ssh_port> root@<host>   # проброс для браузера с локальной машины"
