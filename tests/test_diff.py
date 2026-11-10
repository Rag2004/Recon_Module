"""Decision cases from the three-state recon plan. Alerts only."""
from app.diff import propose_leg
from app.engine import build_alerts


def _leg(strategy, infra, broker, shared=False):
    return propose_leg(
        strategy_qty=strategy,
        infra_qty=infra,
        broker_qty=broker,
        shared_strike=shared,
    )


def test_algo3_infra4_broker3_removes_infra():
    row = _leg(-3, -4, -3)
    assert row["action"] == "remove_infra"
    assert row["actionable"] is True
    assert row["text"] == "Remove the extra infra strike"
    assert row["order"] is None


def test_algo4_infra4_broker3_places_broker_without_infra_add():
    row = _leg(-4, -4, -3)
    assert row["action"] == "place_broker"
    assert row["text"] == "Place the missing strike at the broker"
    assert row["order"]["side"] == "SELL"
    assert row["order"]["quantity"] == 1
    assert row["order"]["skip_infra_on_fill"] is True
    assert row["order"]["order_tag"] == "StateReconcileSkipBook"


def test_restore_infra_when_broker_already_matches():
    row = _leg(-4, -3, -4)
    assert row["action"] == "restore_infra"
    assert row["order"] is None


def test_reduce_extra_broker_when_infra_matches():
    row = _leg(-3, -3, -4)
    assert row["action"] == "reduce_broker"
    assert row["order"]["skip_infra_on_fill"] is True
    assert row["order"]["side"] == "BUY"


def test_both_short_sends_normal_entry():
    row = _leg(-4, -3, -3)
    assert row["action"] == "normal_entry"
    assert row["order"]["skip_infra_on_fill"] is False
    assert row["order"]["order_tag"] == "StateReconcile"


def test_both_extra_sends_normal_exit():
    row = _leg(-3, -4, -4)
    assert row["action"] == "normal_exit"
    assert row["order"]["side"] == "BUY"
    assert row["order"]["skip_infra_on_fill"] is False


def test_shared_strike_is_not_actionable():
    row = _leg(-3, -4, None, shared=True)
    assert row["action"] == "review"
    assert row["actionable"] is False
    assert "Shared strike" in row["text"]


def test_match_raises_no_alert():
    assert _leg(-3, -3, -3) is None


def test_blank_strategy_security_id_uses_infra_to_match_broker():
    strategy = [
        {
            "strategy_id": "SN_275_T13",
            "broker_id": "kotak_sandbox",
            "instrument": "SENSEX-05Nov2026-79300-CE",
            "security_id": "",
            "signed_qty": 20,
        }
    ]
    infra = [
        {
            "strategy_id": "SN_275_T13",
            "broker_id": "kotak_sandbox",
            "underlying": "SENSEX",
            "expiry": "2026-11-05",
            "strike": 79300,
            "option_type": "CE",
            "side": "BUY",
            "quantity": 20,
            "security_id": "1134391",
        }
    ]
    alerts = build_alerts(
        strategy,
        infra,
        [{"security_id": "1134391", "net_qty": 20}],
        now="t",
    )
    assert alerts == []


def test_wrong_strategy_security_id_falls_back_to_infra():
    strategy = [
        {
            "strategy_id": "SN_275_T13",
            "broker_id": "kotak_sandbox",
            "instrument": "SENSEX-05Nov2026-76900-CE",
            "security_id": "999999",
            "signed_qty": -20,
        }
    ]
    infra = [
        {
            "strategy_id": "SN_275_T13",
            "broker_id": "kotak_sandbox",
            "underlying": "SENSEX",
            "expiry": "2026-11-05",
            "strike": 76900,
            "option_type": "CE",
            "side": "SELL",
            "quantity": 20,
            "security_id": "1134001",
        }
    ]
    alerts = build_alerts(
        strategy,
        infra,
        [{"security_id": "1134001", "net_qty": -20}],
        now="t",
    )
    assert alerts == []


def test_build_alerts_shared_security_has_no_button():
    strategy = [
        {"strategy_id": "SN_S01", "broker_id": "dhan_sandbox", "instrument": "SENSEX-26Oct2026-80000-CE", "security_id": "9", "signed_qty": -20},
        {"strategy_id": "SN_S02", "broker_id": "dhan_sandbox", "instrument": "SENSEX-26Oct2026-80000-PE", "security_id": "9", "signed_qty": -20},
    ]
    # Same security id on two strategies — broker net cannot be attributed.
    strategy[1]["instrument"] = "SENSEX-26Oct2026-80000-CE"
    infra = [
        {"strategy_id": "SN_S01", "broker_id": "dhan_sandbox", "underlying": "SENSEX", "expiry": "2026-10-26", "strike": 80000, "option_type": "CE", "side": "SELL", "quantity": 40, "security_id": 9},
    ]
    alerts = build_alerts(strategy, infra, [{"security_id": "9", "net_qty": -40}], now="t")
    shared = [row for row in alerts if row["strategy_id"] == "SN_S01"]
    assert shared
    assert shared[0]["actionable"] is False


def test_partial_read_does_not_sync(monkeypatch, tmp_path):
    from app import loop as loop_mod

    class Coll:
        def update_one(self, *args, **kwargs):
            raise AssertionError("alerts must not be written")

        def update_many(self, *args, **kwargs):
            raise AssertionError("alerts must not be written")

    class DB(dict):
        def __getitem__(self, name):
            return Coll()

    result = loop_mod.run_tick(DB(), fetch_positions=lambda: (_ for _ in ()).throw(RuntimeError("down")), csv_path=tmp_path / "missing.csv")
    assert result["ok"] is False
