"""
server.py — HTTP сервер подписок.

GET /sub/{token}  → base64 список VLESS ключей пользователя
GET /health       → статус сервера
GET /stats        → количество ключей у каждого пользователя (без содержимого)
"""

import base64

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse

# cfg инжектируется при старте через app.state
app = FastAPI(title="vlessFinder", docs_url=None, redoc_url=None)


def _load_subs(request: Request) -> dict:
    from subscription_manager import load_subscriptions
    return load_subscriptions(request.app.state.cfg)


def _load_keys(request: Request) -> list:
    from subscription_manager import load_working_keys
    return load_working_keys(request.app.state.cfg)


@app.get("/sub/{token}")
async def get_subscription(token: str, request: Request) -> PlainTextResponse:
    """
    Подписка пользователя по токену (UUID из config.yaml).

    Формат ответа: стандартная V2Ray/Xray подписка — base64(url1\r\nurl2\r\n...)
    Совместимо с: v2rayNG, Shadowrocket, NekoBox, NekoRay, Hiddify, Clash.Meta,
                  Streisand, Karing и другими xray/sing-box клиентами.
    """
    subscriptions = _load_subs(request)

    if token not in subscriptions:
        raise HTTPException(status_code=404, detail="Токен не найден")

    keys: list[str] = subscriptions[token]
    if not keys:
        raise HTTPException(
            status_code=503,
            detail="Нет рабочих ключей. Ожидайте следующего цикла обновления."
        )

    # Стандартный формат: CRLF между ключами, base64 без переносов строк
    raw_list = '\r\n'.join(keys)
    encoded  = base64.b64encode(raw_list.encode('utf-8')).decode('ascii')

    # Интервал обновления берём из конфига (в часах)
    cfg      = request.app.state.cfg
    interval = cfg['scheduler']['refresh_interval_minutes'] // 60 or 1

    return PlainTextResponse(
        content=encoded,
        headers={
            # Стандартные заголовки подписки xray/v2ray
            'profile-update-interval':  str(interval),
            'subscription-userinfo':    'upload=0; download=0; total=0; expire=0',
            # HTTP cache
            'Cache-Control':            'no-cache, no-store, must-revalidate',
            'Pragma':                   'no-cache',
        }
    )


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({'status': 'ok'})


@app.get("/stats")
async def stats(request: Request) -> JSONResponse:
    """Статистика без раскрытия ключей."""
    subscriptions = _load_subs(request)
    working_keys  = _load_keys(request)

    cfg   = request.app.state.cfg
    users = cfg.get('users', {})

    # Строим обратный маппинг token → username
    token_to_name = {u['token']: name for name, u in users.items() if 'token' in u}

    return JSONResponse({
        'total_working_keys': len(working_keys),
        'users': {
            token_to_name.get(token, token): len(keys)
            for token, keys in subscriptions.items()
        }
    })
