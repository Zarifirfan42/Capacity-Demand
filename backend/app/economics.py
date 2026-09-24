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
# Inclusive days of forward demand used to judge excess stock. Counted from each stock row's as-of date.
EXCESS_COVER_DAYS = 14
# This book gives Grade 40 and Grade 50 separate dated capacities. The flag is off.
# Turn it on only when one batching plant must serve both grades.
SHARED_READY_MIX_BATCHING = False
READY_MIX_CODES = ("G40", "G50")
# After the best expected consequence is found, solutions inside this band may be
# rearranged. The band is total ringgit, not ringgit per cubic metre.
OBJECTIVE_EPSILON_RM = 10.0
SOLVER_TIME_LIMIT_SECONDS = 30
# A shocked plan is fragile when keeping the base allocation costs more than this
# above the re-solved optimum. The bar is the larger of a ringgit floor and a share of that optimum.
FRAGILITY_REGRET_RM = 5000.0
FRAGILITY_REGRET_SHARE = 0.05
# Optional emergency supply, as a share of that day's available capacity. It is not base capacity.
EMERGENCY_CAPACITY_SHARE = 0.30
SENSITIVITY_FACTORS = (0.9, 1.1, 0.8, 1.2)
PENALTY_TYPES = ("lump_sum", "per_m3", "per_day")
DELAY_TYPES = ("lump_days", "proportional", "per_day")
SIMPLE_RULES = ("internal", "external", "earliest", "penalty_delay", "complete_or_skip", "unit_expected")

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
    "Usable inventory = max(0, on-hand − safety stock). Safety stock is reserved and is not allocated. Ready-mix Grade 40 and Grade 50 cannot be stocked, so those quantities are zero. Inventory value and carrying cost are precast only.",
    "Confirmed cubic metres are weighted at 100%. Requested minus confirmed is a separate tranche, weighted at 75%, or at 45% when the line is Forecast.",
    "Excess stock is on-hand minus safety stock minus demand due inside a 14-day window starting on the stock as-of date. The 14 days are a parameter, not a fixed calendar date.",
    "Grade 40 and Grade 50 do not share a batching plant in this book. Each grade has its own dated capacity. The shared-batching flag is off. When it is on, their combined production on a day cannot exceed the larger grade's available capacity that day.",
    "Available supply = available capacity + usable inventory.",
    "An order can only use its own plant and its own product, on or before its required date. Products are not substitutes, and plants are not balanced automatically.",
    "Partial supply is allowed. Contribution margin scales with the unserved fraction of each tranche. A contractual penalty sits on the confirmed tranche only. Internal programme delay uses the same tranche weights as margin: confirmed cubic metres at 100%, and the remainder at its own confidence.",
    "Each order has a penalty type. lump_sum charges the full penalty once the shortfall passes lump_sum_trigger. per_m3 scales the penalty with the unserved fraction of the confirmed tranche. per_day treats the stored penalty as ringgit per day, times the stated delay days, times that same fraction. The recommendation is the mixed-integer solve under those types. Seeded types are labelled unverified. The 2×2 minimax stays off unless an order is separately flagged for it.",
    "Each internal order has a delay type. lump_days charges the full weighted programme cost once the shortfall passes the trigger. proportional and per_day scale delay days times cost per day with the unserved fraction, weighted by the tranche that is short. per_day is the same ringgit as proportional when the stored cost is a daily rate. The type is the contract form.",
    "lump_sum_trigger is any shortfall, or a percentage of the confirmed quantity for a penalty and of the requested quantity for lump_days. The seeded lump-sum and lump-days orders use any shortfall.",
    "The all-proportional plan is a labelled comparison. Its regret is its true-terms consequence minus the typed recommendation. A 2×2 minimax is used only when an order is flagged penalty type unverified.",
    f"Inside RM{OBJECTIVE_EPSILON_RM:.0f} of the best expected consequence, the solver minimises partially served orders, then leaves the later order unserved. Earliness is that last tie-break. It does not override a gap larger than the epsilon. RM{OBJECTIVE_EPSILON_RM:.0f} is a visible setting.",
    "Planning certainty weights scale the objective only: Confirmed 100%, Probable 75%, Forecast 45%. These are judgemental weights, not calibrated probabilities. Gross ringgit amounts are still shown in full.",
    "The headline modelled gap is the best simple rule's expected consequence minus the recommendation, on the same demand and supply. The simple rules are internal-first, external-first, earliest required date, penalty and delay per cubic metre, complete-or-skip for lump-sum orders, and a greedy rank by unit expected consequence. Each rule schedules production on the latest feasible day, so an early due date is not starved by a later order. Each bucket uses whichever rule scores lowest. The gap is at least zero because the recommendation minimises the same objective. It is not observed savings.",
    "The contract-uncertainty range re-solves that gap under all-linear terms, the seeded mix, and all-lump terms. It replaces a 60/140% rescaling of penalty and delay cost.",
    "The current-practice proxy ranks firm orders, then due date, then margin and penalty. It ignores programme delay when it chooses, then the consequence still includes that delay. It is a footnote, not the headline.",
    "Inventory carrying cost for the horizon = on-hand value × 8% a year × 30/365. The 8% rate is an assumption. On-hand value itself is not the carrying cost.",
    "A material review is assumed when programme days are at least 2, expected consequence is at least RM25,000, or contractual penalty at risk is at least RM20,000. Those cut-offs are modelling assumptions.",
    "Project criticality is recorded for the planner. The optimiser uses the delay cost already stored on the order. Changing criticality in a scenario rescales that delay cost.",
    "Emergency production cost is an assumption used only on the expedite screen and in business impact. It is not added to base capacity unless a scenario raises capacity.",
    "The model recommends. A person approves or overrides, and that decision is stored.",
    f"A recommendation is fragile when keeping the base allocation under a 10% or 20% shock costs more than the re-solved optimum by at least RM{FRAGILITY_REGRET_RM:,.0f} or {FRAGILITY_REGRET_SHARE:.0%} of that optimum, whichever is larger. The share of shocks that cross the bar is shown. Unit expected consequence is margin, penalty, and delay cost scaled together.",
    "Emergency capacity is an optional top-up, capped at 30% of that day's available capacity and priced at the assumed emergency cost per cubic metre. It is a proposal for approval. It is not added to base capacity. The expedite screen prices the cubic metres needed to get back under a lump trigger, and ranks orders by penalty and delay avoided per ringgit of that cost.",
    "A partial allocation that leaves penalty and delay unchanged versus missing the whole order is labelled partial service with no penalty avoided. Minimum useful delivery is cubic metres, default zero, and a positive value forces the solver to deliver at least that much or nothing.",
    "The recommendation minimises the objective the gap is scored on, so the gap is at least zero in every contract world. The magnitude is meaningful only if the seeded inputs are.",
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


def flip_penalty_type(kind: str) -> str:
    current = penalty_type_of({"penalty_type": kind})
    if current == "lump_sum":
        return "per_m3"
    return "lump_sum"


def flip_delay_type(kind: str) -> str:
    current = delay_type_of({"delay_type": kind})
    if current == "lump_days":
        return "proportional"
    return "lump_days"


def types_unverified(demand: dict) -> bool:
    if "types_unverified" in demand and demand["types_unverified"] is not None:
        return bool(int(demand["types_unverified"]))
    return True


def full_miss_delay_weight(demand: dict) -> float:
    requested = float(demand["requested_quantity"])
    if requested <= 0:
        return 1.0
    confirmed = min(max(float(demand.get("confirmed_quantity") or 0.0), 0.0), requested)
    remainder = max(requested - confirmed, 0.0)
    remainder_weight = confidence_factor(remainder_confidence(demand))
    return (confirmed / requested) * 1.0 + (remainder / requested) * remainder_weight


def exposure_per_m3(demand: dict) -> float:
    """Average penalty plus delay per cubic metre. A lump is spread over its base quantity."""
    requested = float(demand["requested_quantity"])
    confirmed = min(max(float(demand.get("confirmed_quantity") or 0.0), 0.0), requested if requested else 0.0)
    penalty = float(demand.get("contractual_penalty") or 0.0)
    days = float(demand.get("delay_days_if_unserved") or 0.0)
    delay_total = days * float(demand.get("delay_cost_per_day") or 0.0)
    penalty_per = 0.0
    if confirmed > 0 and penalty > 0:
        kind = penalty_type_of(demand)
        penalty_per = (penalty * days / confirmed) if kind == "per_day" else penalty / confirmed
    delay_per = (delay_total * full_miss_delay_weight(demand) / requested) if requested > 0 and delay_total > 0 else 0.0
    return penalty_per + delay_per


def expedite_advice(demand: dict, unserved: float, emergency_cost_per_m3: float, emergency_is_assumption: bool) -> dict | None:
    """Price the cubic metres that close a firing lump. Proportional shortfalls stay off this list."""
    if unserved < 0.05:
        return None
    requested = float(demand["requested_quantity"])
    confirmed = min(max(float(demand.get("confirmed_quantity") or 0.0), 0.0), requested)
    remainder = max(requested - confirmed, 0.0)
    unserved = min(max(float(unserved), 0.0), requested)
    unserved_remainder = min(remainder, unserved)
    unserved_confirmed = unserved - unserved_remainder
    now = score_parent_unserved(demand, unserved)
    close_m3 = 0.0
    steps = []
    if penalty_type_of(demand) == "lump_sum" and confirmed > 0 and float(demand.get("contractual_penalty") or 0) > 0:
        trigger = lump_trigger_m3(demand, confirmed)
        if unserved_confirmed > trigger + 1e-6:
            need = unserved_confirmed - trigger
            close_m3 = max(close_m3, need)
            steps.append("contractual penalty")
    if delay_type_of(demand) == "lump_days" and float(demand.get("delay_days_if_unserved") or 0) > 0:
        trigger = lump_trigger_m3(demand, requested)
        if unserved > trigger + 1e-6:
            need = unserved - trigger
            close_m3 = max(close_m3, need)
            steps.append("programme delay")
    if close_m3 < 0.05 or not steps:
        return None
    after = score_parent_unserved(demand, max(unserved - close_m3, 0.0))
    avoided = max(
        0.0,
        (now["penalty_at_risk_rm"] + now["delay_cost_incurred_rm"])
        - (after["penalty_at_risk_rm"] + after["delay_cost_incurred_rm"]),
    )
    expedite_cost = emergency_cost_per_m3 * close_m3
    ratio = (avoided / expedite_cost) if expedite_cost > 0.01 else 0.0
    worth = avoided > expedite_cost + 0.01
    return {
        "demand_id": demand["id"],
        "demand_code": demand["demand_code"],
        "customer_or_project": demand["customer_or_project"],
        "unserved_quantity": round_m3(unserved),
        "close_m3": round_m3(close_m3),
        "emergency_cost_per_m3": emergency_cost_per_m3,
        "emergency_cost_is_assumption": bool(emergency_is_assumption),
        "steps_closed": steps,
        "comparison_basis": "Cubic metres to get back under the lump trigger. Ranked by penalty and delay avoided per ringgit of emergency cost.",
        "expedite_cost_rm": round_rm(expedite_cost),
        "penalty_and_delay_avoided_rm": round_rm(avoided),
        "avoided_per_rm": round(ratio, 2),
        "worth_expediting": worth,
        "net_benefit_rm": round_rm(avoided - expedite_cost),
    }


def carrying_cost(inventory_value_rm: float, days: int = HORIZON_DAYS) -> float:
    """Cost of holding stock for this horizon. The value itself is not the cost."""
    return round_rm(float(inventory_value_rm) * CARRYING_RATE_ANNUAL * (days / 365.0))


def remainder_confidence(demand: dict) -> str:
    parent = str(demand.get("confidence_level") or "Confirmed")
    status = str(demand.get("demand_status") or "").strip().lower()
    if parent == "Forecast" or status == "forecast":
        return "Forecast"
    return "Probable"


def penalty_type_of(demand: dict) -> str:
    kind = str(demand.get("penalty_type") or "per_m3")
    return kind if kind in PENALTY_TYPES else "per_m3"


def delay_type_of(demand: dict) -> str:
    kind = str(demand.get("delay_type") or "proportional")
    return kind if kind in DELAY_TYPES else "proportional"


def penalty_unverified(demand: dict) -> bool:
    return bool(int(demand.get("penalty_type_unverified") or 0))


def lump_trigger_m3(demand: dict, base_qty: float) -> float:
    """Any shortfall, or a percentage of the base quantity. The base is confirmed m³ for a penalty and requested m³ for lump_days."""
    raw = str(demand.get("lump_sum_trigger") or "any").strip().lower().replace("%", "")
    if raw in {"", "any", "any shortfall"}:
        return 0.0
    return max(0.0, float(base_qty) * float(raw) / 100.0)


def parent_score(
    demand: dict,
    tranche_unserved: list[tuple[str, float, float]],
    *,
    penalty_as: str | None = None,
    delay_as: str | None = None,
) -> dict:
    """Score one order from tranche unserved quantities.

    tranche_unserved rows are (kind, quantity, unserved). kind is confirmed or unconfirmed.
    Margin uses each tranche's own confidence. Penalty uses the confirmed tranche only.
    Delay uses tranche weights: confirmed unserved cubic metres at 100%, remainder unserved cubic metres at the remainder confidence.
    A lump of delay days charges the full-miss weighted pool once the trigger is passed.
    """
    requested = float(demand["requested_quantity"])
    margin = float(demand["contribution_margin"])
    penalty = float(demand["contractual_penalty"])
    days = float(demand["delay_days_if_unserved"])
    daily = float(demand["delay_cost_per_day"])
    delay_total = days * daily
    penalty_type = penalty_as or penalty_type_of(demand)
    delay_type = delay_as or delay_type_of(demand)
    confirmed_qty = min(max(float(demand.get("confirmed_quantity") or 0.0), 0.0), requested)
    margin_at = 0.0
    margin_gross = 0.0
    penalty_at = 0.0
    penalty_gross = 0.0
    unserved_total = 0.0
    unserved_confirmed = 0.0
    for kind, qty, unserved in tranche_unserved:
        qty = float(qty)
        unserved = min(max(float(unserved), 0.0), qty)
        unserved_total += unserved
        level = "Confirmed" if kind == "confirmed" else remainder_confidence(demand)
        weight = confidence_factor(level)
        if requested > 0 and qty > 0:
            share = margin * (qty / requested) * (unserved / qty)
            margin_gross += share
            margin_at += share * weight
        if kind == "confirmed":
            unserved_confirmed += unserved
            if qty > 0 and penalty_type == "per_m3":
                penalty_gross += penalty * (unserved / qty)
                penalty_at += penalty * weight * (unserved / qty)
            elif qty > 0 and penalty_type == "per_day":
                penalty_gross += penalty * days * (unserved / qty)
                penalty_at += penalty * days * weight * (unserved / qty)
    if penalty_type == "lump_sum" and confirmed_qty > 0 and unserved_confirmed > lump_trigger_m3(demand, confirmed_qty) + 1e-6:
        penalty_gross += penalty
        penalty_at += penalty * confidence_factor("Confirmed")
    fraction = (unserved_total / requested) if requested else 0.0
    remainder_qty = max(requested - confirmed_qty, 0.0)
    remainder_weight = confidence_factor(remainder_confidence(demand))
    unserved_remainder = max(unserved_total - unserved_confirmed, 0.0)
    if requested > 0:
        weighted_unserved_share = (unserved_confirmed / requested) * 1.0 + (unserved_remainder / requested) * remainder_weight
        full_miss_weight = (confirmed_qty / requested) * 1.0 + (remainder_qty / requested) * remainder_weight
    else:
        weighted_unserved_share = 0.0
        full_miss_weight = 1.0
    triggered = unserved_total > lump_trigger_m3(demand, requested) + 1e-6
    if delay_type == "lump_days" and triggered:
        delay_gross = delay_total
        delay_at = delay_total * full_miss_weight
        programme = days if demand.get("demand_type") == "Internal" else 0.0
    else:
        delay_gross = delay_total * fraction
        delay_at = delay_total * weighted_unserved_share
        programme = days * fraction if demand.get("demand_type") == "Internal" else 0.0
    gross = margin_gross + penalty_gross + delay_gross
    expected = margin_at + penalty_at + delay_at
    served_fraction = 1.0 - fraction
    return {
        "margin_at_risk_rm": round_rm(margin_at),
        "penalty_at_risk_rm": round_rm(penalty_at),
        "delay_cost_incurred_rm": round_rm(delay_at),
        "gross_consequence_rm": round_rm(gross),
        "expected_consequence_rm": round_rm(expected),
        "programme_days": round(programme, 2),
        "consequence_avoided_gross_rm": round_rm(margin + penalty + delay_total - gross),
        "unserved_total": unserved_total,
        "served_fraction": served_fraction,
    }


def score_parent_unserved(demand: dict, unserved: float, *, penalty_as: str | None = None, delay_as: str | None = None) -> dict:
    """Score a parent allocation by serving the confirmed tranche before the remainder."""
    requested = float(demand["requested_quantity"])
    confirmed = min(max(float(demand.get("confirmed_quantity") or 0.0), 0.0), requested)
    remainder = max(requested - confirmed, 0.0)
    unserved = min(max(float(unserved), 0.0), requested)
    unserved_remainder = min(remainder, unserved)
    unserved_confirmed = unserved - unserved_remainder
    parts: list[tuple[str, float, float]] = []
    if confirmed > 0:
        parts.append(("confirmed", confirmed, unserved_confirmed))
    if remainder > 0:
        parts.append(("unconfirmed", remainder, unserved_remainder))
    if not parts:
        parts.append(("unconfirmed", requested, unserved))
    return parent_score(demand, parts, penalty_as=penalty_as, delay_as=delay_as)


def linear_unserved_rate(demand: dict, kind: str, quantity: float, *, penalty_as: str | None = None, delay_as: str | None = None) -> float:
    """Ringgit per unserved cubic metre, excluding a lump that is carried by a binary."""
    requested = float(demand["requested_quantity"])
    if requested <= 0 or quantity <= 0:
        return 0.0
    margin = float(demand["contribution_margin"])
    penalty = float(demand["contractual_penalty"])
    days = float(demand["delay_days_if_unserved"])
    daily = float(demand["delay_cost_per_day"])
    delay_total = days * daily
    level = "Confirmed" if kind == "confirmed" else remainder_confidence(demand)
    weight = confidence_factor(level)
    delay_weight = 1.0 if kind == "confirmed" else confidence_factor(remainder_confidence(demand))
    penalty_type = penalty_as or penalty_type_of(demand)
    delay_type = delay_as or delay_type_of(demand)
    rate = (margin / requested) * weight
    if kind == "confirmed" and penalty_type == "per_m3":
        rate += (penalty / quantity) * weight
    elif kind == "confirmed" and penalty_type == "per_day":
        rate += (penalty * days / quantity) * weight
    if delay_type != "lump_days":
        rate += (delay_total / requested) * delay_weight
    return rate


def review_case(
    programme_days: float,
    expected_rm: float,
    penalty_rm: float,
    internal_delay: bool,
    external_penalty: bool,
    fragile: bool = False,
    programme_line: float | None = None,
    expected_line: float | None = None,
    penalty_line: float | None = None,
) -> dict:
    """Who reviews. Thresholds are labelled assumptions, not a head-office policy."""
    programme_line = REVIEW_PROGRAMME_DAYS if programme_line is None else programme_line
    expected_line = REVIEW_EXPECTED_CONSEQUENCE_RM if expected_line is None else expected_line
    penalty_line = REVIEW_PENALTY_RM if penalty_line is None else penalty_line
    triggers = []
    if programme_days >= programme_line:
        triggers.append(f"Programme days at risk are {programme_days:g}, at or above the assumed review line of {programme_line:g}.")
    if expected_rm >= expected_line:
        triggers.append(f"Expected consequence is RM{expected_rm:,.0f}, at or above the assumed review line of RM{expected_line:,.0f}.")
    if penalty_rm >= penalty_line:
        triggers.append(f"Contractual penalty at risk is RM{penalty_rm:,.0f}, at or above the assumed review line of RM{penalty_line:,.0f}.")
    cross = internal_delay and external_penalty
    if cross:
        triggers.append("The same plant and product carries both an internal programme delay and an external penalty.")
    if fragile:
        triggers.append(
            "The recommendation is fragile. Keeping the base allocation under a 10% or 20% shock costs more than the re-solved optimum by the regret bar."
        )
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
    elif fragile:
        owner = "Plant supervisor, with the project planner and the commercial owner of the external order"
        level = "fragile choice"
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
