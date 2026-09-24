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
    CONFIDENCE_FACTOR,
    CRITICALITY_MULT,
    EMERGENCY_CAPACITY_SHARE,
    FRAGILITY_REGRET_RM,
    FRAGILITY_REGRET_SHARE,
    HORIZON_END,
    HORIZON_START,
    OBJECTIVE_EPSILON_RM,
    READY_MIX_CODES,
    SENSITIVITY_FACTORS,
    SHARED_READY_MIX_BATCHING,
    SIMPLE_RULES,
    SOLVER_TIME_LIMIT_SECONDS,
    consequence_for_unserved,
    confidence_factor,
    delay_type_of,
    exposure_per_m3,
    full_miss_delay_weight,
    linear_unserved_rate,
    parent_score,
    penalty_type_of,
    penalty_unverified,
    expedite_advice,
    line_economics,
    remainder_confidence,
    review_case,
    round_m3,
    round_rm,
    score_parent_unserved,
)

TOL = 0.05


def _buffer_days() -> int:
    from app.db import connect, get_meta

    try:
        with connect() as conn:
            raw = get_meta(conn, "buffer_days")
    except Exception:
        return 0
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


def _schedule_deadline(required_date: str, penalty_type: str, buffer_days: int) -> str:
    """Lump-sum orders can be pulled forward by buffer_days. Default 0 leaves the due date unchanged."""
    if buffer_days <= 0 or penalty_type != "lump_sum":
        return required_date
    from datetime import date, timedelta

    from app.economics import HORIZON_START

    day = date.fromisoformat(required_date) - timedelta(days=buffer_days)
    start = date.fromisoformat(HORIZON_START)
    if day < start:
        day = start
    return day.isoformat()


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
        committed = float(row.get("committed_production") or 0.0)
        row["committed_production"] = committed
        row["available_capacity"] = max(0.0, float(row["daily_capacity"]) - float(row["planned_production"]) - committed)
    _clear_unstockable(products, inventory)
    world = {
        "plants": plants,
        "products": products,
        "calendar": calendar,
        "inventory": inventory,
        "demands": demands,
        "scenario_notes": [],
    }
    from app.operations import apply_open_commitments

    apply_open_commitments(world)
    return world


def _plant_mode(plant_id: int) -> str:
    from app.operations import plant_mode

    return plant_mode(plant_id)


def _open_decision(plant_id: int, product_id: int):
    from app.operations import open_decision_view

    return open_decision_view(plant_id, product_id)


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
    scale = scenario.get("penalty_and_delay_factor")
    if scale is not None and abs(float(scale) - 1.0) > 0.001:
        scale = float(scale)
        for demand in world["demands"]:
            demand["contractual_penalty"] = float(demand["contractual_penalty"]) * scale
            demand["delay_cost_per_day"] = float(demand["delay_cost_per_day"]) * scale
        notes.append(
            f"{name}: contractual penalty and delay cost per day set to {scale:.0%} of the book. "
            "Contribution margin is unchanged. The allocation is solved again."
        )
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
        if adj.get("unit_expected_factor") is not None:
            factor = float(adj["unit_expected_factor"])
            demand["contribution_margin"] = float(demand["contribution_margin"]) * factor
            demand["contractual_penalty"] = float(demand["contractual_penalty"]) * factor
            demand["delay_cost_per_day"] = float(demand["delay_cost_per_day"]) * factor
            notes.append(
                f"{name}: {label} unit expected consequence set to {factor:.0%} "
                "by scaling margin, contractual penalty, and delay cost together."
            )
    world["scenario_name"] = name
    world["scenario_notes"] = notes
    if "shared_ready_mix_batching" in scenario:
        world["shared_ready_mix_batching"] = bool(scenario["shared_ready_mix_batching"])
    _clear_unstockable(world["products"], world["inventory"])
    return world


def product_is_stockable(product: dict) -> bool:
    if product.get("stockable") is not None:
        return bool(product["stockable"])
    return product.get("code") not in READY_MIX_CODES


def _clear_unstockable(products: list[dict], inventory: list[dict]) -> None:
    stockable = {int(product["id"]): product_is_stockable(product) for product in products}
    for row in inventory:
        row["on_hand"] = float(row["on_hand"])
        row["safety_stock"] = float(row["safety_stock"])
        if not stockable.get(int(row["product_id"]), True):
            row["on_hand"] = 0.0
            row["safety_stock"] = 0.0
        row["usable"] = max(0.0, row["on_hand"] - row["safety_stock"])


def expand_tranches(demand: dict) -> list[dict]:
    """Split one order into a confirmed tranche and an unconfirmed remainder.

    The confirmed cubic metres use the Confirmed weight. The remainder uses
    Forecast when the line is a forecast, and Probable otherwise. Money and
    programme days are split in proportion to quantity so the two tranches
    add back to the order.
    """
    requested = float(demand["requested_quantity"])
    confirmed = min(max(float(demand.get("confirmed_quantity") or 0.0), 0.0), requested)
    remainder = requested - confirmed
    status = str(demand.get("demand_status") or "").strip().lower()
    parent_level = str(demand.get("confidence_level") or "Confirmed")
    remainder_level = "Forecast" if parent_level == "Forecast" or status == "forecast" else "Probable"
    parent_id = int(demand["id"])
    slices: list[tuple[float, str, str, int]] = []
    if confirmed > TOL and remainder > TOL:
        slices.append((confirmed, "Confirmed", "confirmed", parent_id))
        slices.append((remainder, remainder_level, "unconfirmed", -parent_id))
    elif confirmed > TOL:
        slices.append((confirmed, "Confirmed", "confirmed", parent_id))
    elif remainder > TOL:
        slices.append((remainder, remainder_level, "unconfirmed", parent_id))
    rows = []
    for qty, level, kind, tranche_id in slices:
        fraction = qty / requested if requested else 0.0
        row = dict(demand)
        row["id"] = tranche_id
        row["parent_id"] = parent_id
        row["tranche"] = kind
        row["score_parent"] = dict(demand)
        row["requested_quantity"] = qty
        row["confirmed_quantity"] = qty if kind == "confirmed" else 0.0
        row["confidence_level"] = level
        row["contribution_margin"] = float(demand["contribution_margin"]) * fraction
        # The contractual penalty is an obligation on the confirmed cubic metres only.
        row["contractual_penalty"] = float(demand["contractual_penalty"]) if kind == "confirmed" else 0.0
        row["delay_days_if_unserved"] = float(demand["delay_days_if_unserved"]) * fraction
        rows.append(row)
    return rows


def _collapse_plan(
    parents: list[dict],
    tranches: list[dict],
    tranche_plan: dict[int, dict],
    *,
    penalty_as: str | dict[int, str] | None = None,
    delay_as: str | None = None,
) -> dict[int, dict]:
    by_parent: dict[int, list[dict]] = {}
    for tranche in tranches:
        by_parent.setdefault(int(tranche["parent_id"]), []).append(tranche)
    plan: dict[int, dict] = {}
    for parent in parents:
        parent_id = int(parent["id"])
        parts = by_parent.get(parent_id, [])
        allocated = 0.0
        from_inv = 0.0
        from_prod = 0.0
        by_day: list[dict] = []
        money = {
            "margin_at_risk_rm": 0.0,
            "penalty_at_risk_rm": 0.0,
            "delay_cost_incurred_rm": 0.0,
            "gross_consequence_rm": 0.0,
            "expected_consequence_rm": 0.0,
            "programme_days": 0.0,
            "consequence_avoided_gross_rm": 0.0,
        }
        scored_parts: list[tuple[str, float, float]] = []
        notes = []
        for part in parts:
            slot = tranche_plan.get(int(part["id"]), {"allocated": 0.0, "unserved": part["quantity"], "from_inventory": 0.0, "from_production": 0.0, "by_day": []})
            allocated += float(slot["allocated"])
            from_inv += float(slot["from_inventory"])
            from_prod += float(slot["from_production"])
            by_day.extend(slot.get("by_day") or [])
            scored_parts.append((str(part.get("tranche") or "confirmed"), float(part["quantity"]), float(slot["unserved"])))
            weight = int(round(float(part["confidence_factor"]) * 100))
            notes.append(
                f"{_m3(part['quantity'])} {part['tranche']} at {weight}% ({part['confidence_level']})"
            )
        source = parts[0].get("score_parent") or parent
        penalty_mode = penalty_as.get(parent_id) if isinstance(penalty_as, dict) else penalty_as
        money = parent_score(source, scored_parts, penalty_as=penalty_mode, delay_as=delay_as)
        qty = float(parent["quantity"])
        allocated = _snap(allocated, qty)
        plan[parent_id] = {
            "allocated": allocated,
            "unserved": round_m3(qty - allocated),
            "from_inventory": round_m3(from_inv),
            "from_production": round_m3(from_prod),
            "by_day": by_day,
            "impact_override": {key: round_rm(value) if key != "programme_days" else round(value, 2) for key, value in money.items()},
            "tranche_note": (
                "Confirmed quantity and the unconfirmed remainder are separate tranches. " + "; ".join(notes) + "."
                if len(parts) > 1
                else ""
            ),
        }
    return plan


def _expanded(raw_demands: list[dict]) -> tuple[list[dict], list[dict]]:
    parents = _prepare_demands(raw_demands)
    tranches = _prepare_demands([row for demand in raw_demands for row in expand_tranches(demand)])
    return parents, tranches


def _plans_for(
    raw_demands: list[dict],
    days: list[str],
    cap: dict[str, float],
    usable: float,
    *,
    force_penalty: str | None = None,
    force_delay: str | None = None,
    lex: bool = True,
) -> dict[str, dict[int, dict]]:
    parents, tranches = _expanded(raw_demands)
    optimised = _collapse_plan(
        parents,
        tranches,
        _solve_lp(tranches, days, cap, usable, force_penalty=force_penalty, force_delay=force_delay, lex=lex),
    )
    if force_penalty is None and force_delay is None:
        _plans_for.base_meta = dict(getattr(_solve_lp, "last_meta", {}))  # type: ignore[attr-defined]
    policies = {"optimised": optimised}
    for name in ("internal", "external", "earliest", "practice"):
        policies[name] = _collapse_plan(parents, tranches, _greedy(tranches, days, cap, usable, name))
    for name in ("penalty_delay", "complete_or_skip", "unit_expected"):
        policies[name] = _score_parent_plan(parents, _parent_greedy(parents, days, cap, usable, name))
    return policies


def _prepare_demands(demands: list[dict]) -> list[dict]:
    prepared = []
    for demand in demands:
        qty = float(demand["requested_quantity"])
        if qty <= 0:
            continue
        econ = line_economics(demand)
        prepared.append({**demand, **econ, "required_date": demand["required_date"]})
    return prepared


def _parent_of(demand: dict) -> dict:
    return demand.get("score_parent") or demand


def _applied_type(demand: dict, kind: str, force: str | None, overrides: dict[int, str] | None = None) -> str:
    parent = _parent_of(demand)
    parent_id = int(demand.get("parent_id") or parent.get("id") or demand["id"])
    if overrides and parent_id in overrides:
        return overrides[parent_id]
    if force:
        return force
    return penalty_type_of(parent) if kind == "penalty" else delay_type_of(parent)


def _solve_lp(
    demands: list[dict],
    days: list[str],
    cap: dict[str, float],
    usable: float,
    unit_bonus: dict[int, float] | None = None,
    lump_groups: list[dict] | None = None,
    *,
    force_penalty: str | None = None,
    force_delay: str | None = None,
    penalty_overrides: dict[int, str] | None = None,
    lex: bool = True,
    epsilon: float = OBJECTIVE_EPSILON_RM,
    emergency_cost: float | None = None,
    emergency_share: float = 0.0,
) -> dict[int, dict]:
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

    bonus = unit_bonus or {}
    rates = {}
    for demand in demands:
        parent = _parent_of(demand)
        rates[int(demand["id"])] = linear_unserved_rate(
            parent,
            str(demand.get("tranche") or "confirmed"),
            float(demand["quantity"]),
            penalty_as=_applied_type(demand, "penalty", force_penalty, penalty_overrides),
            delay_as=_applied_type(demand, "delay", force_delay),
        ) + float(bonus.get(int(demand["id"]), 0.0))

    by_parent: dict[int, list[dict]] = {}
    for demand in demands:
        by_parent.setdefault(int(demand.get("parent_id") or demand["id"]), []).append(demand)

    lump_terms = []
    partials = []
    date_weight = []
    horizon = datetime.strptime(HORIZON_START, "%Y-%m-%d")
    for parent_id, pieces in by_parent.items():
        parent = _parent_of(pieces[0])
        requested = float(parent["requested_quantity"])
        confirmed = min(max(float(parent.get("confirmed_quantity") or 0.0), 0.0), requested)
        penalty_type = _applied_type(pieces[0], "penalty", force_penalty, penalty_overrides)
        delay_type = _applied_type(pieces[0], "delay", force_delay)
        parent_unserved = pulp.lpSum(unserved[int(piece["id"])] for piece in pieces)
        if penalty_type == "lump_sum" and confirmed > 0 and float(parent.get("contractual_penalty") or 0) > 0:
            binary = pulp.LpVariable(f"pen_{parent_id}", cat="Binary")
            confirmed_unserved = pulp.lpSum(
                unserved[int(piece["id"])] for piece in pieces if piece.get("tranche") == "confirmed"
            )
            threshold = 0.0 if str(parent.get("lump_sum_trigger") or "any").strip().lower() in {"", "any", "any shortfall"} else confirmed * float(str(parent.get("lump_sum_trigger")).replace("%", "")) / 100.0
            span = max(confirmed - threshold, 0.0)
            problem += confirmed_unserved <= threshold + span * binary, f"pen_trigger_{parent_id}"
            lump_terms.append(float(parent["contractual_penalty"]) * binary)
        delay_total = float(parent.get("delay_days_if_unserved") or 0) * float(parent.get("delay_cost_per_day") or 0)
        if delay_type == "lump_days" and delay_total > 0 and requested > 0:
            binary = pulp.LpVariable(f"day_{parent_id}", cat="Binary")
            raw_trigger = str(parent.get("lump_sum_trigger") or "any").strip().lower().replace("%", "")
            threshold = 0.0 if raw_trigger in {"", "any", "any shortfall"} else requested * float(raw_trigger) / 100.0
            span = max(requested - threshold, 0.0)
            problem += parent_unserved <= threshold + span * binary, f"day_trigger_{parent_id}"
            lump_terms.append(delay_total * full_miss_delay_weight(parent) * binary)
        minimum = float(parent.get("minimum_useful_delivery_m3") or 0.0)
        if minimum > TOL and minimum < requested - TOL:
            useful = pulp.LpVariable(f"useful_{parent_id}", cat="Binary")
            problem += parent_unserved <= (requested - minimum) + requested * (1 - useful), f"useful_hi_{parent_id}"
            problem += parent_unserved >= requested * (1 - useful), f"useful_lo_{parent_id}"
        miss = pulp.LpVariable(f"miss_{parent_id}", cat="Binary")
        hit = pulp.LpVariable(f"hit_{parent_id}", cat="Binary")
        partial = pulp.LpVariable(f"part_{parent_id}", cat="Binary")
        problem += parent_unserved <= requested * miss + 1e-4, f"miss_{parent_id}"
        problem += requested - parent_unserved <= requested * hit + 1e-4, f"hit_{parent_id}"
        problem += partial >= miss + hit - 1, f"partial_{parent_id}"
        partials.append(partial)
        due = datetime.strptime(str(parent["required_date"]), "%Y-%m-%d")
        date_weight.append((due - horizon).days * parent_unserved)

    business = pulp.lpSum(rates[int(demand["id"])] * unserved[int(demand["id"])] for demand in demands) + pulp.lpSum(lump_terms)
    extra: dict[str, pulp.LpVariable] = {}
    if emergency_cost is not None and emergency_share > 0:
        for day in days:
            extra[day] = pulp.LpVariable(f"em_{day.replace('-', '')}", lowBound=0, upBound=float(cap[day]) * float(emergency_share))
    emergency_expr = pulp.lpSum(float(emergency_cost or 0.0) * extra[day] for day in extra) if extra else 0
    # A tiny weight prefers drawing usable inventory before burning line capacity.
    # It is far smaller than any ringgit consequence, so it cannot change who is served.
    problem += business + emergency_expr + (1e-4 * pulp.lpSum(production.values()))

    for demand in demands:
        demand_id = int(demand["id"])
        made = pulp.lpSum(production[demand_id, day] for day in days if day <= demand["required_date"])
        problem += made + inventory_use[demand_id] + unserved[demand_id] == demand["quantity"], f"balance_{demand_id}"

    for day in days:
        users = [production[int(demand["id"]), day] for demand in demands if day <= demand["required_date"]]
        if users:
            ceiling = cap[day] + (extra[day] if day in extra else 0)
            problem += pulp.lpSum(users) <= ceiling, f"cap_{day.replace('-', '')}"

    problem += pulp.lpSum(inventory_use.values()) <= usable, "inventory"

    def _finish(status_name: str) -> None:
        if pulp.LpStatus[status_name] != "Optimal":
            raise RuntimeError(f"Allocation solver returned {pulp.LpStatus[status_name]}.")

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=SOLVER_TIME_LIMIT_SECONDS)
    status = problem.solve(solver)
    _finish(status)
    best = float(pulp.value(business) or 0.0)
    if lex and epsilon >= 0 and partials and not extra:
        problem += business <= best + float(epsilon), "epsilon_band"
        problem.setObjective(pulp.lpSum(partials))
        status = problem.solve(solver)
        _finish(status)
        best_partials = float(pulp.value(pulp.lpSum(partials)) or 0.0)
        problem += pulp.lpSum(partials) <= best_partials + 0.01, "partial_band"
        problem.setObjective(pulp.lpSum(date_weight))
        status = problem.solve(solver)
        _finish(status)

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
    _solve_lp.last_meta = {  # type: ignore[attr-defined]
        "status": pulp.LpStatus[status],
        "time_limit_seconds": SOLVER_TIME_LIMIT_SECONDS,
        "emergency_m3": round_m3(sum(float(pulp.value(extra[day]) or 0.0) for day in extra)),
    }
    return plan


def _policy_sort(demand: dict, policy: str):
    if policy == "internal":
        return (0 if demand["demand_type"] == "Internal" else 1, -demand["unit_expected_rm"], demand["required_date"], demand["id"])
    if policy == "external":
        return (0 if demand["demand_type"] == "External" else 1, -demand["unit_expected_rm"], demand["required_date"], demand["id"])
    if policy == "practice":
        # Illustrative informal rule. Firm orders first, then the due date, then commercial
        # margin and penalty. Programme delay is intentionally left out of this sort.
        certainty = {"Confirmed": 0, "Probable": 1, "Forecast": 2}.get(demand["confidence_level"], 1)
        commercial = float(demand["margin_per_m3"]) + float(demand["penalty_per_m3"])
        return (certainty, demand["required_date"], -commercial, demand["id"])
    return (demand["required_date"], -demand["unit_expected_rm"], demand["id"])


def _greedy(demands: list[dict], days: list[str], cap: dict[str, float], usable: float, policy: str) -> dict[int, dict]:
    remaining_cap = dict(cap)
    remaining_inv = usable
    buffer = _buffer_days()

    plan: dict[int, dict] = {}
    for demand in sorted(demands, key=lambda row: _policy_sort(row, policy)):
        deadline = _schedule_deadline(str(demand["required_date"]), penalty_type_of(demand), buffer)
        need = float(demand["quantity"])
        from_inv = min(need, remaining_inv)
        remaining_inv -= from_inv
        need -= from_inv
        from_prod = 0.0
        by_day = []
        for day in reversed(days):
            if day > deadline or need <= TOL:
                continue
            take = min(need, remaining_cap[day])
            if take <= TOL:
                continue
            remaining_cap[day] -= take
            need -= take
            from_prod += take
            by_day.append({"date": day, "quantity": round_m3(take)})
        allocated = _snap(float(demand["quantity"]) - max(need, 0.0), float(demand["quantity"]))
        plan[int(demand["id"])] = {
            "allocated": allocated,
            "unserved": round_m3(float(demand["quantity"]) - allocated),
            "from_inventory": round_m3(from_inv),
            "from_production": round_m3(from_prod),
            "by_day": by_day,
        }
    return plan


def _supply_by_date(days: list[str], cap: dict[str, float], inventory: float, required_date: str) -> float:
    return float(inventory) + sum(float(cap[day]) for day in days if day <= required_date)


def _draw(days: list[str], cap: dict[str, float], inventory: float, required_date: str, qty: float) -> tuple[float, float, float, list[dict]]:
    """Take up to qty from inventory, then from the latest feasible production day."""
    need = float(qty)
    from_inv = min(need, inventory)
    inventory -= from_inv
    need -= from_inv
    from_prod = 0.0
    by_day: list[dict] = []
    for day in reversed(days):
        if day > required_date or need <= TOL:
            continue
        take = min(need, cap[day])
        if take <= TOL:
            continue
        cap[day] -= take
        need -= take
        from_prod += take
        by_day.append({"date": day, "quantity": round_m3(take)})
    return float(qty) - max(need, 0.0), inventory, from_prod, by_day


def _parent_greedy(parents: list[dict], days: list[str], cap: dict[str, float], usable: float, policy: str) -> dict[int, dict]:
    """Fill whole orders. Dates limit production. Complete-or-skip refuses a lump-sum it cannot finish."""
    remaining_cap = dict(cap)
    remaining_inv = usable
    buffer = _buffer_days()
    if policy == "unit_expected":
        ordered = sorted(parents, key=lambda row: (-float(row["unit_expected_rm"]), row["required_date"], int(row["id"])))
    elif policy == "earliest":
        ordered = sorted(parents, key=lambda row: (row["required_date"], -float(row["unit_expected_rm"]), int(row["id"])))
    else:
        ordered = sorted(parents, key=lambda row: (-exposure_per_m3(row), row["required_date"], int(row["id"])))
    plan: dict[int, dict] = {}
    for demand in ordered:
        requested = float(demand["quantity"])
        confirmed = min(max(float(demand.get("confirmed_quantity") or 0.0), 0.0), requested)
        skip = False
        deadline = _schedule_deadline(str(demand["required_date"]), penalty_type_of(demand), buffer)
        if policy == "complete_or_skip" and penalty_type_of(demand) == "lump_sum" and confirmed > TOL:
            if _supply_by_date(days, remaining_cap, remaining_inv, deadline) + TOL < confirmed:
                skip = True
        if skip:
            plan[int(demand["id"])] = {
                "allocated": 0.0,
                "unserved": round_m3(requested),
                "from_inventory": 0.0,
                "from_production": 0.0,
                "by_day": [],
            }
            continue
        allocated, remaining_inv, from_prod, by_day = _draw(days, remaining_cap, remaining_inv, deadline, requested)
        allocated = _snap(allocated, requested)
        plan[int(demand["id"])] = {
            "allocated": allocated,
            "unserved": round_m3(requested - allocated),
            "from_inventory": round_m3(allocated - from_prod) if allocated + TOL >= from_prod else round_m3(allocated),
            "from_production": round_m3(min(from_prod, allocated)),
            "by_day": by_day,
        }
    return plan


def _score_parent_plan(parents: list[dict], raw_plan: dict[int, dict]) -> dict[int, dict]:
    scored: dict[int, dict] = {}
    for demand in parents:
        demand_id = int(demand["id"])
        slot = raw_plan.get(demand_id) or {
            "allocated": 0.0,
            "unserved": float(demand["quantity"]),
            "from_inventory": 0.0,
            "from_production": 0.0,
            "by_day": [],
        }
        money = score_parent_unserved(demand, float(slot["unserved"]))
        scored[demand_id] = {**slot, "impact_override": money}
    return scored


def _line_view(demand: dict, slot: dict, rank: int, emergency: float, emergency_is_assumption: bool) -> dict:
    impact = slot.get("impact_override") or consequence_for_unserved(demand, slot["unserved"])
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
        "confirmed_quantity": round_m3(float(demand.get("confirmed_quantity") or 0)),
        "allocated_quantity": round_m3(slot["allocated"]) if slot.get("impact_override") else impact["allocated_quantity"],
        "unserved_quantity": round_m3(slot["unserved"]) if slot.get("impact_override") else impact["unserved_quantity"],
        "tranche_note": slot.get("tranche_note") or "",
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
        dated = next((row for row in windows if row["date"] == line["required_date"]), None)
        if dated:
            line["why_not"] = (
                f"By {_pretty(line['required_date'])}, orders due on or before that date need "
                f"{_m3(dated['demand_to_date_m3'])} and dated supply is {_m3(dated['supply_to_date_m3'])}. "
                f"{line['reason']}"
            )


def _narrative(plant: str, product: str, supply: dict, lines: list[dict], policies: list[dict], expedites: list[dict], maintenance: list[str], windows: list[dict]) -> dict:
    optimised = next(row for row in policies if row["policy_code"] == "optimised")
    earliest = next(row for row in policies if row["policy_code"] == "earliest")
    internal = next(row for row in policies if row["policy_code"] == "internal")
    external = next(row for row in policies if row["policy_code"] == "external")
    practice = next(row for row in policies if row["policy_code"] == "practice")
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
                f"Earliness breaks a tie only inside the RM{OBJECTIVE_EPSILON_RM:.0f} band, after partially served orders have been minimised. Outside that band it does not take cubic metres from a higher expected consequence."
            )
    policy_text = (
        f"On the same capacity, the current-practice proxy (illustrative, not observed history) leaves expected consequence of {_rm(practice['expected_consequence_rm'])}. "
        f"An earliest-required-date rule leaves {_rm(earliest['expected_consequence_rm'])}. "
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


def _is_safer(row: dict) -> bool:
    penalty = float(row.get("contractual_penalty_rm") or row.get("contractual_penalty") or 0)
    return row.get("demand_type") == "External" and row.get("confidence_level") == "Confirmed" and penalty > 1


def _tie_notes(lines: list[dict]) -> list[str]:
    """Close calls between a served order and a different unserved order."""
    served = [line for line in lines if line["allocated_quantity"] > TOL]
    missed = [line for line in lines if line["unserved_quantity"] > TOL]
    notes = []
    seen: set[tuple[str, str]] = set()
    for left in missed:
        for right in served:
            if left["demand_code"] == right["demand_code"]:
                continue
            pair = tuple(sorted((left["demand_code"], right["demand_code"])))
            if pair in seen:
                continue
            gap = abs(float(left["unit_expected_rm"]) - float(right["unit_expected_rm"]))
            if gap > TIE_TOLERANCE_RM + 0.01:
                continue
            seen.add(pair)
            left_flag = _is_safer(left)
            right_flag = _is_safer(right)
            if left_flag and right_flag:
                notes.append(
                    f"{left['customer_or_project']} and {right['customer_or_project']} differ by {_rm(gap)} per m³, "
                    f"inside the {_rm(TIE_TOLERANCE_RM)} tie tolerance. Both are confirmed external orders with a contractual penalty, "
                    "so the tie-break does not choose between them. The linear programme keeps the higher unit expected consequence."
                )
            elif left_flag and not right_flag:
                notes.append(
                    f"{left['customer_or_project']} and {right['customer_or_project']} differ by {_rm(gap)} per m³, "
                    f"inside the {_rm(TIE_TOLERANCE_RM)} tie tolerance. {left['customer_or_project']} is a confirmed external order "
                    "with a contractual penalty, so the recommendation prefers it."
                )
            elif right_flag and not left_flag:
                notes.append(
                    f"{right['customer_or_project']} and {left['customer_or_project']} differ by {_rm(gap)} per m³, "
                    f"inside the {_rm(TIE_TOLERANCE_RM)} tie tolerance. The recommendation keeps "
                    f"{right['customer_or_project']} because it is a confirmed external order with a contractual penalty."
                )
    return notes


def _prefer_contractually_safer(
    raw_demands: list[dict],
    days: list[str],
    cap: dict[str, float],
    usable: float,
    plans: dict[str, dict[int, dict]],
) -> tuple[dict[str, dict[int, dict]], str]:
    """Swap only when a safer order is short and a less-safe order is served inside the tolerance."""
    parents = _prepare_demands(raw_demands)
    plan = plans["optimised"]
    served = []
    missed = []
    for parent in parents:
        slot = plan[int(parent["id"])]
        if slot["unserved"] > TOL:
            missed.append(parent)
        if slot["allocated"] > TOL:
            served.append(parent)
    chosen = None
    for safer in missed:
        if not _is_safer(safer):
            continue
        for other in served:
            if _is_safer(other):
                continue
            gap = abs(float(safer["unit_expected_rm"]) - float(other["unit_expected_rm"]))
            if gap <= TIE_TOLERANCE_RM + 0.01 and float(safer["unit_expected_rm"]) <= float(other["unit_expected_rm"]) + 0.01:
                chosen = (safer, other, gap)
                break
        if chosen:
            break
    if chosen is None:
        return plans, ""
    safer, other, gap = chosen
    bonus_amount = float(other["unit_expected_rm"]) - float(safer["unit_expected_rm"]) + 0.05
    tranches = _prepare_demands([row for demand in raw_demands for row in expand_tranches(demand)])
    bonus = {
        int(tranche["id"]): bonus_amount
        for tranche in tranches
        if int(tranche["parent_id"]) == int(safer["id"])
    }
    updated = _collapse_plan(parents, tranches, _solve_lp(tranches, days, cap, usable, unit_bonus=bonus))
    if updated[int(safer["id"])]["allocated"] <= plan[int(safer["id"])]["allocated"] + TOL:
        return plans, (
            f"{safer['customer_or_project']} and {other['customer_or_project']} differ by {_rm(gap)} per m³, "
            f"inside the {_rm(TIE_TOLERANCE_RM)} tie tolerance. {safer['customer_or_project']} is the contractually safer order, "
            f"but its required date cannot take the cubic metres already given to {other['customer_or_project']}."
        )
    replaced = dict(plans)
    replaced["optimised"] = updated
    return replaced, (
        f"{safer['customer_or_project']} and {other['customer_or_project']} differ by {_rm(gap)} per m³, "
        f"inside the {_rm(TIE_TOLERANCE_RM)} tie tolerance. {safer['customer_or_project']} is a confirmed external order "
        f"with a contractual penalty, so the recommendation serves it ahead of {other['customer_or_project']}. "
        f"The extra expected consequence of that choice is about {_rm(gap)} per m³."
    )


def _lump_groups(raw_demands: list[dict]) -> list[dict]:
    ranked = sorted(
        (row for row in raw_demands if float(row.get("contractual_penalty") or 0) > 1),
        key=lambda row: -float(row["contractual_penalty"]),
    )
    groups = []
    for demand in list(ranked)[:LUMP_SUM_TOP_N]:
        parent_id = int(demand["id"])
        pieces = expand_tranches(demand)
        factor = float(CONFIDENCE_FACTOR.get(str(demand.get("confidence_level") or "Confirmed"), 1.0))
        groups.append(
            {
                "parent_id": parent_id,
                "tranche_ids": [int(row["id"]) for row in pieces],
                "parent_quantity": float(demand["requested_quantity"]),
                "penalty_expected_rm": float(demand["contractual_penalty"]) * factor,
                "demand_code": demand["demand_code"],
                "customer_or_project": demand["customer_or_project"],
            }
        )
    return groups


def _plan_expected(plan: dict | None) -> float:
    if not plan:
        return 0.0
    return round_rm(sum(float(slot["impact_override"]["expected_consequence_rm"]) for slot in plan.values()))


def _allocation_rows(demands: list[dict], plan: dict | None) -> list[dict]:
    if not plan:
        return []
    rows = []
    for demand in demands:
        slot = plan[int(demand["id"])]
        rows.append(
            {
                "demand_id": int(demand["id"]),
                "demand_code": demand["demand_code"],
                "customer_or_project": demand["customer_or_project"],
                "demand_type": demand["demand_type"],
                "allocated_m3": slot["allocated"],
                "unserved_m3": slot["unserved"],
                "expected_consequence_rm": slot["impact_override"]["expected_consequence_rm"],
            }
        )
    return rows


def _linear_score(demands: list[dict], plan: dict) -> float:
    total = 0.0
    for demand in demands:
        total += score_parent_unserved(
            demand,
            plan[int(demand["id"])]["unserved"],
            penalty_as="per_m3",
            delay_as="proportional",
        )["expected_consequence_rm"]
    return round_rm(total)


def _regret_comparison(demands: list[dict], typed: dict, proportional: dict | None) -> dict:
    typed_true = _plan_expected(typed)
    if proportional is None:
        return {
            "typed_plan_true_rm": typed_true,
            "plans_differ": False,
            "note": "This bucket was solved on a shared ready-mix plan.",
        }
    prop_true = _plan_expected(proportional)
    differ = any(
        abs(float(typed[int(demand["id"])]["allocated"]) - float(proportional[int(demand["id"])]["allocated"])) > TOL
        for demand in demands
    )
    return {
        "typed_plan_true_rm": typed_true,
        "proportional_plan_true_rm": prop_true,
        "proportional_regret_under_true_terms_rm": round_rm(prop_true - typed_true),
        "typed_plan_if_scored_proportional_rm": _linear_score(demands, typed),
        "proportional_plan_if_scored_proportional_rm": _linear_score(demands, proportional),
        "plans_differ": differ,
        "typed_allocations": _allocation_rows(demands, typed),
        "proportional_allocations": _allocation_rows(demands, proportional),
        "note": (
            "The recommendation is the mixed-integer plan under each order's own penalty type and delay type. "
            "The proportional plan is a labelled comparison that treats every penalty and every delay as linear. "
            "Regret is that comparison's consequence under the true terms, minus the recommendation."
        ),
    }


def _party_slice(demands: list[dict], plan: dict, linear: bool) -> dict:
    buckets = {
        "Internal": {"unserved_m3": 0.0, "consequence_rm": 0.0},
        "External": {"unserved_m3": 0.0, "consequence_rm": 0.0},
    }
    for demand in demands:
        slot = plan[int(demand["id"])]
        kind = demand["demand_type"] if demand["demand_type"] in buckets else "External"
        buckets[kind]["unserved_m3"] += float(slot["unserved"])
        if linear:
            buckets[kind]["consequence_rm"] += score_parent_unserved(
                demand, slot["unserved"], penalty_as="per_m3", delay_as="proportional"
            )["expected_consequence_rm"]
        else:
            buckets[kind]["consequence_rm"] += float(slot["impact_override"]["expected_consequence_rm"])
    unserved = buckets["Internal"]["unserved_m3"] + buckets["External"]["unserved_m3"]
    consequence = buckets["Internal"]["consequence_rm"] + buckets["External"]["consequence_rm"]
    for side in buckets.values():
        side["unserved_m3"] = round_m3(side["unserved_m3"])
        side["consequence_rm"] = round_rm(side["consequence_rm"])
        side["unserved_share"] = round(side["unserved_m3"] / unserved, 4) if unserved > TOL else 0.0
        side["consequence_share"] = round(side["consequence_rm"] / consequence, 4) if consequence > 1 else 0.0
    return {"internal": buckets["Internal"], "external": buckets["External"]}


def _party_burden(demands: list[dict], typed: dict, proportional: dict | None) -> dict:
    return {
        "before": _party_slice(demands, proportional, True) if proportional else None,
        "after": _party_slice(demands, typed, False),
        "note": "Before is the all-proportional plan scored as linear. After is the recommendation scored on each order's own penalty and delay type.",
    }


def solve_bucket(
    world: dict,
    plant_id: int,
    product_id: int,
    plans: dict | None = None,
    *,
    include_comparison: bool = True,
    include_expedite: bool = True,
    lex: bool = True,
) -> dict:
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
    raw_demands = [
        row
        for row in world["demands"]
        if row["plant_id"] == plant_id and row["product_id"] == product_id and float(row["requested_quantity"]) > TOL
    ]
    demands = _prepare_demands(raw_demands)
    emergency = float(product["emergency_cost_per_m3"])
    assumption = bool(product["emergency_cost_is_assumption"])
    recommendation_basis = "typed_milp"
    minimax = None
    proportional_plan = None
    if plans is None:
        plans = _plans_for(raw_demands, days, cap, usable, lex=lex)
        if include_comparison:
            proportional_plans = _plans_for(raw_demands, days, cap, usable, force_penalty="per_m3", force_delay="proportional")
            proportional_plan = proportional_plans["optimised"]
        unverified = [row for row in raw_demands if penalty_unverified(row)]
        if unverified:
            recommendation_basis = "minimax_unverified"
            as_linear = {int(row["id"]): "per_m3" for row in unverified}
            as_lump = {int(row["id"]): "lump_sum" for row in unverified}
            parents, tranches = _expanded(raw_demands)
            linear_tranches = _solve_lp(tranches, days, cap, usable, penalty_overrides=as_linear)
            lump_tranches = _solve_lp(tranches, days, cap, usable, penalty_overrides=as_lump)
            scored = {
                "linear_plan_as_linear": _collapse_plan(parents, tranches, linear_tranches, penalty_as=as_linear),
                "linear_plan_as_lump": _collapse_plan(parents, tranches, linear_tranches, penalty_as=as_lump),
                "lump_plan_as_linear": _collapse_plan(parents, tranches, lump_tranches, penalty_as=as_linear),
                "lump_plan_as_lump": _collapse_plan(parents, tranches, lump_tranches, penalty_as=as_lump),
            }
            cells = {key: _plan_expected(value) for key, value in scored.items()}
            best_linear = min(cells["linear_plan_as_linear"], cells["lump_plan_as_linear"])
            best_lump = min(cells["linear_plan_as_lump"], cells["lump_plan_as_lump"])
            regret_linear = max(cells["linear_plan_as_linear"] - best_linear, cells["linear_plan_as_lump"] - best_lump)
            regret_lump = max(cells["lump_plan_as_linear"] - best_linear, cells["lump_plan_as_lump"] - best_lump)
            choose_lump = regret_lump < regret_linear - 0.01
            plans["optimised"] = scored["lump_plan_as_lump"] if choose_lump else scored["linear_plan_as_linear"]
            minimax = {
                "orders": [row["customer_or_project"] for row in unverified],
                "cells_rm": cells,
                "max_regret_linear_plan_rm": round_rm(regret_linear),
                "max_regret_lump_plan_rm": round_rm(regret_lump),
                "chosen": "lump_sum" if choose_lump else "per_m3",
                "note": "These orders are flagged penalty type unverified. The recommendation is the plan with the lower maximum regret across the two readings of that penalty.",
            }
    optimised_plan = plans["optimised"]
    policies = [
        _policy_block("optimised", "Minimise business consequence", "Mixed-integer programme (CBC) under each order's penalty and delay type", demands, plans["optimised"], emergency, assumption),
        _policy_block("internal", "Internal projects first", "Priority rule on the same capacity", demands, plans["internal"], emergency, assumption),
        _policy_block("external", "External customers first", "Priority rule on the same capacity", demands, plans["external"], emergency, assumption),
        _policy_block("earliest", "Earliest required date", "Priority rule on the same capacity. Second comparison.", demands, plans["earliest"], emergency, assumption),
        _policy_block(
            "penalty_delay",
            "Penalty and delay per m³",
            "Greedy rank by average penalty plus delay per cubic metre. Dates limit production.",
            demands,
            plans["penalty_delay"],
            emergency,
            assumption,
        ),
        _policy_block(
            "complete_or_skip",
            "Complete or skip lump-sum orders",
            "A lump-sum order is served only when its full confirmed quantity fits. Otherwise it is skipped.",
            demands,
            plans["complete_or_skip"],
            emergency,
            assumption,
        ),
        _policy_block(
            "unit_expected",
            "Greedy unit expected consequence",
            "Rank by unit expected consequence. No lump split and no confirmed-tranche preference.",
            demands,
            plans["unit_expected"],
            emergency,
            assumption,
        ),
        _policy_block(
            "practice",
            "Current practice proxy",
            "Illustrative rule on the same capacity. Not observed history. Firm orders, then due date, then margin and penalty. Programme delay is ignored.",
            demands,
            plans["practice"],
            emergency,
            assumption,
        ),
    ]
    ranked = sorted(demands, key=lambda row: (-row["unit_expected_rm"], row["required_date"], row["id"]))
    rank = {int(row["id"]): index for index, row in enumerate(ranked, start=1)}
    lines = [_line_view(row, optimised_plan[int(row["id"])], rank[int(row["id"])], emergency, assumption) for row in demands]
    windows = _windows(demands, days, cap, usable)
    _reasons(lines, windows)
    lines.sort(key=lambda row: (row["rank_by_expected_consequence"]))
    by_demand = {int(row["id"]): row for row in demands}
    for line in lines:
        parent = by_demand[int(line["demand_id"])]
        full = score_parent_unserved(parent, float(parent["requested_quantity"]))
        current = score_parent_unserved(parent, float(line["unserved_quantity"]))
        full_steps = full["penalty_at_risk_rm"] + full["delay_cost_incurred_rm"]
        current_steps = current["penalty_at_risk_rm"] + current["delay_cost_incurred_rm"]
        partial = line["allocated_quantity"] > TOL and line["unserved_quantity"] > TOL
        line["partial_service_no_penalty_avoided"] = bool(partial and abs(full_steps - current_steps) < 1.0)
        minimum = float(parent.get("minimum_useful_delivery_m3") or 0.0)
        line["minimum_useful_delivery_m3"] = round_m3(minimum)
        line["below_minimum_useful_delivery"] = bool(minimum > TOL and TOL < line["allocated_quantity"] < minimum - TOL)
        if line["partial_service_no_penalty_avoided"]:
            line["reason"] = (
                f"{line['reason']} Partial service, no penalty avoided: the cubic metres served leave the penalty and delay unchanged versus missing the order. They earn margin only."
            ).strip()
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
    expedites.sort(key=lambda row: (-float(row.get("avoided_per_rm") or 0), row["customer_or_project"]))
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
    explanation = _narrative(plant["name"], product["name"], supply, lines, policies, expedites, maintenance, windows)
    tie = (
        f"Inside {_rm(OBJECTIVE_EPSILON_RM)} of the best expected consequence, the solver minimises the number of partially served orders, "
        f"then leaves the later order unserved. Earliness is that last tie-break. It does not override a gap larger than {_rm(OBJECTIVE_EPSILON_RM)}."
    )
    explanation["tie_break"] = tie
    explanation["tradeoff"] = f"{explanation['tradeoff']} {tie}".strip()
    explanation["why"] = f"{explanation['why']} {tie}".strip()
    comparison = _regret_comparison(demands, optimised_plan, proportional_plan)
    party = _party_burden(demands, optimised_plan, proportional_plan)
    recommended_true = totals["expected_consequence_rm"]
    rule_rows = [row for row in policies if row["policy_code"] in SIMPLE_RULES]
    best_rule = min(rule_rows, key=lambda row: (row["expected_consequence_rm"], SIMPLE_RULES.index(row["policy_code"])))
    earliest_linear = _linear_score(demands, plans["earliest"])
    recommended_linear = comparison.get("typed_plan_if_scored_proportional_rm", recommended_true)
    raw_gap = best_rule["expected_consequence_rm"] - recommended_true
    # The lexicographic tie-break may leave the recommendation up to the epsilon
    # above a feasible simple rule. Inside that band the reported gap is zero.
    gap_true = round_rm(max(0.0, raw_gap) if raw_gap >= -OBJECTIVE_EPSILON_RM - 0.05 else raw_gap)
    gap_linear = round_rm(earliest_linear - recommended_linear)
    headline = {
        "true_rm": gap_true,
        "best_rule": best_rule["policy_code"],
        "best_rule_label": best_rule["policy"],
        "rules": [
            {
                "policy_code": row["policy_code"],
                "label": row["policy"],
                "expected_consequence_rm": row["expected_consequence_rm"],
                "gap_rm": round_rm(row["expected_consequence_rm"] - recommended_true),
            }
            for row in rule_rows
        ],
        "if_scored_proportional_rm": gap_linear,
        "effect_rm": round_rm(gap_true - gap_linear),
        "note": (
            "The headline gap is the lowest simple-rule consequence minus the recommendation, both scored on each order's own penalty and delay type. "
            f"On this plant and product the best simple rule is {best_rule['policy']}. "
            "The proportional figure scores the earliest-date allocation and the recommendation as if every term were linear."
        ),
    }
    if include_expedite:
        parents, tranches = _expanded(raw_demands)
        emergency_tranches = _solve_lp(
            tranches,
            days,
            cap,
            usable,
            lex=False,
            emergency_cost=emergency,
            emergency_share=EMERGENCY_CAPACITY_SHARE,
        )
        emergency_meta = dict(getattr(_solve_lp, "last_meta", {}))
        emergency_plan = _collapse_plan(parents, tranches, emergency_tranches)
        emergency_expected = round_rm(sum(float(slot["impact_override"]["expected_consequence_rm"]) for slot in emergency_plan.values()))
        emergency_m3 = float(emergency_meta.get("emergency_m3") or 0.0)
        emergency_cost_rm = round_rm(emergency_m3 * emergency)
        emergency_avoided = round_rm(recommended_true - emergency_expected)
        emergency_changes = []
        for demand in demands:
            demand_id = int(demand["id"])
            before = float(optimised_plan[demand_id]["allocated"])
            after = float(emergency_plan[demand_id]["allocated"])
            if abs(before - after) > TOL:
                emergency_changes.append(
                    {
                        "customer_or_project": demand["customer_or_project"],
                        "allocated_before_m3": round_m3(before),
                        "allocated_after_m3": round_m3(after),
                    }
                )
    else:
        emergency_m3 = 0.0
        emergency_cost_rm = 0.0
        emergency_avoided = 0.0
        emergency_changes = []
    change_text = ""
    if emergency_changes:
        bits = [
            f"{row['customer_or_project']} moves from {_m3(row['allocated_before_m3'])} to {_m3(row['allocated_after_m3'])}"
            for row in emergency_changes
        ]
        change_text = " With the extra supply the allocation changes: " + "; ".join(bits) + "."
    expedite_proposal = {
        "extra_m3": round_m3(emergency_m3),
        "cost_rm": emergency_cost_rm,
        "avoids_rm": emergency_avoided,
        "net_benefit_rm": round_rm(emergency_avoided - emergency_cost_rm),
        "recommended": emergency_m3 > TOL and emergency_avoided > emergency_cost_rm + 0.01,
        "needs_approval": True,
        "bound_share": EMERGENCY_CAPACITY_SHARE,
        "changes": emergency_changes,
        "note": (
            f"Optional emergency supply, capped at {EMERGENCY_CAPACITY_SHARE:.0%} of each day's available capacity, "
            "priced at the assumed emergency cost. It is not in the base plan. A person approves it."
            + change_text
        ),
    }
    solver_meta = dict(getattr(_plans_for, "base_meta", {"status": "Optimal", "time_limit_seconds": SOLVER_TIME_LIMIT_SECONDS}))
    return {
        "plant_id": plant_id,
        "plant_name": plant["name"],
        "product_id": product_id,
        "product_name": product["name"],
        "product_code": product["code"],
        "stockable": product_is_stockable(product),
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
        "explanation": explanation,
        "objective": "Minimise expected consequence under each order's penalty type and delay type, subject to dated plant capacity, usable inventory, product compatibility, and required dates.",
        "solver": "CBC mixed-integer programme",
        "solver_status": solver_meta.get("status", "Optimal"),
        "solver_time_limit_seconds": solver_meta.get("time_limit_seconds", SOLVER_TIME_LIMIT_SECONDS),
        "objective_epsilon_rm": OBJECTIVE_EPSILON_RM,
        "expedite_proposal": expedite_proposal,
        "recommendation_basis": recommendation_basis,
        "plant_mode": _plant_mode(plant_id),
        "open_decision": _open_decision(plant_id, product_id),
        "comparison": comparison,
        "unverified_minimax": minimax,
        "party_burden": party,
        "headline_gap": headline,
        "decision_review": review_case(
            totals["programme_days"],
            totals["expected_consequence_rm"],
            totals["penalty_at_risk_rm"],
            any(line["demand_type"] == "Internal" and line["programme_days"] > 0.05 for line in lines),
            any(line["demand_type"] == "External" and line["penalty_at_risk_rm"] > 1 for line in lines),
        ),
        "inventory_projection": {
            "opening_on_hand_m3": round_m3(inventory["on_hand"]),
            "safety_stock_m3": round_m3(inventory["safety_stock"]),
            "drawn_from_inventory_m3": inventory_used,
            "produced_for_allocation_m3": round_m3(sum(slot["from_production"] for slot in optimised_plan.values())),
            "projected_closing_on_hand_m3": round_m3(max(0.0, float(inventory["on_hand"]) - inventory_used)),
            "basis": (
                "Ready-mix cannot be stocked. On-hand, safety stock, and usable inventory are zero."
                if not product_is_stockable(product)
                else (
                    "Projected, not observed. This model makes product only for the allocation, so production does not "
                    "increase stock. Closing on-hand is opening on-hand minus inventory drawn. There is no delivery ledger."
                )
            ),
        },
    }


def _unserved_codes(bucket: dict) -> set[str]:
    return {line["demand_code"] for line in bucket["allocations"] if line["unserved_quantity"] > TOL}


def _order_names(bucket: dict, codes: set[str] | list[str]) -> str:
    lookup = {line["demand_code"]: line["customer_or_project"] for line in bucket["allocations"]}
    labels = [lookup.get(code, code) for code in sorted(codes)]
    return ", ".join(labels)


def _score_fixed(demands: list[dict], unserved_by_id: dict[int, float]) -> float:
    total = 0.0
    for demand in demands:
        total += score_parent_unserved(demand, float(unserved_by_id.get(int(demand["id"]), demand["requested_quantity"])))[
            "expected_consequence_rm"
        ]
    return round_rm(total)


def assess_fragility(plant_id: int, product_id: int) -> dict:
    """Fragile when the base allocation's regret under a 10% or 20% shock exceeds the bar."""
    world = load_world()
    base = solve_bucket(world, plant_id, product_id, include_comparison=False, include_expedite=False)
    base_set = _unserved_codes(base)
    base_unserved = {int(line["demand_id"]): float(line["unserved_quantity"]) for line in base["allocations"]}
    raw = [row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    checks = []
    if base["constrained"]:
        for demand in raw:
            for factor in SENSITIVITY_FACTORS:
                scenario = {
                    "name": "Sensitivity",
                    "demand_adjustments": [
                        {"demand_id": int(demand["id"]), "unit_expected_factor": factor},
                    ],
                }
                shocked = apply_scenario(world, scenario)
                alt = solve_bucket(shocked, plant_id, product_id, include_comparison=False, include_expedite=False)
                alt_set = _unserved_codes(alt)
                shocked_demands = [row for row in shocked["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
                base_cost = _score_fixed(shocked_demands, base_unserved)
                optimum = float(alt["expected_consequence_rm"])
                regret = round_rm(base_cost - optimum)
                threshold = max(FRAGILITY_REGRET_RM, FRAGILITY_REGRET_SHARE * max(optimum, 0.0))
                checks.append(
                    {
                        "demand_code": demand["demand_code"],
                        "customer_or_project": demand["customer_or_project"],
                        "factor": factor,
                        "change_pct": int(round((factor - 1) * 100)),
                        "unserved_set_changed": alt_set != base_set,
                        "unserved_after": sorted(alt_set),
                        "regret_rm": regret,
                        "threshold_rm": round_rm(threshold),
                        "exceeds": regret > threshold + 0.01,
                    }
                )
    flips = [row for row in checks if row["exceeds"]]
    summary, flip_point = _fragility_text(base, flips, checks)
    share = (len(flips) / len(checks)) if checks else 0.0
    return {
        "plant_id": plant_id,
        "product_id": product_id,
        "plant_name": base["plant_name"],
        "product_name": base["product_name"],
        "fragile": bool(flips),
        "flip_point": flip_point,
        "summary": summary,
        "shock_count": len(checks),
        "flip_count": len(flips),
        "flip_share": round(share, 4),
        "threshold_rm": FRAGILITY_REGRET_RM,
        "threshold_share": FRAGILITY_REGRET_SHARE,
        "base_unserved": sorted(base_set),
        "checks": checks,
    }


def _fragility_text(bucket: dict, flips: list[dict], checks: list[dict]) -> tuple[str, str]:
    if not bucket["constrained"]:
        text = "This plant and product can be fully served, so there is no shortfall a 10% or 20% assumption can reprice."
        return text, ""
    share = (len(flips) / len(checks)) if checks else 0.0
    if not flips:
        text = (
            f"Not fragile on this grid. {len(checks)} shocks moved one order's unit expected consequence by 10% or 20% "
            "and the plant-product was solved again. Keeping the base allocation never cost more than "
            f"RM{FRAGILITY_REGRET_RM:,.0f} or {FRAGILITY_REGRET_SHARE:.0%} of the shocked optimum, whichever is larger. "
            f"Share of shocks over that bar: {share:.0%}."
        )
        return text, ""
    closest = min(flips, key=lambda row: (abs(int(row["change_pct"])), -float(row["regret_rm"]), row["customer_or_project"]))
    sign = "+" if int(closest["change_pct"]) > 0 else "-"
    pct = abs(int(closest["change_pct"]))
    flip_point = (
        f"Fragile. {len(flips)} of {len(checks)} shocks ({share:.0%}) put the base plan more than "
        f"RM{FRAGILITY_REGRET_RM:,.0f} or {FRAGILITY_REGRET_SHARE:.0%} above the re-solved optimum. "
        f"The largest small step is {closest['customer_or_project']} at {sign}{pct}%, "
        f"regret {_rm(closest['regret_rm'])}."
    )
    same_step = [
        row
        for row in flips
        if row["demand_code"] != closest["demand_code"] and abs(int(row["change_pct"])) == pct
    ]
    extra = ""
    if same_step:
        bits = []
        for row in same_step:
            row_sign = "+" if int(row["change_pct"]) > 0 else "-"
            bits.append(f"{row['customer_or_project']} at {row_sign}{abs(int(row['change_pct']))}%")
        extra = " The same grid step also flips " + " and ".join(bits) + "."
    summary = (
        flip_point
        + extra
        + " Unit expected consequence means margin, contractual penalty, and delay cost scaled together, then solved again."
    )
    return summary, flip_point


def _lump_penalty_score(bucket: dict, groups: list[dict]) -> float:
    by_code = {group["demand_code"]: group for group in groups}
    total = 0.0
    for line in bucket["allocations"]:
        group = by_code.get(line["demand_code"])
        if group and line["unserved_quantity"] > TOL:
            proportional_penalty = float(line["penalty_at_risk_rm"]) * float(line["confidence_factor"])
            total += float(line["expected_consequence_rm"]) - proportional_penalty + float(group["penalty_expected_rm"])
        else:
            total += float(line["expected_consequence_rm"])
    return round_rm(total)


def compare_lump_sum(plant_id: int, product_id: int) -> dict:
    """Solve the same plant-product with proportional penalties and with a lump-sum penalty on the top N."""
    world = load_world()
    raw = [row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    groups = _lump_groups(raw)
    linear = solve_bucket(world, plant_id, product_id)
    lump_world = deepcopy(world)
    lump_world["lump_sum_penalties"] = True
    lump = solve_bucket(lump_world, plant_id, product_id)
    linear_alloc = {line["demand_code"]: line for line in linear["allocations"]}
    lump_alloc = {line["demand_code"]: line for line in lump["allocations"]}
    differences = []
    for code, line in linear_alloc.items():
        other = lump_alloc.get(code)
        if other is None:
            continue
        if abs(float(line["allocated_quantity"]) - float(other["allocated_quantity"])) > TOL:
            differences.append(
                {
                    "demand_code": code,
                    "customer_or_project": line["customer_or_project"],
                    "linear_allocated_m3": line["allocated_quantity"],
                    "lump_allocated_m3": other["allocated_quantity"],
                    "linear_unserved_m3": line["unserved_quantity"],
                    "lump_unserved_m3": other["unserved_quantity"],
                }
            )
    return {
        "plant_id": plant_id,
        "product_id": product_id,
        "plant_name": linear["plant_name"],
        "product_name": linear["product_name"],
        "top_n": LUMP_SUM_TOP_N,
        "orders": [
            {
                "demand_code": group["demand_code"],
                "customer_or_project": group["customer_or_project"],
                "penalty_expected_rm": round_rm(group["penalty_expected_rm"]),
            }
            for group in groups
        ],
        "linear_unserved": sorted(_unserved_codes(linear)),
        "lump_unserved": sorted(_unserved_codes(lump)),
        "linear_expected_rm": linear["expected_consequence_rm"],
        "lump_scored_with_linear_rm": lump["expected_consequence_rm"],
        "lump_penalty_consequence_rm": _lump_penalty_score(lump, groups),
        "allocation_changed": bool(differences),
        "differences": differences,
        "note": (
            f"The recommendation on this page stays the proportional penalty. "
            f"Lump-sum mode is optional. It puts a yes/no variable on the {LUMP_SUM_TOP_N} largest contractual penalties "
            "and charges the full penalty once any of that order is missed. "
            "The linear figure scores both allocations with the proportional penalty. "
            "The lump-sum figure charges the full penalty on those orders when they are short."
        ),
    }


def _bundle(world: dict, plant_id: int, product_id: int) -> dict:
    raw = [row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    days = sorted(
        row["prod_date"] for row in world["calendar"] if row["plant_id"] == plant_id and row["product_id"] == product_id
    )
    cap = {
        row["prod_date"]: float(row["available_capacity"])
        for row in world["calendar"]
        if row["plant_id"] == plant_id and row["product_id"] == product_id
    }
    inventory = next(row for row in world["inventory"] if row["plant_id"] == plant_id and row["product_id"] == product_id)
    return {
        "product_id": product_id,
        "parents": _prepare_demands(raw),
        "tranches": _prepare_demands([piece for demand in raw for piece in expand_tranches(demand)]),
        "days": days,
        "cap": cap,
        "usable": float(inventory["usable"]),
    }


def _solve_bundles(bundles: list[dict], shared: bool) -> dict[int, dict]:
    """One linear programme. When shared is true, ready-mix grades share a plant-day cap."""
    demands = [demand for bundle in bundles for demand in bundle["tranches"]]
    if not demands:
        return {}
    problem = pulp.LpProblem("capacity_allocation_shared", pulp.LpMinimize)
    production: dict[tuple[int, str], pulp.LpVariable] = {}
    inventory_use: dict[int, pulp.LpVariable] = {}
    unserved: dict[int, pulp.LpVariable] = {}
    for bundle in bundles:
        for demand in bundle["tranches"]:
            demand_id = int(demand["id"])
            inventory_use[demand_id] = pulp.LpVariable(f"sinv_{demand_id}", lowBound=0)
            unserved[demand_id] = pulp.LpVariable(f"suns_{demand_id}", lowBound=0, upBound=demand["quantity"])
            for day in bundle["days"]:
                if day <= demand["required_date"]:
                    production[demand_id, day] = pulp.LpVariable(f"sp_{demand_id}_{day.replace('-', '')}", lowBound=0)
    problem += pulp.lpSum(demand["unit_expected_rm"] * unserved[int(demand["id"])] for demand in demands) + (
        1e-4 * pulp.lpSum(production.values())
    )
    for demand in demands:
        demand_id = int(demand["id"])
        made = pulp.lpSum(var for (owner, _day), var in production.items() if owner == demand_id)
        problem += made + inventory_use[demand_id] + unserved[demand_id] == demand["quantity"], f"sbalance_{demand_id}"
    for bundle in bundles:
        owners = {int(demand["id"]) for demand in bundle["tranches"]}
        for day, limit in bundle["cap"].items():
            users = [production[owner, day] for owner in owners if (owner, day) in production]
            if users:
                problem += pulp.lpSum(users) <= limit, f"cap_{bundle['product_id']}_{day.replace('-', '')}"
        if owners:
            problem += pulp.lpSum(inventory_use[owner] for owner in owners) <= bundle["usable"], f"inventory_{bundle['product_id']}"
    if shared:
        days = sorted({day for bundle in bundles for day in bundle["days"]})
        for day in days:
            users = []
            limits = []
            for bundle in bundles:
                if day in bundle["cap"]:
                    limits.append(bundle["cap"][day])
                owners = {int(demand["id"]) for demand in bundle["tranches"]}
                users.extend(production[owner, day] for owner in owners if (owner, day) in production)
            if users and limits:
                problem += pulp.lpSum(users) <= max(limits), f"shared_{day.replace('-', '')}"
    status = problem.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Allocation solver returned {pulp.LpStatus[status]}.")
    plan: dict[int, dict] = {}
    for demand in demands:
        demand_id = int(demand["id"])
        qty = float(demand["quantity"])
        inv = float(pulp.value(inventory_use[demand_id]) or 0.0)
        made = sum(float(pulp.value(var) or 0.0) for (owner, _day), var in production.items() if owner == demand_id)
        allocated = _snap(inv + made, qty)
        inv = min(inv, allocated)
        made = max(0.0, allocated - inv)
        by_day = []
        for (owner, day), var in production.items():
            if owner != demand_id:
                continue
            amount = float(pulp.value(var) or 0.0)
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


def _greedy_bundles(bundles: list[dict], policy: str, shared: bool) -> dict[int, dict]:
    remaining_cap = {(bundle["product_id"], day): qty for bundle in bundles for day, qty in bundle["cap"].items()}
    remaining_inv = {bundle["product_id"]: bundle["usable"] for bundle in bundles}
    days = sorted({day for bundle in bundles for day in bundle["days"]})
    remaining_shared = {day: max(bundle["cap"].get(day, 0.0) for bundle in bundles) for day in days}
    owner = {int(demand["id"]): bundle["product_id"] for bundle in bundles for demand in bundle["tranches"]}
    demands = [demand for bundle in bundles for demand in bundle["tranches"]]
    plan: dict[int, dict] = {}
    for demand in sorted(demands, key=lambda row: _policy_sort(row, policy)):
        product_id = owner[int(demand["id"])]
        need = float(demand["quantity"])
        from_inv = min(need, remaining_inv[product_id])
        remaining_inv[product_id] -= from_inv
        need -= from_inv
        from_prod = 0.0
        by_day = []
        for day in reversed(days):
            if day > demand["required_date"] or need <= TOL:
                continue
            product_left = remaining_cap.get((product_id, day), 0.0)
            shared_left = remaining_shared[day] if shared else product_left
            take = min(need, product_left, shared_left)
            if take <= 0:
                continue
            remaining_cap[product_id, day] = product_left - take
            if shared:
                remaining_shared[day] -= take
            need -= take
            from_prod += take
            by_day.append({"date": day, "quantity": round_m3(take)})
        qty = float(demand["quantity"])
        allocated = _snap(qty - max(need, 0.0), qty)
        plan[int(demand["id"])] = {
            "allocated": allocated,
            "unserved": round_m3(qty - allocated),
            "from_inventory": round_m3(from_inv),
            "from_production": round_m3(from_prod),
            "by_day": by_day,
        }
    return plan


def _shared_plans(world: dict, plant_id: int, product_ids: list[int]) -> dict[int, dict]:
    bundles = [_bundle(world, plant_id, product_id) for product_id in product_ids]
    plans = {product_id: {} for product_id in product_ids}
    optimised = _solve_bundles(bundles, shared=True)
    for bundle in bundles:
        plans[bundle["product_id"]]["optimised"] = _collapse_plan(bundle["parents"], bundle["tranches"], optimised)
    for policy in ("internal", "external", "earliest", "practice"):
        filled = _greedy_bundles(bundles, policy, shared=True)
        for bundle in bundles:
            plans[bundle["product_id"]][policy] = _collapse_plan(bundle["parents"], bundle["tranches"], filled)
    for bundle in bundles:
        for policy in ("penalty_delay", "complete_or_skip", "unit_expected"):
            plans[bundle["product_id"]][policy] = _score_parent_plan(
                bundle["parents"],
                _parent_greedy(bundle["parents"], bundle["days"], bundle["cap"], bundle["usable"], policy),
            )
    return plans


def allocate(scenario: dict | None = None, *, include_comparison: bool = True, include_expedite: bool = True, lex: bool = True) -> dict:
    return allocate_world(
        apply_scenario(load_world(), scenario),
        include_comparison=include_comparison,
        include_expedite=include_expedite,
        lex=lex,
    )


def allocate_world(world: dict, *, include_comparison: bool = True, include_expedite: bool = True, lex: bool = True) -> dict:
    world["shared_ready_mix_batching"] = bool(world.get("shared_ready_mix_batching", SHARED_READY_MIX_BATCHING))
    buckets = []
    for plant in world["plants"]:
        ready = [product for product in world["products"] if product["code"] in READY_MIX_CODES]
        others = [product for product in world["products"] if product["code"] not in READY_MIX_CODES]
        if world["shared_ready_mix_batching"] and len(ready) > 1:
            shared = _shared_plans(world, plant["id"], [product["id"] for product in ready])
            for product in ready:
                buckets.append(
                    solve_bucket(
                        world,
                        plant["id"],
                        product["id"],
                        shared[product["id"]],
                        include_comparison=include_comparison,
                        include_expedite=include_expedite,
                        lex=lex,
                    )
                )
        else:
            for product in ready:
                buckets.append(
                    solve_bucket(
                        world,
                        plant["id"],
                        product["id"],
                        include_comparison=include_comparison,
                        include_expedite=include_expedite,
                        lex=lex,
                    )
                )
        for product in others:
            buckets.append(
                solve_bucket(
                    world,
                    plant["id"],
                    product["id"],
                    include_comparison=include_comparison,
                    include_expedite=include_expedite,
                    lex=lex,
                )
            )
    buckets.sort(key=lambda row: (-row["shortfall_m3"], row["plant_name"], row["product_name"]))

    def add(key: str) -> float:
        return round_rm(sum(bucket[key] for bucket in buckets)) if key.endswith("_rm") or key.endswith("days") else round_m3(sum(bucket[key] for bucket in buckets))

    optimised_expected = sum(bucket["expected_consequence_rm"] for bucket in buckets)
    earliest_expected = sum(next(p["expected_consequence_rm"] for p in bucket["policies"] if p["policy_code"] == "earliest") for bucket in buckets)
    internal_expected = sum(next(p["expected_consequence_rm"] for p in bucket["policies"] if p["policy_code"] == "internal") for bucket in buckets)
    external_expected = sum(next(p["expected_consequence_rm"] for p in bucket["policies"] if p["policy_code"] == "external") for bucket in buckets)
    practice_expected = sum(next(p["expected_consequence_rm"] for p in bucket["policies"] if p["policy_code"] == "practice") for bucket in buckets)
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
        "value_protected_vs_practice_rm": round_rm(practice_expected - optimised_expected),
        "value_protected_vs_earliest_rm": round_rm(earliest_expected - optimised_expected),
        "value_protected_vs_best_rule_rm": round_rm(sum(bucket["headline_gap"]["true_rm"] for bucket in buckets)),
        "best_rule_by_bucket": [
            {
                "plant_name": bucket["plant_name"],
                "product_name": bucket["product_name"],
                "best_rule": bucket["headline_gap"].get("best_rule"),
                "best_rule_label": bucket["headline_gap"].get("best_rule_label"),
                "gap_rm": bucket["headline_gap"]["true_rm"],
            }
            for bucket in buckets
            if bucket["constrained"]
        ],
        "headline_gap_if_scored_proportional_rm": round_rm(sum(bucket["headline_gap"]["if_scored_proportional_rm"] for bucket in buckets)),
        "headline_gap_effect_rm": round_rm(sum(bucket["headline_gap"]["effect_rm"] for bucket in buckets)),
        "headline_gap_note": buckets[0]["headline_gap"]["note"] if buckets else "",
        "value_protected_vs_internal_first_rm": round_rm(internal_expected - optimised_expected),
        "value_protected_vs_external_first_rm": round_rm(external_expected - optimised_expected),
        "earliest_expected_consequence_rm": round_rm(earliest_expected),
        "internal_first_expected_consequence_rm": round_rm(internal_expected),
        "external_first_expected_consequence_rm": round_rm(external_expected),
        "practice_expected_consequence_rm": round_rm(practice_expected),
        "constrained_buckets": sum(1 for bucket in buckets if bucket["constrained"]),
    }
    return {
        "scenario_name": world.get("scenario_name") or "Baseline",
        "scenario_notes": world.get("scenario_notes") or [],
        "horizon": {"start": HORIZON_START, "end": HORIZON_END},
        "solver": "CBC mixed-integer programme",
        "objective": buckets[0]["objective"] if buckets else "",
        "assumptions": ASSUMPTIONS,
        "model": {
            "decision_variables": [
                "For each order: cubic metres taken from usable inventory.",
                "For each order and each day on or before its required date: cubic metres produced that day.",
                "For each order: cubic metres left unserved.",
            ],
            "objective": "Minimise the sum of (expected RM per unserved m³ × unserved m³), plus a 0.0001 weight on production so inventory is used before the line when the ringgit result is the same.",
            "constraints": [
                "Production on allowed days + inventory used + unserved = requested quantity.",
                "Production on a day cannot exceed that day's available capacity.",
                "Inventory used cannot exceed on-hand minus safety stock.",
                "No production variable exists after the required date, or on another plant or product.",
            ],
            "solver": "CBC mixed-integer programme. One typed solve per plant and product, plus a labelled all-proportional comparison.",
            "not_in_the_objective": [
                "Emergency RM per m³ is an expedite screen, not extra base capacity.",
                "Criticality is a label. It changes the solve only when a scenario rescales delay cost.",
                "Internal versus external is not a weight. Those labels are comparison rules only.",
            ],
            "forecast_limit": "The October order book and the 12-month synthetic history are separate. The forecast is a planning signal. It is not a confirmed order and it is not a solver input. Confirmed, Probable, and Forecast on an order are planning-certainty weights, not calibrated probabilities.",
        },
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
    buffer = _buffer_days()
    for day in days:
        cumulative_capacity += cap[day]
        must = sum(
            float(targets.get(int(row["id"]), 0.0))
            for row in demands
            if _schedule_deadline(str(row["required_date"]), penalty_type_of(row), buffer) <= day
        )
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
        unserved = demand["quantity"] - allocated
        impact = score_parent_unserved(demand, unserved)
        slot = {
            "allocated": allocated,
            "unserved": unserved,
            "from_inventory": 0,
            "from_production": allocated,
            "by_day": [],
            "impact_override": impact,
        }
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
                "committed_production_m3": round_m3(float(row.get("committed_production") or 0.0)),
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
            "stockable": product_is_stockable(product),
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
            **(
                {}
                if not product_is_stockable(product)
                else {"usable_inventory": "max(0, on-hand − safety stock)"}
            ),
            "available_supply": "Dated capacity only. Ready-mix cannot be stocked." if not product_is_stockable(product) else "Available capacity + usable inventory",
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


def _retarget_types(world: dict, mode: str) -> dict:
    world = deepcopy(world)
    for demand in world["demands"]:
        if mode == "linear":
            demand["penalty_type"] = "per_m3"
            demand["delay_type"] = "proportional"
        elif mode == "lump":
            if float(demand.get("contractual_penalty") or 0) > 0:
                demand["penalty_type"] = "lump_sum"
            delay_pool = float(demand.get("delay_days_if_unserved") or 0) * float(demand.get("delay_cost_per_day") or 0)
            if demand.get("demand_type") == "Internal" and delay_pool > 0:
                demand["delay_type"] = "lump_days"
    return world


def value_protected_band(base: dict | None = None) -> dict:
    """Headline gap under all-linear, the seeded mix, and all-lump contract types."""
    if base is None:
        base = allocate(None, include_comparison=False, include_expedite=False)
    seeded = float(base["totals"]["value_protected_vs_best_rule_rm"])
    linear = allocate_world(_retarget_types(load_world(), "linear"), include_comparison=False, include_expedite=False)
    lump = allocate_world(_retarget_types(load_world(), "lump"), include_comparison=False, include_expedite=False)
    linear_gap = float(linear["totals"]["value_protected_vs_best_rule_rm"])
    lump_gap = float(lump["totals"]["value_protected_vs_best_rule_rm"])
    cases = [
        {"label": "all_linear", "value_protected_rm": round_rm(linear_gap)},
        {"label": "seeded", "value_protected_rm": round_rm(seeded)},
        {"label": "all_lump", "value_protected_rm": round_rm(lump_gap)},
    ]
    return {
        "headline_basis": "best_simple_rule",
        "all_linear_rm": round_rm(linear_gap),
        "seeded_rm": round_rm(seeded),
        "all_lump_rm": round_rm(lump_gap),
        "low_rm": round_rm(min(linear_gap, seeded, lump_gap)),
        "base_rm": round_rm(seeded),
        "high_rm": round_rm(max(linear_gap, seeded, lump_gap)),
        "cases": cases,
        "practice_proxy_gap_rm": base["totals"]["value_protected_vs_practice_rm"],
        "practice_proxy_note": (
            "Footnote. The proxy ignores programme delay when it ranks orders, "
            "then the consequence still includes that delay. It is not current practice and not observed savings."
        ),
        "earliest_gap_rm": base["totals"]["value_protected_vs_earliest_rm"],
        "gap_nonnegative_note": (
            "The recommendation minimises the same objective the gap is scored on, so the gap is at least zero in every contract world. "
            "The magnitude is meaningful only if the seeded inputs are."
        ),
        "formula": (
            "Modelled gap = expected consequence of the best simple rule minus the recommended allocation. "
            "The simple rules are internal-first, external-first, earliest required date, penalty and delay per cubic metre, "
            "complete-or-skip for lump-sum orders, and a greedy rank by unit expected consequence. "
            "Each rule places production on the latest feasible day. Each plant-product uses whichever scores lowest. "
            "The range re-solves that gap with every term linear, with the seeded mix, and with every positive term as a lump. "
            "This is a modelled difference on synthetic orders, not observed savings."
        ),
    }


_VOI_CACHE: dict | None = None


def value_of_information(base: dict | None = None) -> dict:
    """Flip each order's contract types, re-solve that bucket, and rank the decision impact."""
    from app.economics import flip_delay_type, flip_penalty_type, types_unverified

    global _VOI_CACHE
    if base is None and _VOI_CACHE is not None:
        return _VOI_CACHE
    if base is None:
        base = allocate(None, include_comparison=False, include_expedite=False)
    world = load_world()
    base_gap = float(base["totals"]["value_protected_vs_best_rule_rm"])
    buckets = {(bucket["plant_id"], bucket["product_id"]): bucket for bucket in base["buckets"]}
    rows = []
    for demand in world["demands"]:
        flipped = deepcopy(world)
        target = next(row for row in flipped["demands"] if int(row["id"]) == int(demand["id"]))
        old_penalty = str(target.get("penalty_type") or "per_m3")
        old_delay = str(target.get("delay_type") or "proportional")
        target["penalty_type"] = flip_penalty_type(old_penalty)
        target["delay_type"] = flip_delay_type(old_delay)
        key = (int(demand["plant_id"]), int(demand["product_id"]))
        current = buckets[key]
        alt = solve_bucket(flipped, key[0], key[1], include_comparison=False, include_expedite=False)
        new_gap = round_rm(base_gap - float(current["headline_gap"]["true_rm"]) + float(alt["headline_gap"]["true_rm"]))
        base_alloc = {int(line["demand_id"]): float(line["allocated_quantity"]) for line in current["allocations"]}
        moved = sum(abs(float(line["allocated_quantity"]) - base_alloc.get(int(line["demand_id"]), 0.0)) for line in alt["allocations"])
        m3_moved = round_m3(moved / 2.0)
        changed = m3_moved > 0.5
        rows.append(
            {
                "demand_id": int(demand["id"]),
                "demand_code": demand["demand_code"],
                "customer_or_project": demand["customer_or_project"],
                "plant_id": key[0],
                "product_id": key[1],
                "plant_name": current["plant_name"],
                "product_name": current["product_name"],
                "from_penalty_type": old_penalty,
                "to_penalty_type": target["penalty_type"],
                "from_delay_type": old_delay,
                "to_delay_type": target["delay_type"],
                "types_unverified": bool(types_unverified(demand)),
                "gap_change_rm": round_rm(new_gap - base_gap),
                "allocation_changed": changed,
                "m3_moved": m3_moved,
                "statement": (
                    f"This plan changes if {demand['customer_or_project']}'s clause is {target['penalty_type']} / {target['delay_type']}."
                    if changed
                    else ""
                ),
            }
        )
    rows.sort(key=lambda row: (-float(row["m3_moved"]), -abs(float(row["gap_change_rm"])), row["demand_code"]))
    payload = {
        "order_count": len(rows),
        "order_count_note": "One row per demand line. The book has 18 lines. Tranches are not separate orders.",
        "lines": rows,
        "note": (
            "Each line is re-solved on its own plant and product with that order's penalty type and delay type flipped. "
            "The recommendation and the best simple rule are both solved again. Other plant-products stay on the base solve. "
            "The list is ranked by cubic metres moved, then by the change in the headline gap."
        ),
    }
    _VOI_CACHE = payload
    return payload


def _book_party_burden(buckets: list[dict]) -> dict:
    sides = {
        "before": {"internal": {"unserved_m3": 0.0, "consequence_rm": 0.0}, "external": {"unserved_m3": 0.0, "consequence_rm": 0.0}},
        "after": {"internal": {"unserved_m3": 0.0, "consequence_rm": 0.0}, "external": {"unserved_m3": 0.0, "consequence_rm": 0.0}},
    }
    for bucket in buckets:
        burden = bucket.get("party_burden") or {}
        for when in ("before", "after"):
            block = burden.get(when)
            if not block:
                continue
            for party in ("internal", "external"):
                sides[when][party]["unserved_m3"] += float(block[party]["unserved_m3"])
                sides[when][party]["consequence_rm"] += float(block[party]["consequence_rm"])
    for when in sides.values():
        unserved = when["internal"]["unserved_m3"] + when["external"]["unserved_m3"]
        consequence = when["internal"]["consequence_rm"] + when["external"]["consequence_rm"]
        for party in when.values():
            party["unserved_m3"] = round_m3(party["unserved_m3"])
            party["consequence_rm"] = round_rm(party["consequence_rm"])
            party["unserved_share"] = round(party["unserved_m3"] / unserved, 4) if unserved > TOL else 0.0
            party["consequence_share"] = round(party["consequence_rm"] / consequence, 4) if consequence > 1 else 0.0
    return {
        **sides,
        "note": "Before is the all-proportional plan scored as linear. After is the recommendation scored on each order's own penalty and delay type.",
    }


def control_tower() -> dict:
    result = allocate(None)
    band = value_protected_band(result)
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
        fragility = assess_fragility(bucket["plant_id"], bucket["product_id"])
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
                "fragile": fragility["fragile"],
                "flip_point": fragility["flip_point"],
                "fragility_summary": fragility["summary"],
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
            "value_protected_rm": totals["value_protected_vs_best_rule_rm"],
            "value_protected_vs_earliest_rm": totals["value_protected_vs_earliest_rm"],
            "value_protected_vs_best_rule_rm": totals["value_protected_vs_best_rule_rm"],
            "best_rule_by_bucket": totals["best_rule_by_bucket"],
            "value_protected_note": "Modelled gap versus the best simple rule on the same supply. Earliest-date is the second comparison. Not observed savings.",
            "value_protected_range": band,
            "headline_gap_if_scored_proportional_rm": totals["headline_gap_if_scored_proportional_rm"],
            "headline_gap_effect_rm": totals["headline_gap_effect_rm"],
            "headline_gap_note": totals["headline_gap_note"],
            "constrained_buckets": totals["constrained_buckets"],
            "party_burden": _book_party_burden(result["buckets"]),
        },
        "insight": (
            f"Adding the month together shows a surplus of {_m3(totals['horizon_surplus_m3'])}. "
            f"That surplus is in the wrong dates. Once required dates are respected, {_m3(totals['dated_shortfall_m3'])} stays unserved. "
            "The decision is which orders absorb that dated shortfall."
        ),
        "hotspots": hotspots,
        "exceptions": [
            {
                "tone": "critical" if row["shortfall_m3"] >= 200 or row["programme_days"] >= 2 else "watch",
                "plant_id": row["plant_id"],
                "product_id": row["product_id"],
                "plant_name": row["plant_name"],
                "product_name": row["product_name"],
                "crunch_date": row["crunch_date"],
                "shortfall_m3": row["shortfall_m3"],
                "programme_days": row["programme_days"],
                "fragile": row["fragile"],
                "flip_point": row["flip_point"],
                "text": (
                    f"{row['plant_name']} / {row['product_name']}: dated shortfall {_m3(row['shortfall_m3'])} "
                    f"by {_pretty(row['crunch_date'])}. Programme days left open in the recommendation: {row['programme_days']:g}."
                    + (f" {row['flip_point']}" if row["fragile"] else " Not fragile on the 10% and 20% grid.")
                ),
            }
            for row in hotspots
        ],
        "policy_totals": {
            "optimised_rm": totals["expected_consequence_rm"],
            "earliest_rm": totals["earliest_expected_consequence_rm"],
            "internal_first_rm": totals["internal_first_expected_consequence_rm"],
            "external_first_rm": totals["external_first_expected_consequence_rm"],
            "practice_rm": totals["practice_expected_consequence_rm"],
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
