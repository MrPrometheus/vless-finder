# vlessFinder

Менеджер VLESS подписок. Автоматически находит рабочие ключи из публичных репозиториев и раздаёт их пользователям через статичные URL подписок, совместимые с любым xray/v2ray клиентом.

## Как это работает

```
GitHub репозитории → fetch → TCP проверка → прокси-тест (xray) → рабочие ключи → подписки
```

1. По расписанию (каждые 30 минут) загружает VLESS ключи из указанных репозиториев
2. Проверяет каждый ключ — сначала TCP connect, затем полный прокси-тест через xray-core
3. Раздаёт рабочие ключи пользователям по статичным URL подписок
4. Клиент (v2rayNG, Shadowrocket, Happ и др.) сам обновляет подписку — всегда получает актуальные ключи

---

## Установка на Ubuntu / Debian

### Требования

- Ubuntu 20.04+ или Debian 11+
- Root доступ
- Подключение к интернету

### Одна команда

```bash
bash <(curl -Ls https://raw.githubusercontent.com/vless-finder/vlessFinder/main/install.sh)
```

> Замените `vless-finder` на ваш GitHub username.

Скрипт автоматически:
- Скачает все файлы проекта с GitHub
- Установит Python 3.10+ (если нужно)
- Скачает и установит актуальный [xray-core](https://github.com/XTLS/Xray-core)
- Создаст Python venv и установит зависимости
- Сгенерирует UUID для пользователя `admin`
- Создаст команду `vlessfinder` (доступна глобально)
- Зарегистрирует systemd сервис с автозапуском

После установки на экране будут показаны URL подписок.

### Обновление (повторный запуск той же команды)

```bash
bash <(curl -Ls https://raw.githubusercontent.com/vless-finder/vlessFinder/main/install.sh)
```

При повторной установке:
- Код обновляется до последней версии
- `config.yaml` **не перезаписывается** — все ваши пользователи и настройки сохраняются
- xray-core обновляется до последней версии
- Если сервис был запущен — перезапускается автоматически

---

## Настройка

Конфиг находится по адресу `/opt/vlessfinder/config.yaml`.

```yaml
# Репозитории с VLESS ключами (GitHub raw URLs)
repos:
  - "https://raw.githubusercontent.com/user/repo/main/keys.txt"
  - "https://raw.githubusercontent.com/user2/repo2/main/vless.txt"

# Пользователи и их подписки
users:
  admin:
    token: "a8b2545c-9b29-43b0-86aa-c18175cbdcc8"  # UUID = часть URL подписки
    keys_count: 3                                    # сколько ключей в подписке

# Настройки валидации
validation:
  tcp_timeout: 3.0             # таймаут TCP connect (сек)
  proxy_test_timeout: 10.0     # таймаут прокси-теста (сек)
  max_proxy_concurrency: 3     # параллельных xray процессов

# HTTP сервер
server:
  host: "0.0.0.0"
  port: 8080

# Планировщик
scheduler:
  refresh_interval_minutes: 30
```

> **Важно:** URL подписки формируется как `http://<IP>:8080/sub/<token>`.
> Токен — UUID пользователя из конфига. URL никогда не меняется — клиент сохраняет его один раз.

---

## Управление сервисом

```bash
# Запуск / остановка / перезапуск
vlessfinder start
vlessfinder stop
vlessfinder restart

# Статус + URL всех подписок
vlessfinder status

# Запуск в терминале (для отладки, без ухода в фон)
vlessfinder start --foreground

# Через systemd
systemctl start vlessfinder
systemctl stop vlessfinder
systemctl restart vlessfinder
systemctl status vlessfinder
```

---

## Управление пользователями

UUID генерируется автоматически — вам нужно только указать имя.

```bash
# Добавить пользователя (5 ключей по умолчанию)
vlessfinder add-user john

# Добавить с другим количеством ключей
vlessfinder add-user john --keys-count 3

# Добавить и сразу перезапустить сервис
vlessfinder add-user john --restart

# Удалить пользователя
vlessfinder remove-user john

# Удалить и сразу перезапустить сервис
vlessfinder remove-user john --restart
```

Пример вывода `add-user`:

```
Пользователь 'john' добавлен.
  Токен:     7f3a1b2c-4d5e-6f7a-8b9c-0d1e2f3a4b5c
  Подписка:  http://1.2.3.4:8080/sub/7f3a1b2c-4d5e-6f7a-8b9c-0d1e2f3a4b5c
  Ключей:    5
```

Изменения сохраняются в `config.yaml` и применяются после перезапуска.

---

## URL подписок

Посмотреть все URL можно командой:

```bash
vlessfinder status
```

Пример вывода:

```
vlessFinder: запущен (PID 12345)
  Адрес:      http://0.0.0.0:8080
  Статистика: http://0.0.0.0:8080/stats

  Подписки:
    admin        → http://0.0.0.0:8080/sub/a8b2545c-9b29-43b0-86aa-c18175cbdcc8
    john         → http://0.0.0.0:8080/sub/7f3a1b2c-4d5e-6f7a-8b9c-0d1e2f3a4b5c
```

### Добавление подписки в клиент

| Клиент | Как добавить |
|--------|-------------|
| **v2rayNG** | ✚ → Импорт из URL |
| **Shadowrocket** | ✚ → Subscribe |
| **Happ** | Subscription → Add → вставить URL |
| **NekoBox / NekoRay** | Подписки → Добавить |
| **Hiddify** | Добавить конфигурацию → URL подписки |
| **Streisand** | Вставить URL в поле подписки |

Формат ответа — стандартная V2Ray/Xray подписка: `base64(url1\r\nurl2\r\n...)`.
Заголовок `profile-update-interval` задаёт интервал автообновления в клиенте.

---

## HTTP API

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/sub/{token}` | Подписка пользователя (base64) |
| `GET` | `/health` | Проверка доступности сервера |
| `GET` | `/stats` | Статистика: кол-во ключей по пользователям |

Пример `/stats`:

```json
{
  "total_working_keys": 15,
  "users": {
    "admin": 3,
    "john": 5
  }
}
```

---

## Логи

```bash
# Следить за логом в реальном времени
tail -f /var/log/vlessfinder/vlessfinder.log

# Логи systemd
journalctl -u vlessfinder -f
```

Пример лога цикла обновления:

```
2025-01-15 10:00:00 [INFO] === Цикл обновления ===
2025-01-15 10:00:02 [INFO] Загружено уникальных ключей: 47
2025-01-15 10:00:18 [INFO] Рабочих ключей после валидации: 12
2025-01-15 10:00:18 [INFO]   admin: 3 ключей
2025-01-15 10:00:18 [INFO]   john: 5 ключей
```

---

## Структура файлов

```
/opt/vlessfinder/          # установочная директория
├── main.py                # точка входа, CLI, демон
├── fetcher.py             # загрузка ключей из репозиториев
├── validator.py           # TCP + прокси-тест через xray
├── subscription_manager.py # хранение и раздача подписок
├── server.py              # HTTP сервер (FastAPI)
├── config.yaml            # конфигурация
└── .venv/                 # Python виртуальное окружение

/var/lib/vlessfinder/      # рабочие данные
├── working_keys.yaml      # последние рабочие ключи
└── subscriptions.yaml     # назначения: token → список ключей

/var/log/vlessfinder/
└── vlessfinder.log        # лог

/usr/local/bin/vlessfinder # симлинк на команду
/etc/systemd/system/vlessfinder.service
```

---

## Обновление

Та же команда, что и для установки:

```bash
bash <(curl -Ls https://raw.githubusercontent.com/vless-finder/vlessFinder/main/install.sh)
```

---

## Удаление

```bash
bash <(curl -Ls https://raw.githubusercontent.com/vless-finder/vlessFinder/main/install.sh) uninstall
```

Скрипт спросит подтверждение, затем удалит:
- systemd сервис
- команду `vlessfinder`
- директорию `/opt/vlessfinder`
- рабочие данные `/var/lib/vlessfinder`
- логи `/var/log/vlessfinder`
- xray-core (опционально — отдельный вопрос)
