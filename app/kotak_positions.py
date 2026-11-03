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
    """Signed net quantity. FnO quantities from Neo are in shares and are divided by lot size."""

    def num(key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    buy = num("cfBuyQty") + num("flBuyQty")
    sell = num("cfSellQty") + num("flSellQty")
    net = buy - sell
    lot = num("lotSz") or 1.0
    segment = str(row.get("exSeg") or "").lower()
    if lot > 1 and "fo" in segment:
        net = net / lot
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


def _login():
    from neo_api_client import NeoAPI

    doc = _load_credentials()
    token = str(doc.get("access_token") or "").strip()
    mobile = normalize_mobile(doc.get("mobile"))
    ucc = str(doc.get("client_id") or "").strip()
    mpin = str(doc.get("mpin") or "").strip()
    secret = str(doc.get("totp_secret") or "").strip()
    if not (token and mobile and ucc and mpin and secret):
        raise RuntimeError("Kotak credentials incomplete")
    client = NeoAPI(consumer_key=token, environment="prod")
    login = client.totp_login(mobile_number=mobile, ucc=ucc, totp=totp_code(secret))
    if _rejected(login):
        raise RuntimeError("Kotak totp_login rejected")
    validate = client.totp_validate(mpin=mpin)
    if _rejected(validate):
        raise RuntimeError("Kotak totp_validate rejected")
    return client


def _ensure_client():
    global _client
    if _client is None:
        _client = _login()
    return _client


def fetch_kotak_positions() -> list[dict]:
    """Live Neo positions. One re-login if the cached session is rejected."""
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            payload = _ensure_client().positions()
            return rows_from_positions(payload)
        except PermissionError as exc:
            last_error = exc
            reset_session()
            logger.info("Kotak recon session refresh attempt=%s", attempt)
        except Exception as exc:
            last_error = exc
            reset_session()
            if attempt == 2:
                break
            logger.info("Kotak recon login retry: %s", exc)
    raise RuntimeError(f"Kotak positions failed: {last_error}")
