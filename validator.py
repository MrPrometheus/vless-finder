"""
validator.py — двухэтапная проверка VLESS ключей.

Этап 1: TCP connect (параллельно, все ключи)
Этап 2: Прокси-тест через xray subprocess + SOCKS5 (конкурентность = 3)
"""

import asyncio
import json
import os
import tempfile
from typing import Any

import httpx

# Счётчик портов для SOCKS5-инпойнтов xray (чтобы параллельные процессы не конфликтовали)
_PORT_COUNTER = 10800
_PORT_LOCK = asyncio.Lock()


async def _next_port(start: int) -> int:
    global _PORT_COUNTER
    async with _PORT_LOCK:
        p = _PORT_COUNTER
        _PORT_COUNTER += 1
        if _PORT_COUNTER >= start + 200:
            _PORT_COUNTER = start
        return p


# ---------------------------------------------------------------------------
# xray config builder
# ---------------------------------------------------------------------------

def _build_stream_settings(key: dict[str, Any]) -> dict:
    """
    Переводит поля распарсенного VLESS ключа в xray streamSettings.
    Поддерживаемые транспорты: tcp, ws, grpc, http (h2)
    Поддерживаемые security: none, tls, reality
    """
    network  = key['network']
    security = key['security']

    # --- Транспортный слой ---
    if network == 'ws':
        network_settings: dict = {
            'wsSettings': {
                'path': key['path'],
                'headers': {
                    'Host': key['host_header'] or key['sni'] or key['host']
                }
            }
        }
    elif network == 'grpc':
        network_settings = {
            'grpcSettings': {
                'serviceName': key['service_name'],
                'multiMode': False
            }
        }
    elif network == 'http':  # HTTP/2
        network_settings = {
            'httpSettings': {
                'path': key['path'],
                'host': [key['host_header'] or key['host']]
            }
        }
    else:  # tcp (по умолчанию)
        network_settings = {
            'tcpSettings': {
                'header': {'type': 'none'}
            }
        }

    # --- TLS слой ---
    if security == 'tls':
        tls_settings: dict = {
            'tlsSettings': {
                'serverName':    key['sni'] or key['host'],
                'fingerprint':   key['fp'] or 'chrome',
                'alpn':          key['alpn'].split(',') if key['alpn'] else ['h2', 'http/1.1'],
                'allowInsecure': key['allow_insecure']
            }
        }
    elif security == 'reality':
        tls_settings = {
            'realitySettings': {
                'serverName':  key['sni'] or key['host'],
                'fingerprint': key['fp'] or 'chrome',
                'publicKey':   key['pbk'],
                'shortId':     key['sid'],
                'spiderX':     key['spx'] or '/'
            }
        }
    else:
        tls_settings = {}

    return {
        'network':  network,
        **network_settings,
        'security': security,
        **tls_settings
    }


def _build_xray_config(key: dict[str, Any], socks_port: int) -> dict:
    """
    Генерирует минимальный xray config для тестирования одного VLESS ключа.
    Inbound:  SOCKS5 на 127.0.0.1:{socks_port}
    Outbound: VLESS на сервер из ключа
    """
    return {
        'log': {'loglevel': 'none'},
        'inbounds': [
            {
                'listen':   '127.0.0.1',
                'port':     socks_port,
                'protocol': 'socks',
                'settings': {
                    'auth': 'noauth',
                    'udp':  False
                }
            }
        ],
        'outbounds': [
            {
                'protocol': 'vless',
                'settings': {
                    'vnext': [
                        {
                            'address': key['host'],
                            'port':    key['port'],
                            'users': [
                                {
                                    'id':         key['uuid'],
                                    'encryption': key['encryption'] or 'none',
                                    'flow':       ''
                                }
                            ]
                        }
                    ]
                },
                'streamSettings': _build_stream_settings(key)
            },
            {
                'protocol': 'freedom',
                'tag':      'direct'
            }
        ]
    }


# ---------------------------------------------------------------------------
# TCP check
# ---------------------------------------------------------------------------

async def _tcp_check_one(host: str, port: int, timeout: float) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def tcp_check_bulk(keys: list[dict], timeout: float) -> list[dict]:
    """Проверяет все ключи по TCP параллельно, возвращает прошедшие."""
    tasks = [_tcp_check_one(k['host'], k['port'], timeout) for k in keys]
    results = await asyncio.gather(*tasks)
    return [k for k, ok in zip(keys, results) if ok]


# ---------------------------------------------------------------------------
# Proxy test (xray subprocess)
# ---------------------------------------------------------------------------

async def _proxy_test_one(
    key:       dict[str, Any],
    cfg:       dict,
    semaphore: asyncio.Semaphore
) -> bool:
    """
    Запускает xray с временным конфигом, проверяет HTTP запрос через SOCKS5.
    Возвращает True если запрос прошёл успешно (HTTP < 400).
    """
    port_start    = cfg['validation']['proxy_local_port_start']
    xray_binary   = cfg['validation']['xray_binary']
    startup_wait  = cfg['validation']['xray_startup_wait']
    test_url      = cfg['validation']['proxy_test_url']
    timeout       = cfg['validation']['proxy_test_timeout']

    socks_port  = await _next_port(port_start)
    xray_config = _build_xray_config(key, socks_port)

    async with semaphore:
        tmp_path: str | None = None
        proc = None
        try:
            # Записываем временный конфиг
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False, encoding='utf-8'
            ) as tmp:
                json.dump(xray_config, tmp)
                tmp_path = tmp.name

            # Запускаем xray
            proc = await asyncio.create_subprocess_exec(
                xray_binary, 'run', '-c', tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )

            # Ждём инициализации listener'а
            await asyncio.sleep(startup_wait)

            # Тест через SOCKS5 прокси
            proxy_url = f'socks5://127.0.0.1:{socks_port}'
            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    timeout=timeout,
                    follow_redirects=True
                ) as client:
                    resp = await client.get(test_url)
                    return resp.status_code < 400
            except Exception:
                return False

        except FileNotFoundError:
            print(f"[validator] xray binary '{xray_binary}' не найден")
            return False
        except Exception as e:
            print(f"[validator] Ошибка прокси-теста для {key['host']}: {e}")
            return False
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    proc.kill()
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def validate_keys(keys: list[dict], cfg: dict) -> list[dict]:
    """
    Полный пайплайн валидации:
    1. TCP check всех ключей параллельно
    2. Прокси-тест выживших (конкурентность = max_proxy_concurrency)

    Возвращает список полностью проверенных рабочих ключей.
    """
    tcp_timeout      = cfg['validation']['tcp_timeout']
    max_concurrency  = cfg['validation']['max_proxy_concurrency']

    print(f"[validator] TCP проверка {len(keys)} ключей...")
    tcp_alive = await tcp_check_bulk(keys, tcp_timeout)
    print(f"[validator] Прошли TCP: {len(tcp_alive)}")

    if not tcp_alive:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)
    tasks = [_proxy_test_one(k, cfg, semaphore) for k in tcp_alive]

    print(f"[validator] Прокси-тест {len(tcp_alive)} ключей (concurrency={max_concurrency})...")
    results = await asyncio.gather(*tasks)

    working = [k for k, ok in zip(tcp_alive, results) if ok]
    print(f"[validator] Рабочих ключей: {len(working)}")
    return working
