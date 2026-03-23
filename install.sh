#!/usr/bin/env bash
# =============================================================================
# vlessFinder — установщик / деинсталлятор
#
# Установка:
#   bash <(curl -Ls https://raw.githubusercontent.com/YOUR_USER/vlessFinder/main/install.sh)
#
# Удаление:
#   bash <(curl -Ls https://raw.githubusercontent.com/YOUR_USER/vlessFinder/main/install.sh) uninstall
#
# Поддерживаемые ОС: Ubuntu 20.04+, Debian 11+
# =============================================================================

set -euo pipefail

# =============================================================================
# Настройки — измените на свой GitHub репозиторий
# =============================================================================

GITHUB_USER="YOUR_GITHUB_USER"
GITHUB_REPO="vlessFinder"
GITHUB_BRANCH="main"
REPO_RAW="https://raw.githubusercontent.com/${GITHUB_USER}/${GITHUB_REPO}/${GITHUB_BRANCH}"

# =============================================================================
# Константы
# =============================================================================

INSTALL_DIR="/opt/vlessfinder"
VENV_DIR="$INSTALL_DIR/.venv"
CONFIG_FILE="$INSTALL_DIR/config.yaml"
LOG_DIR="/var/log/vlessfinder"
STATE_DIR="/var/lib/vlessfinder"
XRAY_BIN="/usr/local/bin/xray"
XRAY_RELEASES="https://api.github.com/repos/XTLS/Xray-core/releases/latest"
SERVICE_FILE="/etc/systemd/system/vlessfinder.service"
WRAPPER="/usr/local/bin/vlessfinder"

ADMIN_TOKEN_PLACEHOLDER="00000000-0000-0000-0000-000000000000"

# Файлы проекта для скачивания
PROJECT_FILES=(
    "main.py"
    "fetcher.py"
    "validator.py"
    "subscription_manager.py"
    "server.py"
    "requirements.txt"
)

# =============================================================================
# Утилиты
# =============================================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERR ]${RESET}  $*" >&2; }
die()     { error "$*"; exit 1; }
section() {
    echo ""
    echo -e "${BOLD}══════════════════════════════════════════${RESET}"
    echo -e "${BOLD}  $*${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════${RESET}"
}

gen_uuid() {
    if command -v "$XRAY_BIN" &>/dev/null; then
        "$XRAY_BIN" uuid 2>/dev/null
    else
        python3 -c "import uuid; print(uuid.uuid4())"
    fi
}

download() {
    local url="$1"
    local dest="$2"
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "$dest" "$url"; then
        die "Не удалось скачать: $url"
    fi
}

# =============================================================================
# Деинсталляция
# =============================================================================

cmd_uninstall() {
    [[ $EUID -ne 0 ]] && die "Запустите с правами root: sudo bash install.sh uninstall"

    echo ""
    echo -e "${BOLD}══════════════════════════════════════════${RESET}"
    echo -e "${BOLD}  Удаление vlessFinder${RESET}"
    echo -e "${BOLD}══════════════════════════════════════════${RESET}"
    echo ""
    echo -e "Будут удалены:"
    echo -e "  ${RED}✕${RESET}  Сервис systemd          /etc/systemd/system/vlessfinder.service"
    echo -e "  ${RED}✕${RESET}  Команда                  /usr/local/bin/vlessfinder"
    echo -e "  ${RED}✕${RESET}  Файлы программы          $INSTALL_DIR"
    echo -e "  ${RED}✕${RESET}  Рабочие данные           $STATE_DIR"
    echo -e "  ${RED}✕${RESET}  Логи                     $LOG_DIR"
    echo -e "  ${YELLOW}○${RESET}  xray-core               $XRAY_BIN  ${YELLOW}(опционально)${RESET}"
    echo ""
    read -r -p "Продолжить? [y/N]: " confirm
    [[ "${confirm,,}" != "y" ]] && { echo "Отменено."; exit 0; }

    echo ""

    # Остановка и удаление сервиса
    if systemctl is-active --quiet vlessfinder 2>/dev/null; then
        info "Останавливаем сервис..."
        systemctl stop vlessfinder
        ok "Сервис остановлен"
    fi
    if systemctl is-enabled --quiet vlessfinder 2>/dev/null; then
        systemctl disable vlessfinder &>/dev/null
        ok "Автозапуск отключён"
    fi
    if [[ -f "$SERVICE_FILE" ]]; then
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
        ok "Сервис удалён: $SERVICE_FILE"
    fi

    # PID файл
    rm -f /var/run/vlessfinder.pid

    # Команда
    if [[ -L "$WRAPPER" ]]; then
        rm -f "$WRAPPER"
        ok "Команда удалена: $WRAPPER"
    fi

    # Директория программы
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        ok "Файлы удалены: $INSTALL_DIR"
    fi

    # Данные подписок
    if [[ -d "$STATE_DIR" ]]; then
        rm -rf "$STATE_DIR"
        ok "Данные удалены: $STATE_DIR"
    fi

    # Логи
    if [[ -d "$LOG_DIR" ]]; then
        rm -rf "$LOG_DIR"
        ok "Логи удалены: $LOG_DIR"
    fi

    # Опционально — xray-core
    if [[ -f "$XRAY_BIN" ]]; then
        echo ""
        read -r -p "Удалить xray-core ($XRAY_BIN)? [y/N]: " xray_confirm
        if [[ "${xray_confirm,,}" == "y" ]]; then
            rm -f "$XRAY_BIN"
            ok "xray-core удалён"
        else
            info "xray-core оставлен"
        fi
    fi

    echo ""
    echo -e "${GREEN}${BOLD}  vlessFinder полностью удалён.${RESET}"
    echo ""
    exit 0
}

# =============================================================================
# Роутинг аргументов
# =============================================================================

COMMAND="${1:-install}"
case "$COMMAND" in
    uninstall) cmd_uninstall ;;
    install)   ;;  # продолжаем ниже
    *) echo "Использование: bash install.sh [install|uninstall]"; exit 1 ;;
esac

# =============================================================================
# Проверки
# =============================================================================

section "Проверка окружения"

[[ $EUID -ne 0 ]] && die "Запустите с правами root:  sudo bash install.sh"

if [[ ! -f /etc/os-release ]]; then
    die "Не удалось определить ОС"
fi
source /etc/os-release
if [[ "$ID" != "ubuntu" && "$ID" != "debian" && "${ID_LIKE:-}" != *"debian"* ]]; then
    warn "Скрипт тестировался на Ubuntu/Debian. Текущая ОС: $PRETTY_NAME"
fi
ok "ОС: $PRETTY_NAME"

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  XRAY_ARCH="64"        ;;
    aarch64) XRAY_ARCH="arm64-v8a" ;;
    armv7l)  XRAY_ARCH="arm32-v7a" ;;
    *)       die "Неподдерживаемая архитектура: $ARCH" ;;
esac
ok "Архитектура: $ARCH → xray-$XRAY_ARCH"

# =============================================================================
# Системные зависимости
# =============================================================================

section "Системные пакеты"

apt-get update -qq

# Python 3.10+
PYTHON_BIN=""
for v in python3.12 python3.11 python3.10; do
    if command -v "$v" &>/dev/null; then
        PYTHON_BIN="$v"; break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    info "Устанавливаем Python 3.11..."
    add-apt-repository -y ppa:deadsnakes/ppa &>/dev/null 2>&1 || true
    apt-get install -y python3.11 python3.11-venv python3.11-dev &>/dev/null
    PYTHON_BIN="python3.11"
fi

apt-get install -y python3-pip python3-venv curl wget unzip jq &>/dev/null
ok "Python: $($PYTHON_BIN --version)"
ok "Системные пакеты установлены"

# =============================================================================
# Скачивание файлов проекта
# =============================================================================

section "Загрузка vlessFinder"

FRESH_INSTALL=false
mkdir -p "$INSTALL_DIR"

info "Скачиваем файлы с ${GITHUB_USER}/${GITHUB_REPO}@${GITHUB_BRANCH}..."
for f in "${PROJECT_FILES[@]}"; do
    download "${REPO_RAW}/${f}" "${INSTALL_DIR}/${f}"
    ok "  ${f}"
done

# config.yaml — скачиваем только при первой установке, чтобы не затереть настройки
if [[ ! -f "$CONFIG_FILE" ]]; then
    download "${REPO_RAW}/config.yaml" "$CONFIG_FILE"
    FRESH_INSTALL=true
    ok "  config.yaml (новый)"
else
    warn "  config.yaml уже существует — сохраняем текущий"
fi

mkdir -p "$LOG_DIR" "$STATE_DIR"
ok "Директории: $LOG_DIR, $STATE_DIR"

# =============================================================================
# Python окружение
# =============================================================================

section "Python окружение"

if [[ -d "$VENV_DIR" ]]; then
    info "Обновляем виртуальное окружение..."
else
    info "Создаём виртуальное окружение..."
fi
"$PYTHON_BIN" -m venv "$VENV_DIR"

info "Устанавливаем зависимости..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
ok "Python зависимости установлены"

# =============================================================================
# Xray-core
# =============================================================================

section "Xray-core"

if [[ -f "$XRAY_BIN" ]]; then
    CURRENT_VER=$("$XRAY_BIN" version 2>/dev/null | head -1 | awk '{print $2}' || echo "?")
    warn "xray уже установлен ($CURRENT_VER) — обновляем до последней версии"
fi

info "Определяем последнюю версию xray-core..."
XRAY_VERSION=$(curl -fsSL "$XRAY_RELEASES" 2>/dev/null | jq -r '.tag_name' 2>/dev/null || echo "")
if [[ -z "$XRAY_VERSION" || "$XRAY_VERSION" == "null" ]]; then
    XRAY_VERSION="v25.3.6"
    warn "Не удалось получить версию через API — используем $XRAY_VERSION"
fi
ok "Версия xray: $XRAY_VERSION"

XRAY_ZIP="Xray-linux-${XRAY_ARCH}.zip"
XRAY_URL="https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${XRAY_ZIP}"

TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

info "Скачиваем xray-core..."
if ! wget -q --show-progress -O "$TMP_DIR/$XRAY_ZIP" "$XRAY_URL"; then
    die "Не удалось скачать xray-core: $XRAY_URL"
fi

unzip -q "$TMP_DIR/$XRAY_ZIP" -d "$TMP_DIR/xray"
install -m 0755 "$TMP_DIR/xray/xray" "$XRAY_BIN"
ok "xray установлен → $XRAY_BIN  ($("$XRAY_BIN" version 2>/dev/null | head -1))"

# =============================================================================
# Конфигурация
# =============================================================================

section "Конфигурация"

# Всегда синхронизируем системные пути (безопасно — не трогает пользовательские данные)
sed -i "s|xray_binary:.*|xray_binary: \"$XRAY_BIN\"|"              "$CONFIG_FILE"
sed -i "s|pid_file:.*|pid_file: \"/var/run/vlessfinder.pid\"|"      "$CONFIG_FILE"
sed -i "s|log_file:.*|log_file: \"$LOG_DIR/vlessfinder.log\"|"      "$CONFIG_FILE"
sed -i "s|state_dir:.*|state_dir: \"$STATE_DIR\"|"                  "$CONFIG_FILE"

ADMIN_TOKEN=""
if [[ "$FRESH_INSTALL" == "true" ]]; then
    # Заменяем placeholder UUID реальным
    ADMIN_TOKEN=$(gen_uuid)
    sed -i "s|${ADMIN_TOKEN_PLACEHOLDER}|${ADMIN_TOKEN}|g" "$CONFIG_FILE"
    ok "Создан пользователь admin (UUID: $ADMIN_TOKEN)"
else
    # Читаем существующий токен admin для вывода в итоге
    ADMIN_TOKEN=$(grep -A2 'admin:' "$CONFIG_FILE" | grep 'token:' | awk -F'"' '{print $2}' || echo "см. config.yaml")
    ok "Конфиг сохранён без изменений"
fi

ok "Пути обновлены: $CONFIG_FILE"

# =============================================================================
# Команда vlessfinder
# =============================================================================

section "Команда vlessfinder"

cat > "$INSTALL_DIR/vlessfinder.sh" << SHEOF
#!/usr/bin/env bash
exec "${VENV_DIR}/bin/python" "${INSTALL_DIR}/main.py" --config "${CONFIG_FILE}" "\$@"
SHEOF
chmod +x "$INSTALL_DIR/vlessfinder.sh"
ln -sf "$INSTALL_DIR/vlessfinder.sh" "$WRAPPER"
ok "Команда доступна глобально: vlessfinder"

# =============================================================================
# Systemd сервис
# =============================================================================

section "Systemd сервис"

WAS_RUNNING=false
if systemctl is-active --quiet vlessfinder 2>/dev/null; then
    WAS_RUNNING=true
    info "Останавливаем текущий сервис для обновления..."
    systemctl stop vlessfinder || true
fi

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=vlessFinder — VLESS subscription manager
Documentation=https://github.com/${GITHUB_USER}/${GITHUB_REPO}
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
PIDFile=/var/run/vlessfinder.pid
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/main.py --config ${CONFIG_FILE} start
ExecStop=${VENV_DIR}/bin/python ${INSTALL_DIR}/main.py --config ${CONFIG_FILE} stop
ExecReload=${VENV_DIR}/bin/python ${INSTALL_DIR}/main.py --config ${CONFIG_FILE} restart
Restart=on-failure
RestartSec=10s
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vlessfinder &>/dev/null
ok "Systemd сервис зарегистрирован (автозапуск при загрузке)"

if [[ "$WAS_RUNNING" == "true" ]]; then
    systemctl start vlessfinder
    ok "Сервис перезапущен"
fi

# =============================================================================
# Итог
# =============================================================================

PORT=$(grep -E '^\s+port:' "$CONFIG_FILE" | head -1 | awk '{print $2}' || echo "8080")
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_SERVER_IP")

echo ""
echo -e "${BOLD}══════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  vlessFinder успешно установлен!${RESET}"
echo -e "${BOLD}══════════════════════════════════════════${RESET}"
echo ""
echo -e "${BOLD}Управление:${RESET}"
echo -e "  vlessfinder start              ${CYAN}# запуск${RESET}"
echo -e "  vlessfinder stop               ${CYAN}# остановка${RESET}"
echo -e "  vlessfinder restart            ${CYAN}# перезапуск${RESET}"
echo -e "  vlessfinder status             ${CYAN}# статус + URL подписок${RESET}"
echo ""
echo -e "${BOLD}Пользователи:${RESET}"
echo -e "  vlessfinder add-user <имя>     ${CYAN}# добавить пользователя${RESET}"
echo -e "  vlessfinder remove-user <имя>  ${CYAN}# удалить пользователя${RESET}"
echo ""
echo -e "${BOLD}Конфиг:${RESET}  nano $CONFIG_FILE"
echo -e "${BOLD}Логи:${RESET}    tail -f $LOG_DIR/vlessfinder.log"
echo ""
echo -e "${BOLD}URL подписок:${RESET}"
echo -e "  admin  →  ${GREEN}http://${HOST_IP}:${PORT}/sub/${ADMIN_TOKEN}${RESET}"
echo ""

if [[ "$FRESH_INSTALL" == "true" ]]; then
    echo -e "${YELLOW}Перед запуском отредактируйте репозитории в конфиге:${RESET}"
    echo -e "  nano $CONFIG_FILE"
    echo -e "  # Укажите свои GitHub raw URLs в разделе repos:"
    echo ""
fi

echo -e "${GREEN}Запуск:${RESET}"
echo -e "  vlessfinder start"
echo ""
