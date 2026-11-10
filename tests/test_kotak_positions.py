import pytest

from app.kotak_positions import neo_net_qty, neo_security_id, rows_from_positions
from app.loop import desk_broker_id
from app.config import settings


def test_fno_net_qty_stays_in_shares():
    row = {
        "exSeg": "nse_fo",
        "lotSz": "20",
        "cfBuyQty": "0",
        "flBuyQty": "40",
        "cfSellQty": "0",
        "flSellQty": "0",
        "tok": "99",
    }
    assert neo_net_qty(row) == 40
    assert neo_security_id(row) == "99"


def test_rows_drop_flat_positions():
    payload = {
        "stat": "ok",
        "data": [
            {"tok": "1", "exSeg": "nse_cm", "flBuyQty": "5", "flSellQty": "0", "trdSym": "AAA"},
            {"tok": "2", "exSeg": "nse_cm", "flBuyQty": "5", "flSellQty": "5", "trdSym": "BBB"},
        ],
    }
    rows = rows_from_positions(payload)
    assert rows == [{"security_id": "1", "net_qty": 5, "symbol": "AAA"}]


def test_error_payload_is_rejected():
    with pytest.raises(PermissionError):
        rows_from_positions({"status_code": 424, "error": [{"code": "10508"}]})


def test_kotak_mode_uses_kotak_book(monkeypatch):
    monkeypatch.setattr(settings, "RECON_BROKER_MODE", "kotak")
    assert desk_broker_id() == "kotak_sandbox"
