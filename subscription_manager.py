"""
subscription_manager.py — управление подписками пользователей.

Файлы хранения (путь задаётся в config.yaml → paths.state_dir):
  working_keys.yaml  — все рабочие ключи после валидации
  subscriptions.yaml — назначенные ключи: { token: [raw_url, ...] }
"""

import random
from pathlib import Path
import yaml


def _state_dir(cfg: dict) -> Path:
    return Path(cfg.get('paths', {}).get('state_dir', 'state'))


def _working_keys_file(cfg: dict) -> Path:
    return _state_dir(cfg) / 'working_keys.yaml'


def _subscriptions_file(cfg: dict) -> Path:
    return _state_dir(cfg) / 'subscriptions.yaml'


def _ensure_state_dir(cfg: dict) -> None:
    _state_dir(cfg).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Working keys
# ---------------------------------------------------------------------------

def save_working_keys(keys: list[dict], cfg: dict) -> None:
    _ensure_state_dir(cfg)
    records = [
        {
            'raw':      k['raw'],
            'host':     k['host'],
            'port':     k['port'],
            'name':     k['name'],
            'network':  k['network'],
            'security': k['security']
        }
        for k in keys
    ]
    with open(_working_keys_file(cfg), 'w', encoding='utf-8') as f:
        yaml.dump({'keys': records}, f, allow_unicode=True)


def load_working_keys(cfg: dict) -> list[dict]:
    path = _working_keys_file(cfg)
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data.get('keys', [])


# ---------------------------------------------------------------------------
# Subscription assignment
# ---------------------------------------------------------------------------

def assign_keys_to_users(working_keys: list[dict], cfg: dict) -> dict[str, list[str]]:
    """
    Назначает ключи по токенам пользователей.
    Возвращает: { token: [raw_vless_url, ...] }
    """
    raw_urls = [k['raw'] for k in working_keys]
    assignments: dict[str, list[str]] = {}

    for username, user_cfg in cfg['users'].items():
        token = user_cfg.get('token', username)
        count = user_cfg.get('keys_count', 3)
        selected = random.sample(raw_urls, min(count, len(raw_urls)))
        assignments[token] = selected

    return assignments


def save_subscriptions(assignments: dict[str, list[str]], cfg: dict) -> None:
    _ensure_state_dir(cfg)
    with open(_subscriptions_file(cfg), 'w', encoding='utf-8') as f:
        yaml.dump({'subscriptions': assignments}, f, allow_unicode=True)


def load_subscriptions(cfg: dict) -> dict[str, list[str]]:
    path = _subscriptions_file(cfg)
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data.get('subscriptions', {})


def refresh_subscriptions(working_keys: list[dict], cfg: dict) -> dict[str, list[str]]:
    """Сохраняет ключи, переназначает пользователям, сохраняет подписки."""
    save_working_keys(working_keys, cfg)
    assignments = assign_keys_to_users(working_keys, cfg)
    save_subscriptions(assignments, cfg)
    return assignments
