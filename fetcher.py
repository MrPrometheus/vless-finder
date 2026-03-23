"""
fetcher.py — загружает VLESS ключи из публичных репозиториев GitHub.

Поддерживает:
- Обычный текстовый формат (один ключ на строку)
- Base64-закодированный контент (декодируется автоматически)
- Дедупликация по raw URL
"""

import asyncio
import base64
import re
from urllib.parse import urlparse, parse_qs
from typing import Any
import httpx

VLESS_RE = re.compile(r'vless://[^\s\r\n]+')


def _try_decode_b64(text: str) -> str:
    """Если весь контент выглядит как base64 — декодирует, иначе возвращает как есть."""
    stripped = text.strip()
    try:
        if re.fullmatch(r'[A-Za-z0-9+/=\n\r]+', stripped):
            decoded = base64.b64decode(stripped + '==').decode('utf-8', errors='replace')
            if 'vless://' in decoded or 'vmess://' in decoded:
                return decoded
    except Exception:
        pass
    return text


def parse_vless_url(raw: str) -> dict[str, Any] | None:
    """
    Парсит vless:// строку в структурированный словарь.
    Возвращает None при ошибке парсинга.

    Формат: vless://uuid@host:port?params#name
    Параметры: security, sni, fp, alpn, type, path, host, serviceName, pbk, sid, spx
    """
    try:
        parsed = urlparse(raw.strip())
        if parsed.scheme != 'vless':
            return None

        uuid = parsed.username
        host = parsed.hostname
        port = int(parsed.port or 443)
        name = parsed.fragment or ''

        q = parse_qs(parsed.query, keep_blank_values=True)

        def _first(key: str, default: str = '') -> str:
            vals = q.get(key, [default])
            return vals[0] if vals else default

        security = _first('security', 'none')
        network  = _first('type', 'tcp')

        result: dict[str, Any] = {
            'raw':          raw.strip(),
            'uuid':         uuid,
            'host':         host,
            'port':         port,
            'name':         name,
            # TLS / Reality
            'encryption':   _first('encryption', 'none'),
            'security':     security,           # none | tls | reality
            'sni':          _first('sni', host or ''),
            'fp':           _first('fp', 'chrome'),
            'alpn':         _first('alpn', ''),
            'allow_insecure': _first('allowInsecure', '0') == '1',
            # Reality
            'pbk':          _first('pbk', ''),
            'sid':          _first('sid', ''),
            'spx':          _first('spx', '/'),
            # Transport
            'network':      network,            # tcp | ws | grpc | http
            'path':         _first('path', '/'),
            'host_header':  _first('host', ''),
            'service_name': _first('serviceName', ''),
        }

        if not uuid or not host or not port:
            return None

        return result
    except Exception:
        return None


async def _fetch_one(client: httpx.AsyncClient, url: str) -> list[dict]:
    """Загружает один репозиторий с 3 попытками, возвращает список распарсенных ключей."""
    for attempt in range(3):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            text = _try_decode_b64(resp.text)
            matches = VLESS_RE.findall(text)
            parsed = [parse_vless_url(m) for m in matches]
            result = [p for p in parsed if p is not None]
            print(f"[fetcher] {url}: {len(result)} ключей")
            return result
        except Exception as e:
            if attempt == 2:
                print(f"[fetcher] Ошибка загрузки {url}: {e}")
            await asyncio.sleep(2 ** attempt)
    return []


async def fetch_vless_keys(repos: list[str]) -> list[dict]:
    """
    Параллельно загружает все репозитории.
    Возвращает дедуплицированный список распарсенных VLESS ключей.
    """
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        tasks = [_fetch_one(client, url) for url in repos]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    seen_raw: set[str] = set()
    keys: list[dict] = []

    for batch in results:
        if isinstance(batch, Exception):
            continue
        for key in batch:
            if key['raw'] not in seen_raw:
                seen_raw.add(key['raw'])
                keys.append(key)

    return keys
