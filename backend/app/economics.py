"""Consequence math shared by the optimiser, scenarios, and impact pages.

The allocation engine minimises expected business consequence. It does not
prefer internal projects or external customers. Operational limits are
constraints. They are not a hidden penalty term.
"""

from __future__ import annotations

HORIZON_START = "2026-10-01"
HORIZON_END = "2026-10-30"
HORIZON_DAYS = 30

# Judgemental planning-certainty weights. They are not calibrated probabilities.
CONFIDENCE_FACTOR = {
    "Confirmed": 1.0,
    "Probable": 0.75,
    "Forecast": 0.45,
}
PLANNING_CERTAINTY_WEIGHT = CONFIDENCE_FACTOR

# Assumptions for who reviews a recommendation. Not a company policy.
REVIEW_PROGRAMME_DAYS = 2.0
REVIEW_EXPECTED_CONSEQUENCE_RM = 25000.0
REVIEW_PENALTY_RM = 20000.0
CARRYING_RATE_ANNUAL = 0.08

CRITICALITY_MULT = {
    "Critical": 1.5,
    "High": 1.2,
    "Medium": 1.0,
    "Low": 0.7,
    "n/a": 1.0,
}

ASSUMPTIONS = [
    "Figures are synthetic. Inventory unit values, emergency production costs, and several programme delay rates are marked assumptions because a live ERP extract is not connected.",
    "Available capacity = daily plant capacity − production already committed outside this demand book.",
    "Usable inventory = max(0, on-hand − safety stock). Safety stock is reserved and is not allocated.",
    "Available supply = available capacity + usable inventory.",
    "An order can only use its own plant and its own product, on or before its required date. Products are not substitutes, and plants are not balanced automatically.",
    "Partial supply is allowed. Margin, contractual penalty, and programme delay cost scale with the unserved fraction of the order.",
    "Planning certainty weights scale the objective only: Confirmed 100%, Probable 75%, Forecast 45%. These are judgemental weights, not calibrated probabilities. Gross ringgit amounts are still shown in full.",
    "The current-practice proxy is an illustrative rule: firm orders first, then required date, then margin and penalty per m³. It ignores programme delay. It is not an observed history.",
    "Inventory carrying cost for the horizon = on-hand value × 8% a year × 30/365. The 8% rate is an assumption. On-hand value itself is not the carrying cost.",
    "A material review is assumed when programme days are at least 2, expected consequence is at least RM25,000, or contractual penalty at risk is at least RM20,000. Those cut-offs are modelling assumptions.",
    "Project criticality is recorded for the planner. The optimiser uses the delay cost already stored on the order. Changing criticality in a scenario rescales that delay cost.",
    "Emergency production cost is an assumption used only on the expedite screen and in business impact. It is not added to base capacity unless a scenario raises capacity.",
    "The model recommends. A person approves or overrides, and that decision is stored.",
]


def round_m3(value: float) -> float:
    return round(float(value), 2)


def round_rm(value: float) -> float:
    return round(float(value), 2)


def confidence_factor(level: str) -> float:
    return CONFIDENCE_FACTOR.get(level, 1.0)


def line_economics(demand: dict) -> dict:
    """Money and day consequences for one demand line.

    contribution_margin, contractual_penalty, and delay cost are totals for
    the full requested quantity. A partial shortfall takes the same fraction.
    """
    qty = float(demand["requested_quantity"])
    margin = float(demand["contribution_margin"])
    penalty = float(demand["contractual_penalty"])
    delay_days = float(demand["delay_days_if_unserved"])
    delay_per_day = float(demand["delay_cost_per_day"])
    delay_cost = delay_days * delay_per_day
    gross = margin + penalty + delay_cost
    factor = confidence_factor(demand["confidence_level"])
    expected = gross * factor
    safe_qty = qty if qty > 0 else 1.0
    return {
        "quantity": qty,
        "contribution_margin_rm": margin,
        "contractual_penalty_rm": penalty,
        "delay_days_if_unserved": delay_days,
        "delay_cost_per_day_rm": delay_per_day,
        "delay_cost_if_fully_unserved_rm": delay_cost,
        "gross_consequence_rm": gross,
        "confidence_factor": factor,
        "expected_consequence_rm": expected,
        "margin_per_m3": margin / safe_qty,
        "penalty_per_m3": penalty / safe_qty,
        "delay_cost_per_m3": delay_cost / safe_qty,
        "unit_gross_rm": gross / safe_qty if qty > 0 else 0.0,
        "unit_expected_rm": expected / safe_qty if qty > 0 else 0.0,
    }


def scaled(amount: float, unserved: float, quantity: float) -> float:
    if quantity <= 0 or unserved <= 0:
        return 0.0
    fraction = min(max(unserved, 0.0), quantity) / quantity
    return amount * fraction


def consequence_for_unserved(demand: dict, unserved: float) -> dict:
    econ = line_economics(demand)
    qty = econ["quantity"]
    unserved = min(max(unserved, 0.0), qty)
    fraction = (unserved / qty) if qty else 0.0
    programme_days = econ["delay_days_if_unserved"] * fraction if demand["demand_type"] == "Internal" else 0.0
    return {
        "unserved_quantity": round_m3(unserved),
        "allocated_quantity": round_m3(qty - unserved),
        "fraction_unserved": fraction,
        "margin_at_risk_rm": round_rm(econ["contribution_margin_rm"] * fraction),
        "penalty_at_risk_rm": round_rm(econ["contractual_penalty_rm"] * fraction),
        "delay_cost_incurred_rm": round_rm(econ["delay_cost_if_fully_unserved_rm"] * fraction),
        "gross_consequence_rm": round_rm(econ["gross_consequence_rm"] * fraction),
        "expected_consequence_rm": round_rm(econ["expected_consequence_rm"] * fraction),
        "programme_days": round(programme_days, 2),
        "consequence_avoided_gross_rm": round_rm(econ["gross_consequence_rm"] * (1 - fraction)),
        "consequence_avoided_expected_rm": round_rm(econ["expected_consequence_rm"] * (1 - fraction)),
    }


def expedite_advice(demand: dict, unserved: float, emergency_cost_per_m3: float, emergency_is_assumption: bool) -> dict | None:
    if unserved < 0.05:
        return None
    econ = line_economics(demand)
    compare_unit = econ["unit_gross_rm"] if demand["confidence_level"] == "Confirmed" else econ["unit_expected_rm"]
    basis = "gross consequence, because the order is confirmed" if demand["confidence_level"] == "Confirmed" else (
        "confidence-weighted consequence, so a probable or forecast order is not expedited on its full gross value"
    )
    expedite_cost = emergency_cost_per_m3 * unserved
    accepted = compare_unit * unserved
    worth = compare_unit > emergency_cost_per_m3 + 0.01
    return {
        "demand_id": demand["id"],
        "demand_code": demand["demand_code"],
        "customer_or_project": demand["customer_or_project"],
        "unserved_quantity": round_m3(unserved),
        "emergency_cost_per_m3": emergency_cost_per_m3,
        "emergency_cost_is_assumption": bool(emergency_is_assumption),
        "compare_unit_rm": round_rm(compare_unit),
        "comparison_basis": basis,
        "expedite_cost_rm": round_rm(expedite_cost),
        "consequence_if_accepted_rm": round_rm(accepted),
        "worth_expediting": worth,
        "net_benefit_rm": round_rm(accepted - expedite_cost),
    }


def carrying_cost(inventory_value_rm: float, days: int = HORIZON_DAYS) -> float:
    """Cost of holding stock for this horizon. The value itself is not the cost."""
    return round_rm(float(inventory_value_rm) * CARRYING_RATE_ANNUAL * (days / 365.0))


def review_case(programme_days: float, expected_rm: float, penalty_rm: float, internal_delay: bool, external_penalty: bool) -> dict:
    """Who reviews. Thresholds are labelled assumptions, not a head-office policy."""
    triggers = []
    if programme_days >= REVIEW_PROGRAMME_DAYS:
        triggers.append(f"Programme days at risk are {programme_days:g}, at or above the assumed review line of {REVIEW_PROGRAMME_DAYS:g}.")
    if expected_rm >= REVIEW_EXPECTED_CONSEQUENCE_RM:
        triggers.append(f"Expected consequence is RM{expected_rm:,.0f}, at or above the assumed review line of RM{REVIEW_EXPECTED_CONSEQUENCE_RM:,.0f}.")
    if penalty_rm >= REVIEW_PENALTY_RM:
        triggers.append(f"Contractual penalty at risk is RM{penalty_rm:,.0f}, at or above the assumed review line of RM{REVIEW_PENALTY_RM:,.0f}.")
    cross = internal_delay and external_penalty
    if cross:
        triggers.append("The same plant and product carries both an internal programme delay and an external penalty.")
    if not triggers:
        return {
            "level": "normal",
            "owner": "Plant scheduler",
            "triggers": [],
            "evidence": "The recommendation is inside the assumed review lines.",
            "assumption": True,
        }
    if cross:
        owner = "Plant supervisor, with the project planner and the commercial owner of the external order"
        level = "cross-business"
    else:
        owner = "Plant supervisor, with the party that bears the larger consequence"
        level = "material exception"
    return {
        "level": level,
        "owner": owner,
        "triggers": triggers,
        "evidence": "The scheduler still prepares the recommendation. The named reviewers approve or override it, with a reason.",
        "assumption": True,
    }
