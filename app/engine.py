"""Turn the three books into open recon alerts. Does not repair."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from app.books import infra_instrument, signed_qty
from app.diff import propose_leg
from app.kotak_positions import normalize_product

_ALGO_PRODUCTS = {"", "MIS"}


def resolve_broker_security_id(
    strategy: dict | None,
    infra: dict | None,
    broker_security_ids: set[str],
) -> str:
    """Prefer the desk Mongo id. Fall back to the strategy id, then the other side if Neo misses."""
    infra_id = str((infra or {}).get("security_id") or "").strip()
    strategy_id = str((strategy or {}).get("security_id") or "").strip()
    primary = infra_id or strategy_id
    if primary and primary in broker_security_ids:
        return primary
    secondary = strategy_id if primary == infra_id else infra_id
    if secondary and secondary in broker_security_ids:
        return secondary
    return primary or secondary


def _broker_product(row: dict) -> str:
    return normalize_product(
        row.get("product_type") or row.get("productType") or row.get("prod") or row.get("product")
    )


def _bucket_key(security_id: str, product_type: str) -> str:
    return f"{security_id}|{product_type}"


def _join_same_security(strategy_by_key: dict, infra_by_key: dict) -> None:
    """One security is one leg, even when the strategy file stored the id as the name."""
    strategy_ids: dict[tuple, list] = defaultdict(list)
    infra_ids: dict[tuple, list] = defaultdict(list)
    for key, slot in strategy_by_key.items():
        sec = str(slot.get("security_id") or "")
        if sec:
            strategy_ids[(slot["strategy_id"], slot["broker_id"], sec)].append(key)
    for key, slot in infra_by_key.items():
        sec = str(slot.get("security_id") or "")
        if sec:
            infra_ids[(slot["strategy_id"], slot["broker_id"], sec)].append(key)
    for ident, strategy_keys in strategy_ids.items():
        infra_keys = infra_ids.get(ident) or []
        if len(strategy_keys) != 1 or len(infra_keys) != 1:
            continue
        strategy_key, infra_key = strategy_keys[0], infra_keys[0]
        if strategy_key == infra_key:
            continue
        strategy = strategy_by_key.pop(strategy_key)
        infra = infra_by_key.pop(infra_key)
        instrument = infra["instrument"] or strategy["instrument"]
        strategy["instrument"] = instrument
        infra["instrument"] = instrument
        merged = (ident[0], ident[1], instrument)
        strategy_by_key[merged] = strategy
        infra_by_key[merged] = infra


def build_alerts(
    strategy_rows: list[dict],
    infra_rows: list[dict],
    broker_rows: list[dict],
    *,
    now: str,
    alert_id_factory: Callable[[str], str] | None = None,
) -> list[dict]:
    strategy_by_key: dict[tuple, dict] = {}
    owners_by_security: dict[str, set[str]] = defaultdict(set)
    for row in strategy_rows:
        key = (row["strategy_id"], row["broker_id"], row["instrument"])
        slot = strategy_by_key.setdefault(
            key,
            {
                "strategy_id": row["strategy_id"],
                "broker_id": row["broker_id"],
                "instrument": row["instrument"],
                "security_id": str(row.get("security_id") or ""),
                "signed_qty": 0,
            },
        )
        slot["signed_qty"] += int(row["signed_qty"])
        if row.get("security_id"):
            slot["security_id"] = str(row["security_id"])
            owners_by_security[str(row["security_id"])].add(row["strategy_id"])

    infra_by_key: dict[tuple, dict] = {}
    for doc in infra_rows:
        instrument = infra_instrument(doc)
        if not instrument:
            continue
        strategy_id = str(doc.get("strategy_id") or "")
        broker_id = str(doc.get("broker_id") or "dhan_sandbox")
        key = (strategy_id, broker_id, instrument)
        slot = infra_by_key.setdefault(
            key,
            {
                "strategy_id": strategy_id,
                "broker_id": broker_id,
                "instrument": instrument,
                "security_id": str(doc.get("security_id") or ""),
                "signed_qty": 0,
                "underlying_scrip": doc.get("underlying_scrip") or 0,
                "segment": doc.get("segment") or "",
            },
        )
        slot["signed_qty"] += signed_qty(doc.get("side") or "", int(doc.get("quantity") or 0))
        if doc.get("security_id"):
            slot["security_id"] = str(doc.get("security_id"))

    broker_buckets: dict[str, dict] = {}
    for row in broker_rows:
        sec = str(row.get("security_id") or row.get("securityId") or "")
        if not sec:
            continue
        product = _broker_product(row)
        net = row.get("net_qty") if row.get("net_qty") is not None else row.get("netQty") or 0
        key = _bucket_key(sec, product)
        bucket = broker_buckets.get(key)
        if bucket:
            bucket["net_qty"] += int(net)
            continue
        broker_buckets[key] = {
            "security_id": sec,
            "product_type": product,
            "net_qty": int(net),
            "symbol": str(row.get("symbol") or row.get("instrument") or ""),
            "broker_id": str(row.get("broker_id") or ""),
        }

    broker_security_ids = {row["security_id"] for row in broker_buckets.values()}

    def algo_bucket(security_id: str) -> dict | None:
        """Algo book is MIS. A row with no product is the same book until Neo sends one."""
        mis = broker_buckets.get(_bucket_key(security_id, "MIS"))
        if mis:
            return mis
        return broker_buckets.get(_bucket_key(security_id, ""))

    _join_same_security(strategy_by_key, infra_by_key)
    claimed_securities = {
        str(slot.get("security_id") or "")
        for slot in (*strategy_by_key.values(), *infra_by_key.values())
        if slot.get("security_id")
    }
    keys = set(strategy_by_key) | set(infra_by_key)
    alerts = []
    for key in sorted(keys):
        strategy = strategy_by_key.get(key)
        infra = infra_by_key.get(key)
        security_id = resolve_broker_security_id(strategy, infra, broker_security_ids)
        owners = owners_by_security.get(security_id, set())
        shared = bool(security_id) and len(owners) > 1
        product = ""
        broker_qty: int | None
        if not security_id or shared:
            broker_qty = None if shared else 0
            if not security_id:
                broker_qty = 0
        else:
            held = algo_bucket(security_id)
            product = str((held or {}).get("product_type") or "")
            broker_qty = int((held or {}).get("net_qty") or 0)
        proposal = propose_leg(
            strategy_qty=int((strategy or {}).get("signed_qty") or 0),
            infra_qty=int((infra or {}).get("signed_qty") or 0),
            broker_qty=broker_qty,
            shared_strike=shared,
            product_type=product,
        )
        if proposal is None:
            continue
        base = strategy or infra or {}
        fingerprint = "|".join(
            [
                str(base.get("strategy_id")),
                str(base.get("broker_id")),
                str(base.get("instrument")),
                proposal["action"],
                str(proposal["strategy_qty"]),
                str(proposal["infra_qty"]),
                str(proposal["broker_qty"]),
                product,
            ]
        )
        alert_id = alert_id_factory(fingerprint) if alert_id_factory else fingerprint
        alerts.append(
            {
                "alert_id": alert_id,
                "fingerprint": fingerprint,
                "status": "open",
                "strategy_id": base.get("strategy_id"),
                "broker_id": base.get("broker_id"),
                "instrument": base.get("instrument"),
                "security_id": security_id,
                "product_type": product,
                "underlying_scrip": (infra or {}).get("underlying_scrip") or 0,
                "segment": (infra or {}).get("segment") or "",
                "updated_at": now,
                **proposal,
            }
        )
    for bucket_key in sorted(broker_buckets):
        row = broker_buckets[bucket_key]
        sec = row["security_id"]
        product = row["product_type"]
        if sec in claimed_securities and product in _ALGO_PRODUCTS:
            if not (product == "" and _bucket_key(sec, "MIS") in broker_buckets):
                continue
        broker_qty = int(row.get("net_qty") or 0)
        if broker_qty == 0:
            continue
        proposal = propose_leg(
            strategy_qty=0,
            infra_qty=0,
            broker_qty=broker_qty,
            shared_strike=False,
            product_type=product,
        )
        if proposal is None:
            continue
        instrument = row.get("symbol") or sec
        broker_id = row.get("broker_id") or ""
        fingerprint = "|".join(
            [
                "",
                broker_id,
                instrument,
                proposal["action"],
                "0",
                "0",
                str(proposal["broker_qty"]),
                product,
            ]
        )
        alert_id = alert_id_factory(fingerprint) if alert_id_factory else fingerprint
        alerts.append(
            {
                "alert_id": alert_id,
                "fingerprint": fingerprint,
                "status": "open",
                "strategy_id": "",
                "broker_id": broker_id,
                "instrument": instrument,
                "security_id": sec,
                "product_type": product,
                "underlying_scrip": 0,
                "segment": "",
                "updated_at": now,
                **proposal,
            }
        )
    return alerts


def index_alerts(alerts: list[dict]) -> dict[str, dict[str, Any]]:
    return {row["fingerprint"]: row for row in alerts}
