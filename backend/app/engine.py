"""Deterministic allocation engine.

A linear programme assigns scarce plant-product supply to demand. The objective
is the expected ringgit consequence of what is left unserved. Internal work is
not preferred. External work is not preferred. People approve the result.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime

import pulp

from app.db import connect, fetch_all
from app.economics import (
    ASSUMPTIONS,
    CRITICALITY_MULT,
    HORIZON_END,
    HORIZON_START,
    consequence_for_unserved,
    expedite_advice,
    line_economics,
    round_m3,
    round_rm,
)

TOL = 0.05


def _pretty(iso: str) -> str:
    parsed = datetime.strptime(iso, "%Y-%m-%d")
    return parsed.strftime("%d %b %Y").lstrip("0")


def _rm(value: float) -> str:
    return f"RM{value:,.0f}"


def _m3(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return f"{value:,.0f} m³"
    return f"{value:,.1f} m³"


def _snap(allocated: float, qty: float) -> float:
    allocated = min(max(allocated, 0.0), qty)
    if qty - allocated <= TOL:
        return qty
    if allocated <= TOL:
        return 0.0
    return round(allocated, 2)


def load_world() -> dict:
    with connect() as conn:
        plants = fetch_all(conn, "SELECT * FROM plants ORDER BY id")
        products = fetch_all(conn, "SELECT * FROM products ORDER BY id")
        calendar = fetch_all(
            conn,
            """
            SELECT * FROM capacity_calendar
            ORDER BY plant_id, product_id, prod_date
            """,
        )
        inventory = fetch_all(conn, "SELECT * FROM inventory")
        demands = fetch_all(conn, "SELECT * FROM demands ORDER BY required_date, id")
    for row in calendar:
        row["available_capacity"] = max(0.0, float(row["daily_capacity"]) - float(row["planned_production"]))
    for row in inventory:
        row["on_hand"] = float(row["on_hand"])
        row["safety_stock"] = float(row["safety_stock"])
        row["usable"] = max(0.0, row["on_hand"] - row["safety_stock"])
    return {
        "plants": plants,
        "products": products,
        "calendar": calendar,
        "inventory": inventory,
        "demands": demands,
        "scenario_notes": [],
    }


def _plant(world: dict, plant_id: int) -> dict:
    return next(row for row in world["plants"] if row["id"] == plant_id)


def _product(world: dict, product_id: int) -> dict:
    return next(row for row in world["products"] if row["id"] == product_id)


def apply_scenario(world: dict, scenario: dict | None) -> dict:
    if not scenario:
        world["scenario_name"] = "Baseline"
        return world
    world = deepcopy(world)
    notes: list[str] = []
    name = scenario.get("name") or "Scenario"
    factor = float(scenario.get("capacity_factor") or 1.0)
    plant_id = scenario.get("plant_id")
    product_id = scenario.get("product_id")
    if abs(factor - 1.0) > 0.001:
        for row in world["calendar"]:
            if plant_id and row["plant_id"] != plant_id:
                continue
            if product_id and row["product_id"] != product_id:
                continue
            row["available_capacity"] = max(0.0, row["available_capacity"] * factor)
        scope = "all plants and products"
        if plant_id:
            scope = _plant(world, plant_id)["name"]
            if product_id:
                scope += f" / {_product(world, product_id)['name']}"
        label = "emergency capacity above the committed plan" if factor > 1 else "a reduction in available capacity"
        notes.append(f"{name}: available capacity at {scope} set to {factor:.0%} ({label}).")

    for adj in scenario.get("inventory_adjustments") or []:
        for row in world["inventory"]:
            if row["plant_id"] == adj["plant_id"] and row["product_id"] == adj["product_id"]:
                delta = float(adj["on_hand_delta_m3"])
                row["on_hand"] = max(0.0, row["on_hand"] + delta)
                row["usable"] = max(0.0, row["on_hand"] - row["safety_stock"])
                notes.append(
                    f"{name}: on-hand at {_plant(world, row['plant_id'])['name']} / "
                    f"{_product(world, row['product_id'])['name']} changed by {_m3(delta)}. "
                    f"Usable inventory is now {_m3(row['usable'])}."
                )

    by_id = {row["id"]: row for row in world["demands"]}
    for adj in scenario.get("demand_adjustments") or []:
        demand = by_id.get(adj["demand_id"])
        if demand is None:
            continue
        label = demand["customer_or_project"]
        if adj.get("requested_quantity") is not None:
            demand["requested_quantity"] = float(adj["requested_quantity"])
            notes.append(f"{name}: {label} quantity set to {_m3(demand['requested_quantity'])}.")
        if adj.get("required_date"):
            demand["required_date"] = adj["required_date"]
            notes.append(f"{name}: {label} required date set to {demand['required_date']}.")
        if adj.get("contribution_margin") is not None:
            demand["contribution_margin"] = float(adj["contribution_margin"])
            notes.append(f"{name}: {label} contribution margin set to {_rm(demand['contribution_margin'])}.")
        if adj.get("contractual_penalty") is not None:
            demand["contractual_penalty"] = float(adj["contractual_penalty"])
            notes.append(f"{name}: {label} contractual penalty set to {_rm(demand['contractual_penalty'])}.")
        if adj.get("delay_days_if_unserved") is not None:
            demand["delay_days_if_unserved"] = float(adj["delay_days_if_unserved"])
            notes.append(f"{name}: {label} delay days set to {demand['delay_days_if_unserved']:g}.")
        explicit_delay_cost = adj.get("delay_cost_per_day") is not None
        if explicit_delay_cost:
            demand["delay_cost_per_day"] = float(adj["delay_cost_per_day"])
            notes.append(f"{name}: {label} delay cost set to {_rm(demand['delay_cost_per_day'])} per day.")
        if adj.get("project_criticality"):
            new_level = adj["project_criticality"]
            old_level = demand["project_criticality"]
            if new_level != old_level and not explicit_delay_cost:
                old_mult = CRITICALITY_MULT.get(old_level, 1.0)
                new_mult = CRITICALITY_MULT.get(new_level, 1.0)
                if old_mult > 0:
                    demand["delay_cost_per_day"] = float(demand["delay_cost_per_day"]) * (new_mult / old_mult)
                notes.append(
                    f"{name}: {label} criticality changed from {old_level} to {new_level}. "
                    f"Delay cost per day rescaled to {_rm(demand['delay_cost_per_day'])} "
                    f"(Critical 1.5, High 1.2, Medium 1.0, Low 0.7, relative to the previous level)."
                )
            else:
                notes.append(f"{name}: {label} criticality set to {new_level}.")
            demand["project_criticality"] = new_level
        if adj.get("confidence_level"):
            demand["confidence_level"] = adj["confidence_level"]
            notes.append(f"{name}: {label} confidence set to {demand['confidence_level']}.")
    world["scenario_name"] = name
    world["scenario_notes"] = notes
    return world


def _prepare_demands(demands: list[dict]) -> list[dict]:
    prepared = []
    for demand in demands:
        qty = float(demand["requested_quantity"])
        if qty <= 0:
            continue
        econ = line_economics(demand)
        prepared.append({**demand, **econ, "required_date": demand["required_date"]})
    return prepared


def _solve_lp(demands: list[dict], days: list[str], cap: dict[str, float], usable: float) -> dict[int, dict]:
    if not demands:
        return {}
    problem = pulp.LpProblem("capacity_allocation", pulp.LpMinimize)
    production: dict[tuple[int, str], pulp.LpVariable] = {}
    inventory_use: dict[int, pulp.LpVariable] = {}
    unserved: dict[int, pulp.LpVariable] = {}
    for demand in demands:
        demand_id = int(demand["id"])
        inventory_use[demand_id] = pulp.LpVariable(f"inv_{demand_id}", lowBound=0)
        unserved[demand_id] = pulp.LpVariable(f"uns_{demand_id}", lowBound=0, upBound=demand["quantity"])
        for day in days:
            if day <= demand["required_date"]:
                production[demand_id, day] = pulp.LpVariable(f"p_{demand_id}_{day.replace('-', '')}", lowBound=0)

    # A tiny weight prefers drawing usable inventory before burning line capacity.
    # It is far smaller than any ringgit consequence, so it cannot change who is served.
    problem += pulp.lpSum(demand["unit_expected_rm"] * unserved[int(demand["id"])] for demand in demands) + (
        1e-4 * pulp.lpSum(production.values())
    )

    for demand in demands:
        demand_id = int(demand["id"])
        made = pulp.lpSum(production[demand_id, day] for day in days if day <= demand["required_date"])
        problem += made + inventory_use[demand_id] + unserved[demand_id] == demand["quantity"], f"balance_{demand_id}"

    for day in days:
        users = [production[int(demand["id"]), day] for demand in demands if day <= demand["required_date"]]
        if users:
            problem += pulp.lpSum(users) <= cap[day], f"cap_{day.replace('-', '')}"

    problem += pulp.lpSum(inventory_use.values()) <= usable, "inventory"

    status = problem.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Allocation solver returned {pulp.LpStatus[status]}.")

    plan: dict[int, dict] = {}
    for demand in demands:
        demand_id = int(demand["id"])
        qty = float(demand["quantity"])
        inv = float(pulp.value(inventory_use[demand_id]) or 0.0)
        made = sum(float(pulp.value(production[demand_id, day]) or 0.0) for day in days if (demand_id, day) in production)
        allocated = _snap(inv + made, qty)
        # Keep the inventory/production split consistent with the snap.
        inv = min(inv, allocated)
        made = max(0.0, allocated - inv)
        by_day = []
        for day in days:
            if (demand_id, day) not in production:
                continue
            amount = float(pulp.value(production[demand_id, day]) or 0.0)
            if amount > TOL:
                by_day.append({"date": day, "quantity": round_m3(amount)})
        plan[demand_id] = {
            "allocated": allocated,
            "unserved": round_m3(qty - allocated),
            "from_inventory": round_m3(inv),
            "from_production": round_m3(made),
            "by_day": by_day,
        }
    return plan


def _greedy(demands: list[dict], days: list[str], cap: dict[str, float], usable: float, policy: str) -> dict[int, dict]:
    remaining_cap = dict(cap)
    remaining_inv = usable

    def sort_key(demand: dict):
        if policy == "internal":
            return (0 if demand["demand_type"] == "Internal" else 1, -demand["unit_expected_rm"], demand["required_date"], demand["id"])
        if policy == "external":
            return (0 if demand["demand_type"] == "External" else 1, -demand["unit_expected_rm"], demand["required_date"], demand["id"])
        return (demand["required_date"], -demand["unit_expected_rm"], demand["id"])

    plan: dict[int, dict] = {}
    for demand in sorted(demands, key=sort_key):
        need = float(demand["quantity"])
        from_inv = min(need, remaining_inv)
        remaining_inv -= from_inv
        need -= from_inv
        from_prod = 0.0
        for day in days:
            if day > demand["required_date"] or need <= TOL:
                break
            take = min(need, remaining_cap[day])
            remaining_cap[day] -= take
            need -= take
            from_prod += take
        allocated = _snap(float(demand["quantity"]) - max(need, 0.0), float(demand["quantity"]))
        plan[int(demand["id"])] = {
            "allocated": allocated,
            "unserved": round_m3(float(demand["quantity"]) - allocated),
            "from_inventory": round_m3(from_inv),
            "from_production": round_m3(from_prod),
            "by_day": [],
        }
    return plan


def _line_view(demand: dict, slot: dict, rank: int, emergency: float, emergency_is_assumption: bool) -> dict:
    impact = consequence_for_unserved(demand, slot["unserved"])
    avoided_days = 0.0
    if demand["demand_type"] == "Internal" and demand["quantity"] > 0:
        avoided_days = float(demand["delay_days_if_unserved"]) * (slot["allocated"] / demand["quantity"])
    return {
        "demand_id": demand["id"],
        "demand_code": demand["demand_code"],
        "demand_type": demand["demand_type"],
        "customer_or_project": demand["customer_or_project"],
        "customer_type": demand["customer_type"],
        "required_date": demand["required_date"],
        "requested_quantity": round_m3(demand["quantity"]),
        "allocated_quantity": impact["allocated_quantity"],
        "unserved_quantity": impact["unserved_quantity"],
        "confidence_level": demand["confidence_level"],
        "confidence_factor": demand["confidence_factor"],
        "project_criticality": demand["project_criticality"],
        "demand_status": demand["demand_status"],
        "contribution_margin_rm": round_rm(demand["contribution_margin_rm"]),
        "contractual_penalty_rm": round_rm(demand["contractual_penalty_rm"]),
        "delay_days_if_unserved": demand["delay_days_if_unserved"],
        "delay_cost_per_day_rm": round_rm(demand["delay_cost_per_day_rm"]),
        "unit_gross_rm": round_rm(demand["unit_gross_rm"]),
        "unit_expected_rm": round_rm(demand["unit_expected_rm"]),
        "rank_by_expected_consequence": rank,
        "margin_at_risk_rm": impact["margin_at_risk_rm"],
        "penalty_at_risk_rm": impact["penalty_at_risk_rm"],
        "delay_cost_incurred_rm": impact["delay_cost_incurred_rm"],
        "gross_consequence_rm": impact["gross_consequence_rm"],
        "expected_consequence_rm": impact["expected_consequence_rm"],
        "programme_days": impact["programme_days"],
        "margin_avoided_rm": round_rm(demand["contribution_margin_rm"] - impact["margin_at_risk_rm"]),
        "penalty_avoided_rm": round_rm(demand["contractual_penalty_rm"] - impact["penalty_at_risk_rm"]),
        "delay_cost_avoided_rm": round_rm(demand["delay_cost_if_fully_unserved_rm"] - impact["delay_cost_incurred_rm"]),
        "programme_days_avoided": round(avoided_days, 2),
        "consequence_avoided_rm": impact["consequence_avoided_gross_rm"],
        "from_inventory_m3": slot["from_inventory"],
        "from_production_m3": slot["from_production"],
        "production_by_day": slot.get("by_day") or [],
        "notes": demand.get("notes") or "",
        "emergency_cost_per_m3": emergency,
        "emergency_cost_is_assumption": emergency_is_assumption,
    }


def _totals(lines: list[dict]) -> dict:
    return {
        "requested_m3": round_m3(sum(line["requested_quantity"] for line in lines)),
        "allocated_m3": round_m3(sum(line["allocated_quantity"] for line in lines)),
        "unserved_m3": round_m3(sum(line["unserved_quantity"] for line in lines)),
        "margin_at_risk_rm": round_rm(sum(line["margin_at_risk_rm"] for line in lines)),
        "penalty_at_risk_rm": round_rm(sum(line["penalty_at_risk_rm"] for line in lines)),
        "delay_cost_rm": round_rm(sum(line["delay_cost_incurred_rm"] for line in lines)),
        "gross_consequence_rm": round_rm(sum(line["gross_consequence_rm"] for line in lines)),
        "expected_consequence_rm": round_rm(sum(line["expected_consequence_rm"] for line in lines)),
        "programme_days": round(sum(line["programme_days"] for line in lines), 2),
        "consequence_avoided_rm": round_rm(sum(line["consequence_avoided_rm"] for line in lines)),
    }


def _policy_block(code: str, label: str, method: str, demands: list[dict], plan: dict[int, dict], emergency: float, assumption: bool) -> dict:
    ranked = sorted(demands, key=lambda row: (-row["unit_expected_rm"], row["required_date"], row["id"]))
    rank = {int(row["id"]): index for index, row in enumerate(ranked, start=1)}
    lines = [_line_view(row, plan[int(row["id"])], rank[int(row["id"])], emergency, assumption) for row in demands]
    totals = _totals(lines)
    return {
        "policy_code": code,
        "policy": label,
        "method": method,
        **totals,
        "served": [
            {"demand_id": line["demand_id"], "name": line["customer_or_project"], "allocated_m3": line["allocated_quantity"], "requested_m3": line["requested_quantity"]}
            for line in lines
            if line["allocated_quantity"] > TOL
        ],
        "left_unserved": [
            {"demand_id": line["demand_id"], "name": line["customer_or_project"], "unserved_m3": line["unserved_quantity"], "requested_m3": line["requested_quantity"], "expected_consequence_rm": line["expected_consequence_rm"]}
            for line in lines
            if line["unserved_quantity"] > TOL
        ],
    }


def _cards(lines: list[dict]) -> list[dict]:
    cards = []
    for line in sorted(lines, key=lambda row: (-row["allocated_quantity"], -row["unit_expected_rm"])):
        if line["allocated_quantity"] <= TOL and line["unserved_quantity"] <= TOL:
            continue
        bullets = []
        if line["allocated_quantity"] > TOL:
            bullets.append(f"Allocate {_m3(line['allocated_quantity'])} of {_m3(line['requested_quantity'])}.")
            if line["programme_days_avoided"] > 0.05:
                bullets.append(f"Programme delay avoided: {line['programme_days_avoided']:g} days.")
            if line["delay_cost_avoided_rm"] > 1:
                bullets.append(f"Delay cost avoided: {_rm(line['delay_cost_avoided_rm'])}.")
            if line["margin_avoided_rm"] > 1:
                bullets.append(f"Contribution margin protected: {_rm(line['margin_avoided_rm'])}.")
            if line["penalty_avoided_rm"] > 1:
                bullets.append(f"Contractual penalty avoided: {_rm(line['penalty_avoided_rm'])}.")
            bullets.append(f"Business consequence avoided: {_rm(line['consequence_avoided_rm'])}.")
        if line["unserved_quantity"] > TOL:
            bullets.append(f"Leave {_m3(line['unserved_quantity'])} unserved.")
            if line["delay_cost_incurred_rm"] > 1:
                bullets.append(f"Programme delay accepted: {_rm(line['delay_cost_incurred_rm'])} ({line['programme_days']:g} days).")
            if line["penalty_at_risk_rm"] > 1:
                bullets.append(f"Contractual penalty accepted: {_rm(line['penalty_at_risk_rm'])}.")
            if line["margin_at_risk_rm"] > 1:
                bullets.append(f"Contribution margin deferred: {_rm(line['margin_at_risk_rm'])}.")
            bullets.append(f"Consequence accepted: {_rm(line['gross_consequence_rm'])}.")
        cards.append(
            {
                "demand_id": line["demand_id"],
                "demand_code": line["demand_code"],
                "name": line["customer_or_project"],
                "demand_type": line["demand_type"],
                "allocated_m3": line["allocated_quantity"],
                "unserved_m3": line["unserved_quantity"],
                "requested_m3": line["requested_quantity"],
                "bullets": bullets,
            }
        )
    return cards


def _crunch_date(windows: list[dict]) -> str | None:
    positive = [row for row in windows if row["gap_m3"] > TOL]
    if not positive:
        return None
    return max(positive, key=lambda row: row["gap_m3"])["date"]


def _reasons(lines: list[dict], windows: list[dict]) -> None:
    """Explain each line against orders that share the scarce dates.

    A later order served from capacity after the crunch is not the reason an
    earlier order was left unserved. Those cubic metres cannot be swapped.
    """
    crunch = _crunch_date(windows)
    served = [line for line in lines if line["allocated_quantity"] > TOL]
    window_served = [line for line in served if crunch and line["required_date"] <= crunch] or served
    for line in lines:
        if line["unserved_quantity"] <= TOL:
            line["reason"] = (
                f"Served in full. Missing 1 m³ would cost {_rm(line['unit_expected_rm'])} of expected consequence "
                f"({line['confidence_level'].lower()} weight {line['confidence_factor']:.0%}). "
                f"That is margin, penalty, and programme delay — not a preference for {line['demand_type'].lower()} demand."
            )
            continue
        others = [row for row in window_served if row["demand_id"] != line["demand_id"]]
        outranks = [row for row in others if row["unit_expected_rm"] >= line["unit_expected_rm"] - 0.01]
        marginal = min(outranks or others, key=lambda row: row["unit_expected_rm"]) if others else None
        if marginal is None:
            line["reason"] = "No supply is available on or before the required date."
            continue
        gap = marginal["unit_expected_rm"] - line["unit_expected_rm"]
        earlier = ""
        if line["required_date"] < marginal["required_date"]:
            earlier = (
                f" It is due earlier ({_pretty(line['required_date'])}) than {marginal['customer_or_project']} "
                f"({_pretty(marginal['required_date'])}). The earlier date restricts which days can produce it. "
                f"It does not raise the consequence above the order that received the cubic metre."
            )
        if line["allocated_quantity"] <= TOL:
            line["reason"] = (
                f"Left unserved. Expected consequence is {_rm(line['unit_expected_rm'])}/m³, "
                f"below {marginal['customer_or_project']} at {_rm(marginal['unit_expected_rm'])}/m³ on the same dated supply. "
                f"Giving this order 1 m³ instead would raise expected consequence by {_rm(max(gap, 0))}.{earlier}"
            )
        else:
            line["reason"] = (
                f"Partially served. Dated supply runs out at {_rm(line['unit_expected_rm'])}/m³, "
                f"after {marginal['customer_or_project']} at {_rm(marginal['unit_expected_rm'])}/m³."
            )


def _narrative(plant: str, product: str, supply: dict, lines: list[dict], policies: list[dict], expedites: list[dict], maintenance: list[str], windows: list[dict]) -> dict:
    optimised = next(row for row in policies if row["policy_code"] == "optimised")
    earliest = next(row for row in policies if row["policy_code"] == "earliest")
    internal = next(row for row in policies if row["policy_code"] == "internal")
    external = next(row for row in policies if row["policy_code"] == "external")
    crunch = max(windows, key=lambda row: row["gap_m3"]) if windows else None
    paragraphs = [
        (
            f"{plant} / {product}: uncommitted capacity over 1–30 Oct is {_m3(supply['available_capacity_m3'])}, "
            f"and usable inventory is {_m3(supply['usable_inventory_m3'])}. "
            f"Available supply is {_m3(supply['available_supply_m3'])}. "
            f"Open demand is {_m3(supply['total_demand_m3'])}."
        )
    ]
    if crunch and crunch["gap_m3"] > TOL:
        paragraphs.append(
            f"By {_pretty(crunch['date'])}, orders due on or before that date total {_m3(crunch['demand_to_date_m3'])}. "
            f"Supply that can still meet them is {_m3(crunch['supply_to_date_m3'])} "
            f"({_m3(crunch['capacity_to_date_m3'])} of capacity plus {_m3(crunch['usable_inventory_m3'])} of usable inventory). "
            f"The dated gap is {_m3(crunch['gap_m3'])}. Capacity after a required date cannot be used for that order."
        )
    elif supply["shortfall_m3"] <= TOL:
        paragraphs.append("Dated supply covers this book. These orders are not competing for a shortfall.")
    paragraphs.append(
        "The engine does not rank internal projects above external customers, or the reverse. "
        "It minimises expected business consequence: contribution margin, contractual penalty, and programme delay cost, "
        "multiplied by confidence (Confirmed 100%, Probable 75%, Forecast 45%)."
    )
    ranked = sorted(lines, key=lambda row: -row["unit_expected_rm"])
    ranking = []
    for index, line in enumerate(ranked, start=1):
        parts = []
        if line["contribution_margin_rm"] > 0:
            parts.append(f"margin {_rm(line['contribution_margin_rm'])}")
        if line["contractual_penalty_rm"] > 0:
            parts.append(f"penalty {_rm(line['contractual_penalty_rm'])}")
        if line["delay_days_if_unserved"] > 0:
            parts.append(
                f"{line['delay_days_if_unserved']:g} programme days at {_rm(line['delay_cost_per_day_rm'])}/day"
            )
        weight = ""
        if line["confidence_level"] != "Confirmed":
            weight = f", then weighted at {line['confidence_factor']:.0%} because the order is {line['confidence_level'].lower()}"
        ranking.append(
            f"{index}. {line['customer_or_project']} ({line['demand_type']}, {line['confidence_level']}) — "
            f"{_rm(line['unit_expected_rm'])}/m³ from {', '.join(parts) or 'no recorded money impact'}{weight}."
        )
    crunch_day = crunch["date"] if crunch and crunch["gap_m3"] > TOL else None
    window = [line for line in lines if crunch_day and line["required_date"] <= crunch_day]
    served = [line for line in window if line["allocated_quantity"] > TOL] or [line for line in lines if line["allocated_quantity"] > TOL]
    unserved = [line for line in window if line["unserved_quantity"] > TOL] or [line for line in lines if line["unserved_quantity"] > TOL]
    tradeoff = ""
    if served and unserved:
        marginal = min(served, key=lambda row: row["unit_expected_rm"])
        challengers = [row for row in unserved if row["demand_id"] != marginal["demand_id"]]
        challenger = max(challengers, key=lambda row: row["unit_expected_rm"]) if challengers else None
        delta = (marginal["unit_expected_rm"] - challenger["unit_expected_rm"]) if challenger else 0.0
        served_text = "; ".join(
            f"{line['customer_or_project']} {_m3(line['allocated_quantity'])}"
            for line in sorted(served, key=lambda row: -row["unit_expected_rm"])
        )
        tradeoff = f"Supply that can meet orders due by {_pretty(crunch_day) if crunch_day else 'the required dates'} is given to: {served_text}."
        if challenger is not None:
            tradeoff += (
                f" The cubic metre on the margin goes to {marginal['customer_or_project']} "
                f"at {_rm(marginal['unit_expected_rm'])}/m³ rather than {challenger['customer_or_project']} "
                f"at {_rm(challenger['unit_expected_rm'])}/m³. "
                f"Moving 1 m³ across would raise expected consequence by {_rm(max(delta, 0))}."
            )
        if challenger is not None and challenger["required_date"] < marginal["required_date"]:
            tradeoff += (
                f" {challenger['customer_or_project']} is due on {_pretty(challenger['required_date'])}, earlier than "
                f"{marginal['customer_or_project']} on {_pretty(marginal['required_date'])}. "
                f"Earliness is a production constraint, not a priority rule."
            )
    policy_text = (
        f"On the same capacity, an earliest-required-date rule leaves expected consequence of {_rm(earliest['expected_consequence_rm'])}. "
        f"Internal-first leaves {_rm(internal['expected_consequence_rm'])}. "
        f"External-first leaves {_rm(external['expected_consequence_rm'])}. "
        f"The recommendation leaves {_rm(optimised['expected_consequence_rm'])}. "
        f"Value protected versus earliest-date is {_rm(earliest['expected_consequence_rm'] - optimised['expected_consequence_rm'])}."
    )
    expedite_text = ""
    worth = [row for row in expedites if row["worth_expediting"]]
    skip = [row for row in expedites if not row["worth_expediting"]]
    if worth or skip:
        bits = []
        if worth:
            names = ", ".join(f"{row['customer_or_project']} ({_m3(row['unserved_quantity'])}, net benefit {_rm(row['net_benefit_rm'])})" for row in worth)
            bits.append(f"Emergency production would cost less than accepting the shortfall for: {names}.")
        if skip:
            names = ", ".join(row["customer_or_project"] for row in skip)
            bits.append(f"Emergency production would cost more than the consequence at stake for: {names}.")
        bits.append("Emergency cost is an assumption and is not in base capacity. It is a human decision, not an automatic top-up.")
        expedite_text = " ".join(bits)
    return {
        "summary": paragraphs[0],
        "paragraphs": paragraphs,
        "ranking": ranking,
        "tradeoff": tradeoff,
        "policy_comparison": policy_text,
        "expedite": expedite_text,
        "maintenance": maintenance,
        "why": " ".join(part for part in [paragraphs[1] if len(paragraphs) > 1 else "", tradeoff, policy_text] if part),
    }


def _windows(demands: list[dict], days: list[str], cap: dict[str, float], usable: float) -> list[dict]:
    due_dates = sorted({row["required_date"] for row in demands})
    windows = []
    for due in due_dates:
        demand_to_date = sum(row["quantity"] for row in demands if row["required_date"] <= due)
        capacity_to_date = sum(cap[day] for day in days if day <= due)
        supply = usable + capacity_to_date
        windows.append(
            {
                "date": due,
                "demand_to_date_m3": round_m3(demand_to_date),
                "capacity_to_date_m3": round_m3(capacity_to_date),
                "usable_inventory_m3": round_m3(usable),
                "supply_to_date_m3": round_m3(supply),
                "gap_m3": round_m3(max(0.0, demand_to_date - supply)),
            }
        )
    return windows


def solve_bucket(world: dict, plant_id: int, product_id: int) -> dict:
    plant = _plant(world, plant_id)
    product = _product(world, product_id)
    days = sorted(
        row["prod_date"]
        for row in world["calendar"]
        if row["plant_id"] == plant_id and row["product_id"] == product_id
    )
    cap = {
        row["prod_date"]: float(row["available_capacity"])
        for row in world["calendar"]
        if row["plant_id"] == plant_id and row["product_id"] == product_id
    }
    maintenance = [
        f"{_pretty(row['prod_date'])}: {row['note']}"
        for row in world["calendar"]
        if row["plant_id"] == plant_id and row["product_id"] == product_id and row.get("note")
    ]
    inventory = next(row for row in world["inventory"] if row["plant_id"] == plant_id and row["product_id"] == product_id)
    usable = float(inventory["usable"])
    demands = _prepare_demands(
        [row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    )
    emergency = float(product["emergency_cost_per_m3"])
    assumption = bool(product["emergency_cost_is_assumption"])
    optimised_plan = _solve_lp(demands, days, cap, usable)
    policies = [
        _policy_block("optimised", "Minimise business consequence", "Linear programme (CBC)", demands, optimised_plan, emergency, assumption),
        _policy_block("internal", "Internal projects first", "Priority rule on the same capacity", demands, _greedy(demands, days, cap, usable, "internal"), emergency, assumption),
        _policy_block("external", "External customers first", "Priority rule on the same capacity", demands, _greedy(demands, days, cap, usable, "external"), emergency, assumption),
        _policy_block("earliest", "Earliest required date", "Priority rule on the same capacity", demands, _greedy(demands, days, cap, usable, "earliest"), emergency, assumption),
    ]
    ranked = sorted(demands, key=lambda row: (-row["unit_expected_rm"], row["required_date"], row["id"]))
    rank = {int(row["id"]): index for index, row in enumerate(ranked, start=1)}
    lines = [_line_view(row, optimised_plan[int(row["id"])], rank[int(row["id"])], emergency, assumption) for row in demands]
    windows = _windows(demands, days, cap, usable)
    _reasons(lines, windows)
    lines.sort(key=lambda row: (row["rank_by_expected_consequence"]))
    totals = _totals(lines)
    available_capacity = sum(cap.values())
    total_demand = sum(row["quantity"] for row in demands)
    available_supply = available_capacity + usable
    used_by_day = {day: 0.0 for day in days}
    for slot in optimised_plan.values():
        for item in slot.get("by_day") or []:
            used_by_day[item["date"]] += item["quantity"]
    binding = []
    for day in days:
        available = cap[day]
        used = used_by_day[day]
        binding.append(
            {
                "date": day,
                "available_capacity_m3": round_m3(available),
                "used_m3": round_m3(used),
                "binding": available > TOL and used >= available - TOL,
            }
        )
    expedites = []
    for demand in demands:
        advice = expedite_advice(demand, optimised_plan[int(demand["id"])]["unserved"], emergency, assumption)
        if advice:
            expedites.append(advice)
    inventory_used = round_m3(sum(slot["from_inventory"] for slot in optimised_plan.values()))
    supply = {
        "available_capacity_m3": round_m3(available_capacity),
        "usable_inventory_m3": round_m3(usable),
        "on_hand_m3": round_m3(inventory["on_hand"]),
        "safety_stock_m3": round_m3(inventory["safety_stock"]),
        "available_supply_m3": round_m3(available_supply),
        "total_demand_m3": round_m3(total_demand),
        "shortfall_m3": totals["unserved_m3"],
        "aggregate_gap_m3": round_m3(max(0.0, total_demand - available_supply)),
        "inventory_used_m3": inventory_used,
        "internal_demand_m3": round_m3(sum(row["quantity"] for row in demands if row["demand_type"] == "Internal")),
        "external_demand_m3": round_m3(sum(row["quantity"] for row in demands if row["demand_type"] == "External")),
    }
    after_dates = [line["required_date"] for line in lines if line["unserved_quantity"] > TOL]
    stranded = 0.0
    stranded_note = ""
    if after_dates:
        latest = max(after_dates)
        stranded = sum(cap[day] for day in days if day > latest)
        if stranded > 1:
            stranded_note = (
                f"{_m3(stranded)} of {product['name']} capacity at {plant['name']} falls after {_pretty(latest)}. "
                f"Later orders can still use it. It cannot be brought back onto an order whose required date has passed."
            )
    return {
        "plant_id": plant_id,
        "plant_name": plant["name"],
        "product_id": product_id,
        "product_name": product["name"],
        "product_code": product["code"],
        "unit": product["unit"],
        "constrained": totals["unserved_m3"] > TOL,
        "inventory_value_per_m3": product["inventory_value_per_m3"],
        "inventory_value_is_assumption": True,
        **supply,
        **{k: totals[k] for k in totals if k not in supply},
        "allocations": lines,
        "policies": policies,
        "cards": _cards(lines),
        "expedite_screen": expedites,
        "binding_dates": [row for row in binding if row["binding"]],
        "windows": windows,
        "maintenance": maintenance,
        "stranded_capacity_m3": round_m3(stranded),
        "stranded_note": stranded_note,
        "explanation": _narrative(plant["name"], product["name"], supply, lines, policies, expedites, maintenance, windows),
        "objective": "Minimise expected contribution margin at risk + contractual penalty + programme delay cost, subject to dated plant capacity, usable inventory, product compatibility, and required dates.",
        "solver": "CBC linear programme",
    }


def allocate(scenario: dict | None = None) -> dict:
    world = apply_scenario(load_world(), scenario)
    buckets = [solve_bucket(world, plant["id"], product["id"]) for plant in world["plants"] for product in world["products"]]
    buckets.sort(key=lambda row: (-row["shortfall_m3"], row["plant_name"], row["product_name"]))

    def add(key: str) -> float:
        return round_rm(sum(bucket[key] for bucket in buckets)) if key.endswith("_rm") or key.endswith("days") else round_m3(sum(bucket[key] for bucket in buckets))

    optimised_expected = sum(bucket["expected_consequence_rm"] for bucket in buckets)
    earliest_expected = sum(next(p["expected_consequence_rm"] for p in bucket["policies"] if p["policy_code"] == "earliest") for bucket in buckets)
    internal_expected = sum(next(p["expected_consequence_rm"] for p in bucket["policies"] if p["policy_code"] == "internal") for bucket in buckets)
    external_expected = sum(next(p["expected_consequence_rm"] for p in bucket["policies"] if p["policy_code"] == "external") for bucket in buckets)
    totals = {
        "total_demand_m3": add("total_demand_m3"),
        "internal_demand_m3": add("internal_demand_m3"),
        "external_demand_m3": add("external_demand_m3"),
        "available_capacity_m3": add("available_capacity_m3"),
        "usable_inventory_m3": add("usable_inventory_m3"),
        "available_supply_m3": add("available_supply_m3"),
        "aggregate_gap_m3": round_m3(max(0.0, sum(b["total_demand_m3"] for b in buckets) - sum(b["available_supply_m3"] for b in buckets))),
        "horizon_surplus_m3": round_m3(max(0.0, sum(b["available_supply_m3"] for b in buckets) - sum(b["total_demand_m3"] for b in buckets))),
        "dated_shortfall_m3": add("shortfall_m3"),
        "allocated_m3": add("allocated_m3"),
        "margin_at_risk_rm": add("margin_at_risk_rm"),
        "penalty_at_risk_rm": add("penalty_at_risk_rm"),
        "delay_cost_rm": add("delay_cost_rm"),
        "gross_consequence_rm": add("gross_consequence_rm"),
        "expected_consequence_rm": round_rm(optimised_expected),
        "programme_days": round(sum(bucket["programme_days"] for bucket in buckets), 2),
        "value_protected_vs_earliest_rm": round_rm(earliest_expected - optimised_expected),
        "value_protected_vs_internal_first_rm": round_rm(internal_expected - optimised_expected),
        "value_protected_vs_external_first_rm": round_rm(external_expected - optimised_expected),
        "earliest_expected_consequence_rm": round_rm(earliest_expected),
        "internal_first_expected_consequence_rm": round_rm(internal_expected),
        "external_first_expected_consequence_rm": round_rm(external_expected),
        "constrained_buckets": sum(1 for bucket in buckets if bucket["constrained"]),
    }
    return {
        "scenario_name": world.get("scenario_name") or "Baseline",
        "scenario_notes": world.get("scenario_notes") or [],
        "horizon": {"start": HORIZON_START, "end": HORIZON_END},
        "solver": "CBC linear programme",
        "objective": buckets[0]["objective"] if buckets else "",
        "assumptions": ASSUMPTIONS,
        "totals": totals,
        "buckets": buckets,
    }


def _bucket_inputs(world: dict, plant_id: int, product_id: int):
    days = sorted(row["prod_date"] for row in world["calendar"] if row["plant_id"] == plant_id and row["product_id"] == product_id)
    cap = {row["prod_date"]: float(row["available_capacity"]) for row in world["calendar"] if row["plant_id"] == plant_id and row["product_id"] == product_id}
    inventory = next(row for row in world["inventory"] if row["plant_id"] == plant_id and row["product_id"] == product_id)
    demands = _prepare_demands([row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id])
    return days, cap, float(inventory["usable"]), demands


def feasibility(world: dict, plant_id: int, product_id: int, targets: dict[int, float]) -> dict:
    days, cap, usable, demands = _bucket_inputs(world, plant_id, product_id)
    by_id = {int(row["id"]): row for row in demands}
    unknown = [demand_id for demand_id in targets if demand_id not in by_id]
    if unknown:
        return {"feasible": False, "message": "One or more demand lines are not on this plant and product."}
    for demand_id, allocated in targets.items():
        qty = by_id[demand_id]["quantity"]
        if allocated < -TOL or allocated > qty + TOL:
            return {
                "feasible": False,
                "message": f"{by_id[demand_id]['customer_or_project']} cannot be allocated {_m3(allocated)}. Requested quantity is {_m3(qty)}.",
            }
    cumulative_capacity = 0.0
    for day in days:
        cumulative_capacity += cap[day]
        must = sum(float(targets.get(int(row["id"]), 0.0)) for row in demands if row["required_date"] <= day)
        have = usable + cumulative_capacity
        if must > have + 0.2:
            return {
                "feasible": False,
                "message": (
                    f"This override cannot be produced. By {day}, the edited allocation needs {_m3(must)} "
                    f"but dated supply is only {_m3(have)} (usable inventory plus capacity on or before that date)."
                ),
                "blocked_date": day,
                "required_m3": round_m3(must),
                "available_m3": round_m3(have),
            }
    return {"feasible": True, "message": "The edited allocation fits plant capacity, inventory, product, and required dates."}


def score_targets(world: dict, plant_id: int, product_id: int, targets: dict[int, float]) -> dict:
    _, _, _, demands = _bucket_inputs(world, plant_id, product_id)
    product = _product(world, product_id)
    lines = []
    for demand in demands:
        demand_id = int(demand["id"])
        allocated = _snap(float(targets.get(demand_id, 0.0)), demand["quantity"])
        slot = {"allocated": allocated, "unserved": demand["quantity"] - allocated, "from_inventory": 0, "from_production": allocated, "by_day": []}
        lines.append(_line_view(demand, slot, 0, float(product["emergency_cost_per_m3"]), bool(product["emergency_cost_is_assumption"])))
    return _totals(lines) | {"allocations": lines}


def capacity_view(plant_id: int, product_id: int) -> dict:
    world = load_world()
    plant = _plant(world, plant_id)
    product = _product(world, product_id)
    rows = [row for row in world["calendar"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    inventory = next(row for row in world["inventory"] if row["plant_id"] == plant_id and row["product_id"] == product_id)
    demands = [row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    cum_supply = float(inventory["usable"])
    cum_demand = 0.0
    series = []
    for row in rows:
        internal = sum(float(d["requested_quantity"]) for d in demands if d["required_date"] == row["prod_date"] and d["demand_type"] == "Internal")
        external = sum(float(d["requested_quantity"]) for d in demands if d["required_date"] == row["prod_date"] and d["demand_type"] == "External")
        due = internal + external
        cum_supply += float(row["available_capacity"])
        cum_demand += due
        series.append(
            {
                "date": row["prod_date"],
                "daily_capacity_m3": row["daily_capacity"],
                "planned_production_m3": row["planned_production"],
                "available_capacity_m3": row["available_capacity"],
                "internal_demand_due_m3": round_m3(internal),
                "external_demand_due_m3": round_m3(external),
                "demand_due_m3": round_m3(due),
                "cumulative_supply_m3": round_m3(cum_supply),
                "cumulative_demand_m3": round_m3(cum_demand),
                "cumulative_gap_m3": round_m3(max(0.0, cum_demand - cum_supply)),
                "constrained": cum_demand > cum_supply + TOL,
                "note": row["note"],
            }
        )
    bucket = solve_bucket(world, plant_id, product_id)
    return {
        "plant": plant,
        "product": product,
        "inventory": {
            "as_of_date": inventory["as_of_date"],
            "on_hand_m3": inventory["on_hand"],
            "safety_stock_m3": inventory["safety_stock"],
            "usable_m3": inventory["usable"],
            "inventory_value_per_m3": product["inventory_value_per_m3"],
            "inventory_value_rm": round_rm(inventory["on_hand"] * product["inventory_value_per_m3"]),
            "value_is_assumption": True,
        },
        "formulas": {
            "available_capacity": "Plant capacity − already planned production",
            "usable_inventory": "max(0, on-hand − safety stock)",
            "available_supply": "Available capacity + usable inventory",
            "dated_gap": "Demand due by a date − supply available on or before that date",
        },
        "series": series,
        "constrained_dates": [row["date"] for row in series if row["constrained"]],
        "unserved_orders": [
            {
                "demand_code": line["demand_code"],
                "customer_or_project": line["customer_or_project"],
                "required_date": line["required_date"],
                "unserved_quantity": line["unserved_quantity"],
            }
            for line in bucket["allocations"]
            if line["unserved_quantity"] > TOL
        ],
        "shortfall_m3": bucket["shortfall_m3"],
        "note": (
            "Highlighted dates are where cumulative volume due has run ahead of cumulative supply. "
            "Later capacity can make the cumulative lines cross again, but it cannot fill an order whose required date has passed."
        ),
    }


def control_tower() -> dict:
    result = allocate(None)
    totals = result["totals"]
    inventory_at_risk = 0.0
    for bucket in result["buckets"]:
        if bucket["constrained"]:
            inventory_at_risk += bucket["on_hand_m3"] * bucket["inventory_value_per_m3"]
    hotspots = []
    for bucket in result["buckets"]:
        if not bucket["constrained"]:
            continue
        crunch = max(bucket["windows"], key=lambda row: row["gap_m3"])
        hotspots.append(
            {
                "plant_id": bucket["plant_id"],
                "product_id": bucket["product_id"],
                "plant_name": bucket["plant_name"],
                "product_name": bucket["product_name"],
                "shortfall_m3": bucket["shortfall_m3"],
                "total_demand_m3": bucket["total_demand_m3"],
                "available_supply_m3": bucket["available_supply_m3"],
                "crunch_date": crunch["date"],
                "expected_consequence_rm": bucket["expected_consequence_rm"],
                "programme_days": bucket["programme_days"],
                "why": bucket["explanation"]["tradeoff"] or bucket["explanation"]["summary"],
            }
        )
    return {
        "question": "When plant capacity is constrained and total demand exceeds available capacity, how should the available capacity be allocated between internal projects and external customers, and what is the financial and operational consequence of that decision?",
        "horizon": result["horizon"],
        "kpis": {
            "total_demand_m3": totals["total_demand_m3"],
            "available_capacity_m3": totals["available_capacity_m3"],
            "usable_inventory_m3": totals["usable_inventory_m3"],
            "available_supply_m3": totals["available_supply_m3"],
            "capacity_gap_m3": totals["dated_shortfall_m3"],
            "aggregate_gap_m3": totals["aggregate_gap_m3"],
            "horizon_surplus_m3": totals["horizon_surplus_m3"],
            "internal_demand_m3": totals["internal_demand_m3"],
            "external_demand_m3": totals["external_demand_m3"],
            "inventory_at_risk_rm": round_rm(inventory_at_risk),
            "inventory_at_risk_note": "Assumption. On-hand quantity at constrained plant-products, valued at the prototype unit cost. Safety stock is inside this balance and is not allocated.",
            "margin_at_risk_rm": totals["margin_at_risk_rm"],
            "programme_days_at_risk": totals["programme_days"],
            "delay_cost_rm": totals["delay_cost_rm"],
            "penalty_at_risk_rm": totals["penalty_at_risk_rm"],
            "expected_consequence_rm": totals["expected_consequence_rm"],
            "gross_consequence_rm": totals["gross_consequence_rm"],
            "value_protected_rm": totals["value_protected_vs_earliest_rm"],
            "constrained_buckets": totals["constrained_buckets"],
        },
        "insight": (
            f"Adding the month together shows a surplus of {_m3(totals['horizon_surplus_m3'])}. "
            f"That surplus is in the wrong dates. Once required dates are respected, {_m3(totals['dated_shortfall_m3'])} stays unserved. "
            "The decision is which orders absorb that dated shortfall."
        ),
        "hotspots": hotspots,
        "policy_totals": {
            "optimised_rm": totals["expected_consequence_rm"],
            "earliest_rm": totals["earliest_expected_consequence_rm"],
            "internal_first_rm": totals["internal_first_expected_consequence_rm"],
            "external_first_rm": totals["external_first_expected_consequence_rm"],
        },
        "assumptions": ASSUMPTIONS,
    }


def parse_user_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Required date must be YYYY-MM-DD.") from exc
    if parsed < date.fromisoformat(HORIZON_START) or parsed > date.fromisoformat(HORIZON_END):
        raise ValueError(f"Required date must fall inside {HORIZON_START} to {HORIZON_END}.")
    return parsed.isoformat()
