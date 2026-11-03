"""Compare strategy, infra, and broker signed quantities. Alerts only — no repairs."""
from __future__ import annotations

from typing import Any

TAG_REPAIR = "StateReconcile"
TAG_SKIP_BOOK = "StateReconcileSkipBook"


def propose_leg(
    *,
    strategy_qty: int,
    infra_qty: int,
    broker_qty: int | None,
    shared_strike: bool,
) -> dict[str, Any] | None:
    """Return an alert payload, or None when the three books match.

    ``broker_qty`` is None when this contract is shared by more than one strategy
    and the broker residual cannot be attributed. Those alerts are not actionable.
    Quantities are signed: long/BUY positive, short/SELL negative.
    """
    if shared_strike or broker_qty is None:
        if strategy_qty == infra_qty:
            return None
        return _alert(
            action="review",
            actionable=False,
            strategy_qty=strategy_qty,
            infra_qty=infra_qty,
            broker_qty=0 if broker_qty is None else broker_qty,
            text="Shared strike — gap shown, no reconcile",
        )

    if strategy_qty == infra_qty == broker_qty:
        return None

    if infra_qty != strategy_qty and broker_qty == strategy_qty:
        action = "remove_infra" if _infra_has_extra(strategy_qty, infra_qty) else "restore_infra"
        text = (
            "Remove the extra infra strike"
            if action == "remove_infra"
            else "Restore the missing infra row"
        )
        return _alert(
            action=action,
            actionable=True,
            strategy_qty=strategy_qty,
            infra_qty=infra_qty,
            broker_qty=broker_qty,
            text=text,
        )

    if infra_qty == strategy_qty and broker_qty != strategy_qty:
        action, text = _broker_action(strategy_qty, broker_qty)
        order = _order_toward(strategy_qty, broker_qty, skip_book=True)
        return _alert(
            action=action,
            actionable=action != "review",
            strategy_qty=strategy_qty,
            infra_qty=infra_qty,
            broker_qty=broker_qty,
            text=text,
            order=order,
        )

    if infra_qty == broker_qty and infra_qty != strategy_qty:
        action, text = _both_gap_action(strategy_qty, infra_qty)
        order = _order_toward(strategy_qty, infra_qty, skip_book=False)
        return _alert(
            action=action,
            actionable=action != "review",
            strategy_qty=strategy_qty,
            infra_qty=infra_qty,
            broker_qty=broker_qty,
            text=text,
            order=order,
        )

    return _alert(
        action="review",
        actionable=False,
        strategy_qty=strategy_qty,
        infra_qty=infra_qty,
        broker_qty=broker_qty,
        text="Infra and broker differ from strategy in different ways",
    )


def _infra_has_extra(strategy_qty: int, infra_qty: int) -> bool:
    if strategy_qty == 0:
        return infra_qty != 0
    if infra_qty == 0:
        return False
    if (strategy_qty > 0) != (infra_qty > 0):
        return True
    return abs(infra_qty) > abs(strategy_qty)


def _broker_action(strategy_qty: int, broker_qty: int) -> tuple[str, str]:
    if strategy_qty == 0 and broker_qty != 0:
        return "reduce_broker", "Reduce the extra broker strike"
    if broker_qty == 0 or abs(broker_qty) < abs(strategy_qty):
        if strategy_qty != 0 and broker_qty != 0 and (strategy_qty > 0) != (broker_qty > 0):
            return "review", "Broker side does not match strategy"
        return "place_broker", "Place the missing strike at the broker"
    if abs(broker_qty) > abs(strategy_qty):
        return "reduce_broker", "Reduce the extra broker strike"
    return "review", "Broker book does not match strategy"


def _both_gap_action(strategy_qty: int, held_qty: int) -> tuple[str, str]:
    if strategy_qty == 0 or abs(held_qty) > abs(strategy_qty):
        return "normal_exit", "Send the missing exit"
    if held_qty == 0 or abs(held_qty) < abs(strategy_qty):
        if held_qty != 0 and (strategy_qty > 0) != (held_qty > 0):
            return "review", "Held side does not match strategy"
        return "normal_entry", "Send the missing entry"
    return "review", "Both books differ from strategy"


def _order_toward(target: int, current: int, *, skip_book: bool) -> dict[str, Any]:
    delta = target - current
    side = "BUY" if delta > 0 else "SELL"
    increasing = abs(target) > abs(current) and (current == 0 or (target > 0) == (current > 0))
    return {
        "side": side,
        "quantity": abs(delta),
        "order_class": "ENTRY" if increasing or target != 0 and current == 0 else "EXIT",
        "order_tag": TAG_SKIP_BOOK if skip_book else TAG_REPAIR,
        "skip_infra_on_fill": skip_book,
    }


def _alert(
    *,
    action: str,
    actionable: bool,
    strategy_qty: int,
    infra_qty: int,
    broker_qty: int,
    text: str,
    order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "actionable": actionable,
        "strategy_qty": int(strategy_qty),
        "infra_qty": int(infra_qty),
        "broker_qty": int(broker_qty),
        "text": text,
        "order": order,
    }
