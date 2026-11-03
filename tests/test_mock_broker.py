"""Mock desk broker book: seed collection, else mock infra, never Dhan rows."""
from app.config import settings
from app.loop import fetch_mock_broker_positions, load_infra


class _Find:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None):
        query = query or {}
        out = []
        for doc in self.docs:
            if "broker_id" in query and doc.get("broker_id") != query["broker_id"]:
                continue
            if query.get("quantity", {}).get("$gt") == 0 and int(doc.get("quantity") or 0) <= 0:
                continue
            out.append(doc)
        return out


class _DB:
    def __init__(self, **collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]


def test_mock_fetch_uses_seed_when_present(monkeypatch):
    monkeypatch.setattr(settings, "RECON_BROKER_MODE", "mock")
    db = _DB(
        recon_mock_broker_positions=_Find(
            [{"security_id": "9", "quantity": 20, "side": "SELL"}]
        ),
        positions=_Find(
            [{"security_id": "9", "quantity": 99, "side": "BUY", "broker_id": "mock"}]
        ),
    )
    rows = fetch_mock_broker_positions(db)
    assert rows == [{"security_id": "9", "net_qty": -20}]


def test_mock_fetch_falls_back_to_mock_positions(monkeypatch):
    monkeypatch.setattr(settings, "RECON_BROKER_MODE", "mock")
    db = _DB(
        recon_mock_broker_positions=_Find([]),
        positions=_Find(
            [
                {"security_id": "9", "quantity": 20, "side": "SELL", "broker_id": "mock"},
                {"security_id": "9", "quantity": 5, "side": "SELL", "broker_id": "dhan_sandbox"},
            ]
        ),
    )
    rows = fetch_mock_broker_positions(db)
    assert rows == [{"security_id": "9", "net_qty": -20}]


def test_infra_filter_excludes_dhan_rows_in_mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "RECON_BROKER_MODE", "mock")
    db = _DB(
        positions=_Find(
            [
                {"security_id": "1", "quantity": 10, "broker_id": "mock", "status": "OPEN"},
                {"security_id": "2", "quantity": 10, "broker_id": "dhan_sandbox", "status": "OPEN"},
            ]
        )
    )
    rows = load_infra(db)
    assert [row["security_id"] for row in rows] == ["1"]
