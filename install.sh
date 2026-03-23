#!/usr/bin/env bash
# =============================================================================
# vlessFinder — установщик
# Поддерживаемые ОС: Ubuntu 20.04+, Debian 11+
# Запуск: bash install.sh
# =============================================================================

set -euo pipefail

# --- Цвета ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }
section() { echo -e "\n${BOLD}──────────────────────────────────────${RESET}"; echo -e "${BOLD} $*${RESET}"; echo -e "${BOLD}──────────────────────────────────────${RESET}"; }

# --- Константы ---
INSTALL_DIR="/opt/vlessfinder"
VENV_DIR="$INSTALL_DIR/.venv"
CONFIG_FILE="$INSTALL_DIR/config.yaml"
LOG_DIR="/var/log/vlessfinder"
STATE_DIR="/var/lib/vlessfinder"
RUN_DIR="/var/run"
XRAY_BIN="/usr/local/bin/xray"
XRAY_RELEASES="https://api.github.com/repos/XTLS/Xray-core/releases/latest"
SERVICE_FILE="/etc/systemd/system/vlessfinder.service"
MIN_PYTHON="3.10"

# =============================================================================
# Проверки
# =============================================================================

section "Проверка окружения"

# Root
if [[ $EUID -ne 0 ]]; then
    die "Запустите скрипт с правами root: sudo bash install.sh"
fi

# ОС
if [[ ! -f /etc/os-release ]]; then
    die "Не удалось определить ОС"
fi
source /etc/os-release
if [[ "$ID" != "ubuntu" && "$ID" != "debian" && "$ID_LIKE" != *"debian"* ]]; then
    warn "Скрипт тестировался на Ubuntu/Debian. Текущая ОС: $PRETTY_NAME"
fi
ok "ОС: $PRETTY_NAME"

# Архитектура (для xray)
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)   XRAY_ARCH="64" ;;
    aarch64)  XRAY_ARCH="arm64-v8a" ;;
    armv7l)   XRAY_ARCH="arm32-v7a" ;;
    *)        die "Неподдерживаемая архитектура: $ARCH" ;;
esac
ok "Архитектура: $ARCH → xray-$XRAY_ARCH"

# =============================================================================
# Системные зависимости
# =============================================================================

section "Системные пакеты"

apt-get update -qq

PACKAGES=()

# Python 3.10+
PYTHON_BIN=""
for v in python3.12 python3.11 python3.10; do
    if command -v "$v" &>/dev/null; then
        PYTHON_BIN="$v"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    info "Устанавливаем Python 3.11..."
    add-apt-repository -y ppa:deadsnakes/ppa &>/dev/null 2>&1 || true
    apt-get install -y python3.11 python3.11-venv python3.11-dev &>/dev/null
    PYTHON_BIN="python3.11"
fi

PACKAGES+=(python3-pip python3-venv curl wget unzip jq)
apt-get install -y "${PACKAGES[@]}" &>/dev/null
ok "Python: $($PYTHON_BIN --version)"
ok "Системные пакеты установлены"

# =============================================================================
# Копирование файлов проекта
# =============================================================================

section "Установка vlessFinder"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
    info "Копируем файлы в $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
    cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR/"
else
    info "Уже в $INSTALL_DIR"
fi

# Создаём рабочие директории
mkdir -p "$LOG_DIR" "$STATE_DIR"
ok "Директории: $LOG_DIR, $STATE_DIR"

# =============================================================================
# Python окружение
# =============================================================================

section "Python окружение"

if [[ -d "$VENV_DIR" ]]; then
    warn "Виртуальное окружение уже существует — пересоздаём"
    rm -rf "$VENV_DIR"
fi

info "Создаём venv..."
"$PYTHON_BIN" -m venv "$VENV_DIR"

info "Устанавливаем зависимости..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
ok "Python зависимости установлены"

# =============================================================================
# Xray-core
# =============================================================================

section "Xray-core"

XRAY_INSTALLED=false
if [[ -f "$XRAY_BIN" ]]; then
    CURRENT_VER=$("$XRAY_BIN" version 2>/dev/null | head -1 | awk '{print $2}' || echo "?")
    warn "xray уже установлен ($CURRENT_VER). Обновляем до последней версии..."
fi

info "Определяем последнюю версию xray-core..."
XRAY_VERSION=$(curl -fsSL "$XRAY_RELEASES" | jq -r '.tag_name' 2>/dev/null || echo "")

if [[ -z "$XRAY_VERSION" ]]; then
    warn "Не удалось получить версию через API (лимит GitHub?). Используем последний известный тег."
    XRAY_VERSION="v25.3.6"
fi
ok "Версия xray: $XRAY_VERSION"

XRAY_ZIP="Xray-linux-${XRAY_ARCH}.zip"
XRAY_URL="https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${XRAY_ZIP}"

TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

info "Скачиваем xray-core..."
if ! wget -q --show-progress -O "$TMP_DIR/$XRAY_ZIP" "$XRAY_URL"; then
    die "Не удалось скачать xray-core с $XRAY_URL"
fi

info "Распаковываем..."
unzip -q "$TMP_DIR/$XRAY_ZIP" -d "$TMP_DIR/xray"
install -m 0755 "$TMP_DIR/xray/xray" "$XRAY_BIN"

XRAY_VER_CHECK=$("$XRAY_BIN" version 2>/dev/null | head -1 || echo "?")
ok "xray установлен: $XRAY_VER_CHECK → $XRAY_BIN"

# =============================================================================
# Генерация UUID для пользователей
# =============================================================================

section "Конфигурация"

gen_uuid() {
    # Используем xray для генерации UUID (гарантированно корректный формат)
    "$XRAY_BIN" uuid 2>/dev/null || python3 -c "import uuid; print(uuid.uuid4())"
}

# Обновляем config.yaml: xray путь, state_dir, log, pid
CONFIG_SRC="$INSTALL_DIR/config.yaml"

# sed-замены для путей
sed -i "s|xray_binary:.*|xray_binary: \"$XRAY_BIN\"|" "$CONFIG_SRC"
sed -i "s|pid_file:.*|pid_file: \"/var/run/vlessfinder.pid\"|" "$CONFIG_SRC"
sed -i "s|log_file:.*|log_file: \"$LOG_DIR/vlessfinder.log\"|" "$CONFIG_SRC"
sed -i "s|state_dir:.*|state_dir: \"$STATE_DIR\"|" "$CONFIG_SRC"

# Генерируем UUID для пользователей-примеров если они ещё дефолтные
ALICE_TOKEN=$(gen_uuid)
BOB_TOKEN=$(gen_uuid)

# Заменяем placeholder UUID
sed -i "s|11111111-1111-1111-1111-111111111111|$ALICE_TOKEN|g" "$CONFIG_SRC"
sed -i "s|22222222-2222-2222-2222-222222222222|$BOB_TOKEN|g"   "$CONFIG_SRC"

ok "UUID для alice: $ALICE_TOKEN"
ok "UUID для bob:   $BOB_TOKEN"
ok "Конфиг обновлён: $CONFIG_SRC"

# =============================================================================
# Исполняемый wrapper
# =============================================================================

section "Команда vlessfinder"

# Обновляем vlessfinder.sh с правильными путями
cat > "$INSTALL_DIR/vlessfinder.sh" << SHEOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$INSTALL_DIR/main.py" --config "$CONFIG_FILE" "\$@"
SHEOF
chmod +x "$INSTALL_DIR/vlessfinder.sh"

# Симлинк в /usr/local/bin для вызова без пути
ln -sf "$INSTALL_DIR/vlessfinder.sh" /usr/local/bin/vlessfinder
ok "Команда доступна: vlessfinder [start|stop|restart|status]"

# =============================================================================
# Systemd сервис (автозапуск)
# =============================================================================

section "Systemd сервис"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=vlessFinder VLESS subscription manager
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
PIDFile=/var/run/vlessfinder.pid
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/main.py --config $CONFIG_FILE start
ExecStop=$VENV_DIR/bin/python $INSTALL_DIR/main.py --config $CONFIG_FILE stop
ExecReload=$VENV_DIR/bin/python $INSTALL_DIR/main.py --config $CONFIG_FILE restart
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vlessfinder &>/dev/null
ok "Systemd сервис зарегистрирован (автозапуск при загрузке)"

# =============================================================================
# Итог
# =============================================================================

section "Установка завершена"

PORT=$(grep -E '^\s+port:' "$CONFIG_FILE" | head -1 | awk '{print $2}')
HOST_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "YOUR_SERVER_IP")

echo -e "
${BOLD}Управление:${RESET}
  vlessfinder start              # запуск
  vlessfinder start --foreground # запуск в терминале (отладка)
  vlessfinder stop               # остановка
  vlessfinder restart            # перезапуск
  vlessfinder status             # статус и URL подписок

  systemctl start vlessfinder    # через systemd
  systemctl status vlessfinder   # статус через systemd

${BOLD}Конфиг:${RESET}
  $CONFIG_FILE

${BOLD}Логи:${RESET}
  tail -f $LOG_DIR/vlessfinder.log

${BOLD}URL подписок:${RESET}
  alice → http://${HOST_IP}:${PORT}/sub/${ALICE_TOKEN}
  bob   → http://${HOST_IP}:${PORT}/sub/${BOB_TOKEN}

${YELLOW}Отредактируйте конфиг перед запуском:${RESET}
  nano $CONFIG_FILE
  # Добавьте/переименуйте пользователей, укажите свои репозитории

${GREEN}Запуск прямо сейчас:${RESET}
  vlessfinder start
"
