"""Load the three books and build per-leg comparisons."""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def contract_key(symbol: str | None, expiry, strike, option_type: str | None) -> str | None:
    name = _symbol_token(symbol)
    right = str(option_type or "").strip().upper()
    if not name or not right or expiry in (None, "") or strike is None:
        return None
    try:
        token = _expiry_token(expiry)
        strike_token = _strike_token(strike)
    except (TypeError, ValueError):
        return None
    if not token or not strike_token:
        return None
    return f"{name}-{token}-{strike_token}-{right}"


def _symbol_token(symbol: str | None) -> str:
    raw = str(symbol or "").strip().upper()
    raw = raw.replace(" 50", "").replace("50", "") if raw.endswith(" 50") else raw
    if raw in {"NIFTY 50", "NIFTY50"}:
        return "NIFTY"
    return raw.split()[0] if raw else ""


def _expiry_token(expiry) -> str:
    if isinstance(expiry, datetime):
        day = expiry.date()
    elif isinstance(expiry, date):
        day = expiry
    else:
        day = date.fromisoformat(str(expiry).strip()[:10])
    return f"{day.day:02d}{_MONTHS[day.month - 1]}{day.year}"


def _strike_token(strike) -> str:
    value = float(strike)
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def signed_qty(side: str, quantity: int) -> int:
    qty = abs(int(quantity))
    name = str(side or "").upper()
    if name in {"BUY", "LONG"}:
        return qty
    if name in {"SELL", "SHORT"}:
        return -qty
    return qty


def read_strategy_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            try:
                qty = int(float(raw.get("quantity") or 0))
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            instrument = str(raw.get("instrument") or "")
            if not instrument:
                continue
            rows.append(
                {
                    "strategy_id": str(raw.get("strategy_id") or ""),
                    "broker_id": str(raw.get("broker_id") or "dhan_sandbox"),
                    "instrument": instrument,
                    "security_id": str(raw.get("security_id") or ""),
                    "signed_qty": signed_qty(raw.get("position_side") or "", qty),
                    "entry_price": raw.get("entry_price") or "",
                }
            )
    return rows


def infra_instrument(doc: dict) -> str | None:
    return contract_key(
        doc.get("underlying") or doc.get("symbol"),
        doc.get("expiry"),
        doc.get("strike"),
        doc.get("option_type"),
    )


_INSTRUMENT = re.compile(r"^([A-Z0-9]+)-(\d{2}[A-Za-z]{3}\d{4})-(\d+(?:\.\d+)?)-(CE|PE)$")


def parse_instrument(instrument: str) -> dict | None:
    match = _INSTRUMENT.match(str(instrument or ""))
    if not match:
        return None
    symbol, expiry_token, strike, right = match.groups()
    day = int(expiry_token[:2])
    mon = expiry_token[2:5].title()
    year = int(expiry_token[5:])
    month = _MONTHS.index(mon) + 1
    return {
        "underlying": symbol,
        "expiry": date(year, month, day).isoformat(),
        "strike": strike,
        "option_type": right,
    }
