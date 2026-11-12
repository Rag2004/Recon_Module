"""1–2s compare loop. Writes alerts. Never places orders or edits positions."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pymongo import MongoClient

from app.books import read_strategy_csv
from app.config import settings
from app.engine import build_alerts

logger = logging.getLogger(__name__)

COLLECTION = "recon_alerts"


def open_csv_path() -> Path:
    raw = str(settings.POSITION_STATE_DIR or "").strip()
    base = Path(raw) if raw else Path(__file__).resolve().parents[2] / "state"
    return base / "open_positions.csv"


def dhan_redis_key() -> str:
    base = str(settings.DHAN_API_BASE or "").lower()
    env = "sandbox" if "sandbox" in base else "live"
    return f"broker:dhan:{env}"


def dhan_credentials() -> tuple[str, str]:
    """Redis broker:dhan:* first, then .env. Never reads the Kotak key."""
    try:
        import redis as redis_lib

        raw = redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        ).get(dhan_redis_key())
        if raw:
            body = json.loads(raw)
            if isinstance(body, dict):
                token = str(body.get("access_token") or "").strip()
                if token:
                    client_id = str(body.get("client_id") or "").strip() or str(settings.DHAN_CLIENT_ID or "").strip()
                    return token, client_id
    except Exception as exc:
        logger.debug("dhan redis read skipped: %s", exc)
    return str(settings.DHAN_ACCESS_TOKEN or "").strip(), str(settings.DHAN_CLIENT_ID or "").strip()


def fetch_dhan_positions() -> list[dict]:
    token, client_id = dhan_credentials()
    if not token or not client_id:
        raise RuntimeError("Dhan credentials missing")
    url = f"{settings.DHAN_API_BASE.rstrip('/')}/v2/positions"
    headers = {"access-token": token, "client-id": str(client_id), "Content-Type": "application/json"}
    with httpx.Client(timeout=8.0) as client:
        resp = client.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Dhan positions {resp.status_code}")
    payload = resp.json()
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "positions"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
    raise RuntimeError("Dhan positions payload was not a list")


def broker_mode() -> str:
    return str(settings.RECON_BROKER_MODE or "dhan").strip().lower()


def desk_broker_id() -> str:
    mode = broker_mode()
    if mode == "mock":
        return "mock"
    if mode == "kotak":
        return "kotak_sandbox"
    return "dhan_sandbox"


def _signed_row(doc: dict) -> int:
    if doc.get("net_qty") is not None:
        return int(doc.get("net_qty") or 0)
    if doc.get("netQty") is not None:
        return int(doc.get("netQty") or 0)
    qty = int(doc.get("quantity") or 0)
    side = str(doc.get("side") or doc.get("position_side") or "").upper()
    if side in {"SELL", "SHORT"}:
        return -abs(qty)
    return qty


def fetch_mock_broker_positions(db) -> list[dict]:
    """Paper broker book. Seed collection wins; otherwise aggregate mock infra."""
    seeded = [
        doc
        for doc in db["recon_mock_broker_positions"].find({})
        if int(doc.get("quantity") or doc.get("net_qty") or doc.get("netQty") or 0) != 0
    ]
    if seeded:
        return [
            {"security_id": str(doc.get("security_id") or ""), "net_qty": _signed_row(doc)}
            for doc in seeded
            if doc.get("security_id") not in (None, "")
        ]
    totals: dict[str, int] = {}
    for doc in db["positions"].find({"broker_id": "mock", "quantity": {"$gt": 0}}):
        sec = str(doc.get("security_id") or "")
        if not sec:
            continue
        totals[sec] = totals.get(sec, 0) + _signed_row(doc)
    return [{"security_id": sec, "net_qty": qty} for sec, qty in totals.items() if qty]


def fetch_broker_positions(db) -> list[dict]:
    if broker_mode() == "mock":
        return fetch_mock_broker_positions(db)
    if broker_mode() == "kotak":
        from app.kotak_positions import fetch_kotak_positions_cached

        return fetch_kotak_positions_cached()
    return fetch_dhan_positions()


def load_infra(db) -> list[dict]:
    query = {
        "$or": [{"status": "OPEN"}, {"status": {"$exists": False}}],
        "quantity": {"$gt": 0},
        "broker_id": desk_broker_id(),
    }
    return list(db["positions"].find(query))


def sync_alerts(db, alerts: list[dict]) -> None:
    coll = db[COLLECTION]
    live = {row["fingerprint"] for row in alerts}
    dismissed: set[str] = set()
    if live:
        for doc in coll.find(
            {"status": "dismissed", "fingerprint": {"$in": list(live)}},
            {"fingerprint": 1},
        ):
            fingerprint = str(doc.get("fingerprint") or "")
            if fingerprint:
                dismissed.add(fingerprint)
    for row in alerts:
        if row["fingerprint"] in dismissed:
            continue
        coll.update_one(
            {"fingerprint": row["fingerprint"]},
            {"$set": row, "$setOnInsert": {"created_at": row["updated_at"]}},
            upsert=True,
        )
    coll.update_many(
        {"status": "open", "fingerprint": {"$nin": list(live)}},
        {"$set": {"status": "closed", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )


def run_tick(db, *, fetch_positions=None, csv_path: Path | None = None) -> dict:
    """One compare. A failed read returns an error and does not rewrite alerts."""
    path = csv_path or open_csv_path()
    fetch = fetch_positions or (lambda: fetch_broker_positions(db))
    try:
        strategy_rows = read_strategy_csv(path)
        infra_rows = load_infra(db)
        broker_rows = fetch()
    except Exception as exc:
        logger.warning("recon tick skipped: %s", exc)
        return {"ok": False, "error": str(exc)}
    alerts = build_alerts(
        strategy_rows,
        infra_rows,
        broker_rows,
        now=datetime.now(timezone.utc).isoformat(),
    )
    sync_alerts(db, alerts)
    return {"ok": True, "alerts": len(alerts)}


def recon_loop_interval() -> float:
    """Seconds between ticks. Allow up to 60s so Neo is not hammered."""
    return max(1.0, min(float(settings.RECON_INTERVAL_SEC or 10), 60.0))


def loop_forever(stop_event=None) -> None:
    client = MongoClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB_NAME]
    interval = recon_loop_interval()
    logger.info("recon loop every %.1fs mode=%s csv=%s", interval, broker_mode(), open_csv_path())
    while stop_event is None or not stop_event.is_set():
        started = time.monotonic()
        try:
            result = run_tick(db)
            if not result.get("ok"):
                logger.warning("recon tick error %s", result.get("error"))
        except Exception:
            logger.exception("recon tick crashed; loop continues")
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, interval - elapsed))
