#!/usr/bin/env bash
# vlessfinder.sh — обёртка для запуска vlessFinder
# Использование: ./vlessfinder.sh [start|stop|restart|status] [-f]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
MAIN="$SCRIPT_DIR/main.py"
CONFIG="$SCRIPT_DIR/config.yaml"

# Если python venv не создан — предупредить
if [ ! -f "$PYTHON" ]; then
    echo "Виртуальное окружение не найдено. Запустите сначала:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

exec "$PYTHON" "$MAIN" --config "$CONFIG" "$@"
