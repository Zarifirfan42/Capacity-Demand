"""Business impact of the recommendation versus named allocation rules.

The headline comparison is the best simple rule on the same demand and supply.
Earliest-date is the second comparison. The practice proxy is a footnote.
The approved column appears only after a person records a decision.
None of these figures are observed savings.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from app.db import connect, fetch_all
from app.economics import ASSUMPTIONS, EXCESS_COVER_DAYS, carrying_cost, round_m3, round_rm
from app.engine import allocate, load_world, product_is_stockable, value_of_information, value_protected_band
from app.stress import load_stress_report


def _blank() -> dict:
    return {
        "unserved_m3": 0.0,
        "margin_at_risk_rm": 0.0,
        "penalty_at_risk_rm": 0.0,
        "delay_cost_rm": 0.0,
        "gross_consequence_rm": 0.0,
        "expected_consequence_rm": 0.0,
        "programme_days": 0.0,
        "consequence_avoided_rm": 0.0,
    }


def _add(target: dict, source: dict) -> None:
    for key in target:
        target[key] = round(target[key] + float(source.get(key, 0.0)), 2)


def _best_rule_sum(result: dict) -> dict:
    totals = _blank()
    for bucket in result["buckets"]:
        code = bucket["headline_gap"].get("best_rule") or "earliest"
        policy = next(row for row in bucket["policies"] if row["policy_code"] == code)
        _add(totals, policy)
    return totals


def _policy_sum(result: dict, code: str) -> dict:
    totals = _blank()
    for bucket in result["buckets"]:
        policy = next(row for row in bucket["policies"] if row["policy_code"] == code)
        _add(totals, policy)
    return totals


def _inventory(world: dict, result: dict) -> dict:
    value = 0.0
    safety_value = 0.0
    usable_value = 0.0
    excess_value = 0.0
    excess_m3 = 0.0
    lines = []
    window_end = ""
    for stock in world["inventory"]:
        product = next(row for row in world["products"] if row["id"] == stock["product_id"])
        if not product_is_stockable(product):
            continue
        plant = next(row for row in world["plants"] if row["id"] == stock["plant_id"])
        unit = float(product["inventory_value_per_m3"])
        on_hand = float(stock["on_hand"])
        safety = float(stock["safety_stock"])
        as_of = date.fromisoformat(str(stock["as_of_date"]))
        window_end = (as_of + timedelta(days=EXCESS_COVER_DAYS - 1)).isoformat()
        forward = sum(
            float(demand["requested_quantity"])
            for demand in world["demands"]
            if demand["plant_id"] == stock["plant_id"]
            and demand["product_id"] == stock["product_id"]
            and demand["required_date"] <= window_end
        )
        excess = max(0.0, on_hand - safety - forward)
        line_value = on_hand * unit
        value += line_value
        safety_value += safety * unit
        usable_value += float(stock["usable"]) * unit
        excess_value += excess * unit
        excess_m3 += excess
        lines.append(
            {
                "plant_name": plant["name"],
                "product_name": product["name"],
                "on_hand_m3": on_hand,
                "safety_stock_m3": safety,
                "usable_m3": stock["usable"],
                "forward_cover_demand_m3": round_m3(forward),
                "stockable": True,
                "excess_m3": round_m3(excess),
                "inventory_value_rm": round_rm(line_value),
                "excess_value_rm": round_rm(excess * unit),
                "unit_value_rm": unit,
                "unit_value_is_assumption": True,
            }
        )
    consumed_value = 0.0
    for bucket in result["buckets"]:
        consumed_value += bucket["inventory_used_m3"] * bucket["inventory_value_per_m3"]
    constrained_on_hand_value = 0.0
    for bucket in result["buckets"]:
        if bucket["constrained"]:
            constrained_on_hand_value += bucket["on_hand_m3"] * bucket["inventory_value_per_m3"]
    return {
        "inventory_value_rm": round_rm(value),
        "safety_stock_value_rm": round_rm(safety_value),
        "usable_inventory_value_rm": round_rm(usable_value),
        "excess_inventory_m3": round_m3(excess_m3),
        "excess_inventory_value_rm": round_rm(excess_value),
        "working_capital_rm": round_rm(value),
        "carrying_cost_rm": carrying_cost(value),
        "carrying_rate_annual": 0.08,
        "excess_cover_days": EXCESS_COVER_DAYS,
        "excess_window_end": window_end,
        "working_capital_note": (
            "Precast only. Ready-mix cannot be stocked. Inventory value is on-hand × the assumed unit cost. "
            "That balance is not the carrying cost. "
            f"Carrying cost for this 30-day horizon = inventory value × 8% a year × 30/365. "
            f"Excess is on-hand above safety stock and demand due by {window_end}, "
            f"which is {EXCESS_COVER_DAYS} days from the stock as-of date. The 8% rate and the {EXCESS_COVER_DAYS}-day window are assumptions."
        ),
        "inventory_consumed_value_rm": round_rm(consumed_value),
        "inventory_at_risk_rm": round_rm(constrained_on_hand_value),
        "lines": lines,
    }


def _expedite(result: dict) -> dict:
    actions = []
    worth_cost = 0.0
    worth_benefit = 0.0
    rejected_cost = 0.0
    for bucket in result["buckets"]:
        for row in bucket["expedite_screen"]:
            actions.append({**row, "plant_name": bucket["plant_name"], "product_name": bucket["product_name"]})
            if row["worth_expediting"]:
                worth_cost += row["expedite_cost_rm"]
                worth_benefit += row["net_benefit_rm"]
            else:
                rejected_cost += row["expedite_cost_rm"]
    return {
        "actions": actions,
        "emergency_cost_if_clearing_worthwhile_shortfalls_rm": round_rm(worth_cost),
        "net_benefit_if_those_are_expedited_rm": round_rm(worth_benefit),
        "emergency_cost_if_clearing_low_consequence_shortfalls_rm": round_rm(rejected_cost),
        "note": "Emergency cost closes a firing lump. It is a labelled assumption and a proposal for approval. It is not added to base capacity.",
    }


def _latest_decisions() -> list[dict]:
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM decisions ORDER BY id DESC")
    latest: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = (row["plant_id"], row["product_id"])
        if key not in latest:
            row["recommended"] = json.loads(row["recommended_json"])
            row["final"] = json.loads(row["final_json"])
            latest[key] = row
    return list(latest.values())


def build_impact() -> dict:
    result = allocate(None)
    world = load_world()
    practice = _policy_sum(result, "practice")
    earliest = _policy_sum(result, "earliest")
    best = _best_rule_sum(result)
    internal = _policy_sum(result, "internal")
    external = _policy_sum(result, "external")
    pilot = _policy_sum(result, "optimised")
    decisions = _latest_decisions()
    approved = _blank()
    approved_notes = []
    covered = set()
    for bucket in result["buckets"]:
        decision = next((row for row in decisions if row["plant_id"] == bucket["plant_id"] and row["product_id"] == bucket["product_id"]), None)
        current_ids = {line["demand_id"] for line in bucket["allocations"]}
        if decision is None:
            _add(approved, next(row for row in bucket["policies"] if row["policy_code"] == "optimised"))
            continue
        final_ids = {line["demand_id"] for line in decision["final"].get("allocations", [])}
        if final_ids != current_ids:
            _add(approved, next(row for row in bucket["policies"] if row["policy_code"] == "optimised"))
            approved_notes.append(
                f"{bucket['plant_name']} / {bucket['product_name']}: the recorded decision does not match the current demand book, so this bucket still shows the recommendation."
            )
            continue
        final_totals = decision["final"]["totals"]
        _add(approved, final_totals)
        covered.add((bucket["plant_id"], bucket["product_id"]))
    if not decisions:
        approved_notes.append("No allocation has been approved yet. The approved column matches the recommendation until a planner records a decision.")
    inventory = _inventory(world, result)
    expedite = _expedite(result)
    band = value_protected_band(result)
    return {
        "horizon": result["horizon"],
        "baseline_name": "Best simple rule — primary comparison, not observed practice",
        "pilot_name": "Optimised — minimise business consequence",
        "approved_name": "Approved — human decision where recorded",
        "columns": [
            {"key": "best", "label": "Best simple rule", "detail": "Lowest of earliest-date, penalty and delay per m³, complete-or-skip, and greedy unit expected, chosen per plant-product.", **best},
            {"key": "earliest", "label": "Earliest required date", "detail": "Second comparison on the same demand and capacity. A named rule, not a record of what the plant did.", **earliest},
            {"key": "pilot", "label": "Optimised", "detail": "Mixed-integer programme under each order's penalty and delay type.", **pilot},
            {"key": "approved", "label": "Approved plan", "detail": "Latest human decision on each plant and product, otherwise the recommendation.", **approved},
        ],
        "upper_bound": {
            "key": "practice",
            "label": "Current practice proxy — upper bound only",
            "detail": band["practice_proxy_note"],
            "gap_rm": band["practice_proxy_gap_rm"],
            **practice,
        },
        "reference_policies": [
            {"key": "internal", "label": "Internal projects first", **internal},
            {"key": "external", "label": "External customers first", **external},
        ],
        "inventory": inventory,
        "expedite": expedite,
        "value_protected_rm": result["totals"]["value_protected_vs_best_rule_rm"],
        "value_protected_vs_earliest_rm": result["totals"]["value_protected_vs_earliest_rm"],
        "best_rule_by_bucket": result["totals"]["best_rule_by_bucket"],
        "verify_contracts": value_of_information(result),
        "stress": load_stress_report(),
        "value_protected_range": band,
        "value_protected_note": band["formula"],
        "approved_buckets": len(covered),
        "approved_notes": approved_notes,
        "decisions": [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "username": row["username"],
                "plant_name": row["plant_name"],
                "product_name": row["product_name"],
                "status": row["status"],
                "override_reason": row["override_reason"],
                "consequence_recommended": row["consequence_recommended"],
                "consequence_final": row["consequence_final"],
                "unserved_recommended": row["unserved_recommended"],
                "unserved_final": row["unserved_final"],
            }
            for row in decisions
        ],
        "assumptions": ASSUMPTIONS,
    }
