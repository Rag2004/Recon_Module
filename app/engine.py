"""Turn the three books into open recon alerts. Does not repair."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from app.books import infra_instrument, signed_qty
from app.diff import propose_leg


def resolve_broker_security_id(
    strategy: dict | None,
    infra: dict | None,
    broker_by_security: dict[str, int],
) -> str:
    """Prefer the desk Mongo id. Fall back to the strategy id, then the other side if Neo misses."""
    infra_id = str((infra or {}).get("security_id") or "").strip()
    strategy_id = str((strategy or {}).get("security_id") or "").strip()
    primary = infra_id or strategy_id
    if primary and primary in broker_by_security:
        return primary
    secondary = strategy_id if primary == infra_id else infra_id
    if secondary and secondary in broker_by_security:
        return secondary
    return primary or secondary


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

    broker_by_security = {}
    for row in broker_rows:
        sec = str(row.get("security_id") or row.get("securityId") or "")
        if not sec:
            continue
        broker_by_security[sec] = int(row.get("net_qty") if row.get("net_qty") is not None else row.get("netQty") or 0)

    keys = set(strategy_by_key) | set(infra_by_key)
    alerts = []
    for key in sorted(keys):
        strategy = strategy_by_key.get(key)
        infra = infra_by_key.get(key)
        security_id = resolve_broker_security_id(strategy, infra, broker_by_security)
        owners = owners_by_security.get(security_id, set())
        shared = bool(security_id) and len(owners) > 1
        broker_qty: int | None
        if not security_id or shared:
            broker_qty = None if shared else 0
            if not security_id:
                broker_qty = 0
        else:
            broker_qty = int(broker_by_security.get(security_id, 0))
        proposal = propose_leg(
            strategy_qty=int((strategy or {}).get("signed_qty") or 0),
            infra_qty=int((infra or {}).get("signed_qty") or 0),
            broker_qty=broker_qty,
            shared_strike=shared,
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
                "underlying_scrip": (infra or {}).get("underlying_scrip") or 0,
                "segment": (infra or {}).get("segment") or "",
                "updated_at": now,
                **proposal,
            }
        )
    return alerts


def index_alerts(alerts: list[dict]) -> dict[str, dict[str, Any]]:
    return {row["fingerprint"]: row for row in alerts}
