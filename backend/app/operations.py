"""Committed production, shadow plans, actuals checks, and the measurement read model."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from app.db import connect, fetch_all, fetch_one, get_meta, set_meta
from app.economics import HORIZON_END, HORIZON_START, penalty_type_of, round_m3, round_rm

DELIVERY_SLACK = 0.05


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def plant_mode(plant_id: int) -> str:
    with connect() as conn:
        raw = get_meta(conn, f"mode:{int(plant_id)}")
    return raw if raw in {"shadow", "pilot"} else "shadow"


def set_plant_mode(plant_id: int, mode: str) -> str:
    if mode not in {"shadow", "pilot"}:
        raise ValueError("Mode is shadow or pilot.")
    with connect() as conn:
        set_meta(conn, f"mode:{int(plant_id)}", mode)
    return mode


def buffer_days() -> int:
    with connect() as conn:
        raw = get_meta(conn, "buffer_days")
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


def set_buffer_days(days: int) -> int:
    days = max(0, min(int(days), 14))
    with connect() as conn:
        set_meta(conn, "buffer_days", str(days))
    return days


def _open_row(plant_id: int, product_id: int) -> dict | None:
    with connect() as conn:
        return fetch_one(
            conn,
            """
            SELECT * FROM decisions
            WHERE plant_id = ? AND product_id = ? AND status != 'replaced'
            ORDER BY id DESC
            LIMIT 1
            """,
            (plant_id, product_id),
        )


def open_decision_view(plant_id: int, product_id: int) -> dict | None:
    row = _open_row(plant_id, product_id)
    if row is None:
        return None
    final = json.loads(row["final_json"])
    recommended = json.loads(row["recommended_json"])
    recommended_qty = {int(line["demand_id"]): float(line["allocated_quantity"]) for line in recommended.get("allocations", [])}
    lines = []
    for line in final.get("allocations", []):
        lines.append(
            {
                "demand_id": int(line["demand_id"]),
                "customer_or_project": line.get("customer_or_project"),
                "committed_m3": float(line["allocated_quantity"]),
                "recommended_m3": recommended_qty.get(int(line["demand_id"]), 0.0),
            }
        )
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"],
        "status": row["status"],
        "deadline": row.get("deadline"),
        "required_signatories": json.loads(row.get("required_signatories_json") or "[]"),
        "locked": bool(row.get("actual_json")),
        "lines": lines,
    }


def apply_open_commitments(world: dict) -> None:
    """Shrink the book by quantities already committed, and by the inventory those decisions drew."""
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM decisions WHERE status != 'replaced' ORDER BY id")
    latest: dict[tuple[int, int], dict] = {}
    for row in rows:
        latest[(int(row["plant_id"]), int(row["product_id"]))] = row
    inventory_draw: dict[tuple[int, int], float] = {}
    committed_by_demand: dict[int, float] = {}
    for row in latest.values():
        if not row.get("committed_json"):
            continue
        final = json.loads(row["final_json"])
        for line in final.get("allocations", []):
            committed_by_demand[int(line["demand_id"])] = float(line["allocated_quantity"])
        schedule = json.loads(row["committed_json"]) if row.get("committed_json") else {}
        key = (int(row["plant_id"]), int(row["product_id"]))
        inventory_draw[key] = inventory_draw.get(key, 0.0) + float(schedule.get("inventory_m3") or 0.0)
    for row in world["inventory"]:
        drawn = inventory_draw.get((int(row["plant_id"]), int(row["product_id"])), 0.0)
        if drawn <= 0:
            continue
        row["on_hand"] = max(0.0, float(row["on_hand"]) - drawn)
        row["usable"] = max(0.0, float(row["on_hand"]) - float(row["safety_stock"]))
    for demand in world["demands"]:
        committed = committed_by_demand.get(int(demand["id"]), 0.0)
        demand["committed_quantity"] = round_m3(committed)
        requested = float(demand["requested_quantity"])
        if committed <= 0 or requested <= 0:
            continue
        residual = max(0.0, requested - committed)
        fraction = residual / requested
        demand["requested_quantity"] = round_m3(residual)
        demand["confirmed_quantity"] = round_m3(max(0.0, float(demand["confirmed_quantity"]) - committed))
        demand["contribution_margin"] = float(demand["contribution_margin"]) * fraction
        demand["contractual_penalty"] = float(demand["contractual_penalty"]) * fraction
        demand["delay_days_if_unserved"] = float(demand["delay_days_if_unserved"]) * fraction


def schedule_commitment(world: dict, plant_id: int, product_id: int, targets: dict[int, float]) -> dict:
    """Place approved quantities on the latest feasible day, buffering lump-sum orders."""
    from app.engine import _draw, _schedule_deadline

    days = sorted(row["prod_date"] for row in world["calendar"] if row["plant_id"] == plant_id and row["product_id"] == product_id)
    cap = {row["prod_date"]: float(row["available_capacity"]) for row in world["calendar"] if row["plant_id"] == plant_id and row["product_id"] == product_id}
    inventory = next(row for row in world["inventory"] if row["plant_id"] == plant_id and row["product_id"] == product_id)
    usable = float(inventory["usable"])
    demands = [row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    buffer = buffer_days()
    ordered = sorted(demands, key=lambda row: (_schedule_deadline(str(row["required_date"]), penalty_type_of(row), buffer), int(row["id"])))
    days_out = []
    inventory_m3 = 0.0
    for demand in ordered:
        qty = float(targets.get(int(demand["id"]), 0.0))
        if qty <= 0:
            continue
        deadline = _schedule_deadline(str(demand["required_date"]), penalty_type_of(demand), buffer)
        allocated, usable, from_prod, by_day = _draw(days, cap, usable, deadline, qty)
        inventory_m3 += max(0.0, allocated - from_prod)
        for item in by_day:
            days_out.append({"demand_id": int(demand["id"]), "date": item["date"], "quantity": item["quantity"]})
    totals: dict[str, float] = {}
    for item in days_out:
        totals[item["date"]] = totals.get(item["date"], 0.0) + float(item["quantity"])
    return {"days": days_out, "inventory_m3": round_m3(inventory_m3), "by_date": {day: round_m3(qty) for day, qty in totals.items()}}


def write_commitment(plant_id: int, product_id: int, schedule: dict) -> None:
    with connect() as conn:
        for day, qty in schedule.get("by_date", {}).items():
            conn.execute(
                """
                UPDATE capacity_calendar
                SET committed_production = committed_production + ?
                WHERE plant_id = ? AND product_id = ? AND prod_date = ?
                """,
                (float(qty), plant_id, product_id, day),
            )


def release_commitment(decision: dict) -> None:
    schedule = json.loads(decision["committed_json"]) if decision.get("committed_json") else {}
    with connect() as conn:
        for day, qty in (schedule.get("by_date") or {}).items():
            conn.execute(
                """
                UPDATE capacity_calendar
                SET committed_production = MAX(0, committed_production - ?)
                WHERE plant_id = ? AND product_id = ? AND prod_date = ?
                """,
                (float(qty), decision["plant_id"], decision["product_id"], day),
            )
        conn.execute("UPDATE decisions SET status = 'replaced' WHERE id = ?", (decision["id"],))


def assert_replaceable(plant_id: int, product_id: int, replace: bool) -> dict | None:
    current = _open_row(plant_id, product_id)
    if current is None:
        return None
    if current.get("actual_json"):
        raise ValueError("This decision has actuals and can no longer be replaced.")
    if not replace:
        raise ValueError(
            f"Open decision: you are replacing {current['username']} at {current['created_at']}. Confirm replace to continue."
        )
    return current


def log_replacement(old_id: int, new_id: int, username: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO decision_replacements(replaced_decision_id, replacement_decision_id, created_at, username, note)
            VALUES(?, ?, ?, ?, ?)
            """,
            (old_id, new_id, now_iso(), username, "Replaced before actuals were recorded."),
        )
        conn.execute("UPDATE decisions SET replaced_by = ? WHERE id = ?", (new_id, old_id))


def validate_actuals(final_allocations: list[dict], lines: list[dict]) -> list[dict]:
    by_id = {int(line["demand_id"]): line for line in final_allocations}
    if {int(line["demand_id"]) for line in lines} != set(by_id):
        raise ValueError("Actuals must cover the same demand lines as the approved allocation.")
    end = date.fromisoformat(HORIZON_END) + timedelta(days=90)
    start = date.fromisoformat(HORIZON_START)
    checked = []
    for line in lines:
        source = by_id[int(line["demand_id"])]
        requested = float(source["requested_quantity"])
        delivered = float(line["delivered_m3"])
        if delivered < -0.01 or delivered > requested * (1 + DELIVERY_SLACK) + 0.05:
            raise ValueError(
                f"{source.get('customer_or_project', 'An order')} delivered quantity must sit between 0 and 5% above requested."
            )
        try:
            delivered_on = date.fromisoformat(str(line["actual_delivery_date"])[:10])
        except ValueError as exc:
            raise ValueError("Actual delivery date must be a calendar date.") from exc
        if delivered_on < start or delivered_on > end:
            raise ValueError("Actual delivery date must fall between 1 Oct 2026 and 90 days after the horizon.")
        internal = source.get("demand_type") == "Internal"
        days_lost = float(line.get("programme_days_lost") or 0)
        penalty = float(line.get("penalty_paid_rm") or 0)
        if days_lost < -0.01 or days_lost > 365:
            raise ValueError("Programme days lost must sit between 0 and 365.")
        if not internal and days_lost > 0.01:
            raise ValueError("Programme days lost are recorded on internal orders.")
        cap_penalty = float(source.get("contractual_penalty_rm") or source.get("contractual_penalty") or 0)
        if internal and penalty > 0.01:
            raise ValueError("Internal orders do not pay a liquidated-damages amount.")
        if penalty < -0.01 or penalty > cap_penalty + 0.05:
            raise ValueError("Penalty paid cannot exceed the contractual penalty on the order.")
        checked.append(
            {
                "demand_id": int(line["demand_id"]),
                "customer_or_project": source.get("customer_or_project"),
                "demand_type": source.get("demand_type"),
                "requested_m3": round_m3(requested),
                "delivered_m3": round_m3(delivered),
                "actual_delivery_date": delivered_on.isoformat(),
                "programme_days_lost": round(days_lost, 2),
                "penalty_paid_rm": round_rm(penalty),
                "delay_cost_per_day_rm": float(source.get("delay_cost_per_day_rm") or 0),
                "contribution_margin_rm": float(source.get("contribution_margin_rm") or 0),
            }
        )
    return checked


def score_plan_on_book(world: dict, plant_id: int, product_id: int, targets: dict[int, float]) -> float:
    from app.engine import score_targets

    return float(score_targets(world, plant_id, product_id, targets)["expected_consequence_rm"])
