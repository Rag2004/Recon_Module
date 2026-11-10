"""Kotak Neo live position book. Session is cached and refreshed on 403."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import struct
import time

from app.config import settings

logger = logging.getLogger(__name__)

_KOTAK_KEY = "broker:kotak:prod"
_client = None


def totp_code(secret: str, at: float | None = None, step: int = 30) -> str:
    padded = secret.strip().upper().replace(" ", "")
    padded += "=" * ((8 - len(padded) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def normalize_mobile(value) -> str:
    text = str(value or "").strip().replace(" ", "").replace("-", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return text


def reset_session() -> None:
    global _client
    _client = None


def neo_net_qty(row: dict) -> int:
    """Signed net quantity in shares, the same unit Kotak fills and the desk stores."""

    def num(key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    buy = num("cfBuyQty") + num("flBuyQty")
    sell = num("cfSellQty") + num("flSellQty")
    net = buy - sell
    if net == 0 and row.get("qty") not in (None, ""):
        net = num("qty")
    return int(round(net))


def neo_security_id(row: dict) -> str:
    token = str(row.get("tok") or row.get("token") or "").strip()
    if token:
        return token
    return str(row.get("trdSym") or row.get("sym") or "").strip()


def _rejected(response) -> bool:
    if not isinstance(response, dict):
        return False
    status = response.get("status_code") or response.get("stCode")
    if status in (400, "400", 403, "403", 424, "424"):
        return True
    return bool(response.get("error"))


def rows_from_positions(payload) -> list[dict]:
    data = payload
    if isinstance(payload, dict):
        if _rejected(payload):
            raise PermissionError("kotak session rejected")
        nested = payload.get("data")
        data = nested if isinstance(nested, list) else []
    if not isinstance(data, list):
        raise RuntimeError("Kotak positions payload was not a list")
    out = []
    for row in data:
        if not isinstance(row, dict):
            continue
        security_id = neo_security_id(row)
        net = neo_net_qty(row)
        if not security_id or net == 0:
            continue
        out.append({"security_id": security_id, "net_qty": net, "symbol": str(row.get("trdSym") or "")})
    return out


def _load_credentials() -> dict:
    try:
        import redis as redis_lib

        raw = redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        ).get(_KOTAK_KEY)
    except Exception as exc:
        raise RuntimeError(f"Kotak redis read failed: {exc}") from exc
    if not raw:
        raise RuntimeError("Kotak credentials missing")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise RuntimeError("Kotak credentials missing")
    return body


def _clear_trade_session(doc: dict) -> None:
    doc.pop("trade_token", None)
    doc.pop("trade_sid", None)
    doc.pop("trade_base_url", None)
    doc["session_at"] = ""
    try:
        import redis as redis_lib

        redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        ).set(_KOTAK_KEY, json.dumps(doc))
    except Exception as exc:
        logger.warning("Kotak session clear failed: %s", exc)


def _client_from_session(doc: dict):
    from neo_api_client import NeoAPI

    token = str(doc.get("access_token") or "").strip()
    trade = str(doc.get("trade_token") or "").strip()
    sid = str(doc.get("trade_sid") or "").strip()
    if not (token and trade and sid):
        raise RuntimeError("Kotak session is not generated. Enter the authenticator code on the Kotak page.")
    client = NeoAPI(consumer_key=token, environment="prod")
    cfg = getattr(client, "configuration", None)
    if cfg is None:
        raise RuntimeError("Neo client has no configuration")
    cfg.edit_token = trade
    cfg.edit_sid = sid
    base = str(doc.get("trade_base_url") or "").strip()
    if base:
        cfg.base_url = base
    return client


def _ensure_client():
    global _client
    if _client is None:
        _client = _client_from_session(_load_credentials())
    return _client


def _session_rejected(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    if _rejected(payload):
        return True
    text = str(payload.get("Error Message") or payload.get("Error") or "").lower()
    return "2fa" in text or "unauthorized" in text or "session" in text


_kotak_positions_cache: dict = {"at": 0.0, "rows": None}
_KOTAK_POSITIONS_TTL_SEC = 10.0


def fetch_kotak_positions_cached() -> list[dict]:
    """Reuse a successful Neo book for a few seconds so a 10s recon tick does not spam positions()."""
    now = time.monotonic()
    cached = _kotak_positions_cache.get("rows")
    cached_at = float(_kotak_positions_cache.get("at") or 0)
    if cached is not None and now - cached_at < _KOTAK_POSITIONS_TTL_SEC:
        return cached
    try:
        rows = fetch_kotak_positions()
    except Exception:
        # One timeout must not blank the broker book while the last good snapshot is still fresh.
        if cached is not None and now - cached_at < _KOTAK_POSITIONS_TTL_SEC * 2:
            logger.warning("Kotak positions failed; reusing last good book for one more window")
            _kotak_positions_cache["at"] = now
            return cached
        raise
    _kotak_positions_cache["at"] = now
    _kotak_positions_cache["rows"] = rows
    return rows


def fetch_kotak_positions() -> list[dict]:
    """Live Neo positions from the session saved on the Kotak page."""
    doc = _load_credentials()
    if not (str(doc.get("trade_token") or "").strip() and str(doc.get("trade_sid") or "").strip()):
        raise RuntimeError("Kotak session is not generated. Enter the authenticator code on the Kotak page.")
    try:
        payload = _ensure_client().positions()
    except Exception as exc:
        reset_session()
        raise RuntimeError(f"Kotak positions failed: {exc}") from exc
    if _session_rejected(payload):
        reset_session()
        _clear_trade_session(doc)
        raise RuntimeError("Kotak session was rejected. Generate a new one on the Kotak page.")
    return rows_from_positions(payload)
