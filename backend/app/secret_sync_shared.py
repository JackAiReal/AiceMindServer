from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

_SYNC_SHARED_KEY_INFO = b'AiceMindSyncSharedKey'
_SYNC_STREAM_INFO = b'AiceMindSyncStream'
_SYNC_MAC_INFO = b'AiceMindSyncMac'


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _workspace_root() -> Path:
    return _backend_root().parents[1]


def shared_key_file() -> Path:
    custom = str(os.getenv('AICEMIND_SECRET_SYNC_KEY_FILE', '')).strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return _workspace_root() / '.shared' / 'aicemind-secret-sync.key'


def ensure_shared_key_text() -> str:
    env_value = str(os.getenv('AICEMIND_SECRET_SYNC_KEY', '')).strip()
    if env_value:
        return env_value

    key_file = shared_key_file()
    if key_file.exists():
        text = key_file.read_text(encoding='utf-8').strip()
        if text:
            return text

    seed = base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8').rstrip('=')
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(seed, encoding='utf-8')
    return seed


def shared_key_bytes() -> bytes:
    text = ensure_shared_key_text()
    compact = ''.join(text.split())
    try:
        padding = '=' * (-len(compact) % 4)
        raw = base64.urlsafe_b64decode((compact + padding).encode('utf-8'))
    except Exception:
        raw = compact.encode('utf-8')
    return hashlib.sha256(raw + _SYNC_SHARED_KEY_INFO).digest()


def stream_bytes(key: bytes, nonce: bytes, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        block = hmac.new(key, nonce + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:size])


def encrypt_payload(data: dict[str, Any]) -> str:
    plain = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    shared = shared_key_bytes()
    enc_key = hmac.new(shared, _SYNC_STREAM_INFO, hashlib.sha256).digest()
    mac_key = hmac.new(shared, _SYNC_MAC_INFO, hashlib.sha256).digest()
    nonce = os.urandom(16)
    cipher = bytes(a ^ b for a, b in zip(plain, stream_bytes(enc_key, nonce, len(plain))))
    digest = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + cipher + digest).decode('utf-8').rstrip('=')
