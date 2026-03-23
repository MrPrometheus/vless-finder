#!/usr/bin/env python3
"""
vlessFinder — менеджер VLESS подписок

Использование:
  python main.py start              # запуск в фоне (daemon)
  python main.py start --foreground # запуск в терминале (для отладки)
  python main.py stop               # остановка
  python main.py restart            # перезапуск
  python main.py status             # статус процесса
  python main.py add-user <имя>     # добавить пользователя (UUID генерируется автоматически)
  python main.py remove-user <имя>  # удалить пользователя

Флаги:
  -c, --config PATH   путь к конфигу (по умолчанию: config.yaml)
  -f, --foreground    не уходить в фон (только для start)
"""

import argparse
import asyncio
import datetime
import logging
import os
import signal
import sys
import uuid
from pathlib import Path

import yaml
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        print(f"[vlessFinder] Конфиг не найден: {path}", file=sys.stderr)
        sys.exit(1)
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_file: str, foreground: bool) -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding='utf-8')
    ]
    if foreground:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers
    )


log = logging.getLogger('vlessFinder')


# ---------------------------------------------------------------------------
# Refresh cycle
# ---------------------------------------------------------------------------

async def refresh_cycle(cfg: dict) -> None:
    from fetcher import fetch_vless_keys
    from validator import validate_keys
    from subscription_manager import refresh_subscriptions, load_working_keys

    log.info(f"=== Цикл обновления ===")
    try:
        keys = await fetch_vless_keys(cfg['repos'])
        log.info(f"Загружено уникальных ключей: {len(keys)}")

        if not keys:
            log.warning("Ключи не найдены — пропускаем валидацию")
            return

        working = await validate_keys(keys, cfg)

        if not working:
            log.warning("Нет рабочих ключей — подписки не обновлены (оставлены предыдущие)")
            return

        assignments = refresh_subscriptions(working, cfg)
        for token, user_keys in assignments.items():
            # Найти имя пользователя по токену
            name = next(
                (n for n, u in cfg['users'].items() if u.get('token') == token),
                token[:8] + '...'
            )
            log.info(f"  {name}: {len(user_keys)} ключей")

    except Exception as e:
        log.error(f"Ошибка цикла обновления: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------

def run_server(cfg: dict) -> None:
    """Блокирующий запуск uvicorn + APScheduler в одном event loop."""
    from server import app
    from subscription_manager import load_working_keys

    app.state.cfg = cfg

    existing = load_working_keys(cfg)
    if existing:
        log.info(f"Загружено {len(existing)} ключей из предыдущего сеанса")

    interval = cfg['scheduler']['refresh_interval_minutes']
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        refresh_cycle,
        trigger='interval',
        minutes=interval,
        args=[cfg],
        id='refresh',
        next_run_time=datetime.datetime.now()  # первый запуск сразу
    )

    @app.on_event("startup")
    async def _startup():
        scheduler.start()
        log.info(f"Планировщик запущен (интервал: {interval} мин)")

    @app.on_event("shutdown")
    async def _shutdown():
        scheduler.shutdown(wait=False)

    host = cfg['server']['host']
    port = cfg['server']['port']
    log.info(f"HTTP сервер: http://{host}:{port}")

    # Вывод подписок в лог при старте
    for name, user in cfg['users'].items():
        token = user.get('token', '')
        log.info(f"  Подписка {name}: http://<host>:{port}/sub/{token}")

    uvicorn.run(app, host=host, port=port, log_level='warning')


# ---------------------------------------------------------------------------
# Daemonize (Linux)
# ---------------------------------------------------------------------------

def daemonize(pid_file: str, log_file: str) -> None:
    """Двойной fork для создания daemon-процесса."""
    # Fork 1
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        print(f"Fork #1 failed: {e}", file=sys.stderr)
        sys.exit(1)

    os.setsid()
    os.umask(0o022)

    # Fork 2
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        print(f"Fork #2 failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Перенаправляем stdio в /dev/null и лог-файл
    sys.stdout.flush()
    sys.stderr.flush()

    with open('/dev/null', 'r') as f:
        os.dup2(f.fileno(), sys.stdin.fileno())

    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_file, 'a') as f:
        os.dup2(f.fileno(), sys.stdout.fileno())
        os.dup2(f.fileno(), sys.stderr.fileno())

    # Записываем PID
    pid_dir = Path(pid_file).parent
    pid_dir.mkdir(parents=True, exist_ok=True)
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))


# ---------------------------------------------------------------------------
# PID helpers
# ---------------------------------------------------------------------------

def read_pid(pid_file: str) -> int | None:
    try:
        with open(pid_file, 'r') as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    cfg      = load_config(args.config)
    pid_file = cfg['paths']['pid_file']
    log_file = cfg['paths']['log_file']

    # Проверяем не запущен ли уже
    pid = read_pid(pid_file)
    if pid and is_running(pid):
        print(f"vlessFinder уже запущен (PID {pid})")
        sys.exit(1)

    setup_logging(log_file, foreground=args.foreground)

    if args.foreground:
        print(f"[vlessFinder] Запуск в терминале (Ctrl+C для остановки)")
        run_server(cfg)
    else:
        # Проверка: только Linux поддерживает os.fork
        if not hasattr(os, 'fork'):
            print("Daemon режим не поддерживается на этой ОС. Используйте --foreground")
            sys.exit(1)

        daemonize(pid_file, log_file)
        setup_logging(log_file, foreground=False)
        log.info(f"vlessFinder запущен (PID {os.getpid()})")
        run_server(cfg)


def cmd_stop(args: argparse.Namespace) -> None:
    cfg      = load_config(args.config)
    pid_file = cfg['paths']['pid_file']

    pid = read_pid(pid_file)
    if not pid:
        print("vlessFinder не запущен (PID файл не найден)")
        sys.exit(1)

    if not is_running(pid):
        print(f"vlessFinder не запущен (PID {pid} не существует)")
        try:
            os.unlink(pid_file)
        except FileNotFoundError:
            pass
        sys.exit(1)

    print(f"Остановка vlessFinder (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    # Ждём завершения (до 10 сек)
    import time
    for _ in range(20):
        time.sleep(0.5)
        if not is_running(pid):
            try:
                os.unlink(pid_file)
            except FileNotFoundError:
                pass
            print("vlessFinder остановлен")
            return

    # Принудительная остановка
    print("Принудительная остановка (SIGKILL)...")
    os.kill(pid, signal.SIGKILL)
    try:
        os.unlink(pid_file)
    except FileNotFoundError:
        pass
    print("vlessFinder остановлен")


def cmd_restart(args: argparse.Namespace) -> None:
    cmd_stop(args)
    import time
    time.sleep(1)
    cmd_start(args)


def cmd_status(args: argparse.Namespace) -> None:
    cfg      = load_config(args.config)
    pid_file = cfg['paths']['pid_file']

    pid = read_pid(pid_file)
    if not pid:
        print("vlessFinder: не запущен")
        sys.exit(1)

    if is_running(pid):
        host = cfg['server']['host']
        port = cfg['server']['port']
        print(f"vlessFinder: запущен (PID {pid})")
        print(f"  Адрес:      http://{host}:{port}")
        print(f"  Статистика: http://{host}:{port}/stats")
        print()
        print("  Подписки:")
        for name, user in cfg['users'].items():
            token = user.get('token', '???')
            print(f"    {name:12} → http://{host}:{port}/sub/{token}")
    else:
        print(f"vlessFinder: не запущен (устаревший PID {pid})")
        try:
            os.unlink(pid_file)
        except FileNotFoundError:
            pass
        sys.exit(1)


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

def _save_config(path: str, cfg: dict) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _restart_if_needed(args: argparse.Namespace, cfg: dict) -> None:
    """Перезапускает сервис если он запущен, иначе выводит подсказку."""
    pid_file = cfg['paths']['pid_file']
    pid = read_pid(pid_file)
    if pid and is_running(pid):
        if getattr(args, 'restart', False):
            print("Перезапуск vlessFinder...")
            cmd_restart(args)
        else:
            print()
            print("  Подсказка: сервис запущен. Чтобы применить изменения:")
            print(f"    vlessfinder restart -c {args.config}")
    else:
        if getattr(args, 'restart', False):
            print("  (Сервис не запущен — перезапуск пропущен)")


def cmd_add_user(args: argparse.Namespace) -> None:
    name = args.name
    keys_count = args.keys_count

    cfg = load_config(args.config)
    users = cfg.setdefault('users', {})

    if name in users:
        print(f"Пользователь '{name}' уже существует.")
        sys.exit(1)

    token = str(uuid.uuid4())
    users[name] = {'token': token, 'keys_count': keys_count}
    _save_config(args.config, cfg)

    host = cfg['server']['host']
    port = cfg['server']['port']
    display_host = 'your-server' if host == '0.0.0.0' else host
    print(f"Пользователь '{name}' добавлен.")
    print(f"  Токен:     {token}")
    print(f"  Подписка:  http://{display_host}:{port}/sub/{token}")
    print(f"  Ключей:    {keys_count}")

    _restart_if_needed(args, cfg)


def cmd_remove_user(args: argparse.Namespace) -> None:
    name = args.name

    cfg = load_config(args.config)
    users = cfg.get('users', {})

    if name not in users:
        print(f"Пользователь '{name}' не найден.")
        sys.exit(1)

    token = users[name].get('token', '')
    del users[name]
    _save_config(args.config, cfg)

    print(f"Пользователь '{name}' удалён (токен: {token}).")

    _restart_if_needed(args, cfg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog='vlessfinder',
        description='VLESS subscription manager',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py start                       # запуск daemon
  python main.py start --foreground          # запуск в терминале
  python main.py start -c /etc/vlessfinder/config.yaml
  python main.py stop
  python main.py restart
  python main.py status
  python main.py add-user john               # добавить пользователя john (5 ключей)
  python main.py add-user john --keys-count 3
  python main.py add-user john --restart     # добавить и сразу перезапустить сервис
  python main.py remove-user john
  python main.py remove-user john --restart  # удалить и сразу перезапустить сервис
        """
    )
    parser.add_argument(
        '-c', '--config',
        default='config.yaml',
        metavar='PATH',
        help='путь к конфигу (по умолчанию: config.yaml)'
    )

    subparsers = parser.add_subparsers(dest='command', metavar='COMMAND')
    subparsers.required = True

    # start
    p_start = subparsers.add_parser('start', help='запустить vlessFinder')
    p_start.add_argument(
        '-f', '--foreground',
        action='store_true',
        help='запустить в терминале (не в фоне)'
    )
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser('stop', help='остановить vlessFinder')
    p_stop.set_defaults(func=cmd_stop)

    # restart
    p_restart = subparsers.add_parser('restart', help='перезапустить vlessFinder')
    p_restart.add_argument('-f', '--foreground', action='store_true')
    p_restart.set_defaults(func=cmd_restart)

    # status
    p_status = subparsers.add_parser('status', help='статус и адреса подписок')
    p_status.set_defaults(func=cmd_status)

    # add-user
    p_add = subparsers.add_parser('add-user', help='добавить пользователя (UUID генерируется автоматически)')
    p_add.add_argument('name', help='имя пользователя')
    p_add.add_argument('--keys-count', type=int, default=5, metavar='N',
                       help='количество ключей в подписке (по умолчанию: 5)')
    p_add.add_argument('-r', '--restart', action='store_true',
                       help='перезапустить сервис после изменений')
    p_add.set_defaults(func=cmd_add_user)

    # remove-user
    p_remove = subparsers.add_parser('remove-user', help='удалить пользователя')
    p_remove.add_argument('name', help='имя пользователя')
    p_remove.add_argument('-r', '--restart', action='store_true',
                          help='перезапустить сервис после изменений')
    p_remove.set_defaults(func=cmd_remove_user)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
