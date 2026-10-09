#!/data/data/com.termux/files/usr/bin/bash
# Перезагрузка сервера PAI (run_server.py на порту 8080)
set -u

PORT=8080
DIR="$HOME/downloads/new"
LOG="$HOME/pai_server.log"

echo "[*] Останавливаю старый сервер..."
pkill -f 'python run_server.py' 2>/dev/null
for i in $(seq 1 10); do
    pgrep -f 'python run_server.py' >/dev/null || break
    sleep 1
done
if pgrep -f 'python run_server.py' >/dev/null; then
    echo "[!] Процессы не завершились, убиваю принудительно"
    pkill -9 -f 'python run_server.py'
    sleep 2
fi

echo "[*] Запускаю сервер..."
cd "$DIR" || { echo "[!] Нет каталога $DIR"; exit 1; }
PYTHONPATH=. nohup python run_server.py > "$LOG" 2>&1 &

echo "[*] Ждём поднятия порта $PORT..."
for i in $(seq 1 20); do
    if python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',$PORT))" 2>/dev/null; then
        echo "[OK] Сервер запущен (PID: $(pgrep -f 'python run_server.py' | head -1))"
        echo '--- последние строки лога ---'
        tail -5 "$LOG"
        exit 0
    fi
    sleep 1
done

echo "[FAIL] Сервер не поднялся за 20 сек. Лог:"
tail -20 "$LOG"
exit 1
