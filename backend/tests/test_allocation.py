"""Business-logic tests. They use a temporary database, not the demo file."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["CDI_DB"] = str(Path(tempfile.mkdtemp()) / "cdi.db")

from app.economics import line_economics  # noqa: E402
from app.engine import allocate, feasibility, load_world  # noqa: E402
from app.quality import assess  # noqa: E402
from app.seed import seed  # noqa: E402


def setup_module() -> None:
    seed(force=True)


def _bucket(result: dict, plant: str, code: str) -> dict:
    return next(row for row in result["buckets"] if row["plant_name"] == plant and row["product_code"] == code)


def _line(bucket: dict, code: str) -> dict:
    return next(row for row in bucket["allocations"] if row["demand_code"] == code)


def test_confidence_weights_do_not_treat_forecast_as_firm() -> None:
    firm = line_economics(
        {
            "requested_quantity": 100,
            "contribution_margin": 1000,
            "contractual_penalty": 0,
            "delay_days_if_unserved": 0,
            "delay_cost_per_day": 0,
            "confidence_level": "Confirmed",
        }
    )
    forecast = line_economics({**{
        "requested_quantity": 100,
        "contribution_margin": 1000,
        "contractual_penalty": 0,
        "delay_days_if_unserved": 0,
        "delay_cost_per_day": 0,
        "confidence_level": "Forecast",
    }})
    assert firm["expected_consequence_rm"] == 1000
    assert forecast["expected_consequence_rm"] == 450


def test_hero_book_serves_higher_consequence_not_internal_label() -> None:
    result = allocate(None)
    hero = _bucket(result, "Shah Alam Works", "G40")
    assert _line(hero, "INT-MERDEKA")["allocated_quantity"] == 400
    assert _line(hero, "EXT-JKR")["allocated_quantity"] == 180
    assert _line(hero, "EXT-GAMUDA")["unserved_quantity"] > 299
    policies = {row["policy_code"]: row["expected_consequence_rm"] for row in hero["policies"]}
    assert policies["optimised"] < policies["internal"]
    assert policies["optimised"] < policies["external"]
    assert "why_not" in _line(hero, "EXT-GAMUDA")


def test_removing_internal_delay_cost_can_flip_the_window() -> None:
    """Internal work is protected by its delay cost, not by its label."""
    world = load_world()
    merdeka = next(row for row in world["demands"] if row["demand_code"] == "INT-MERDEKA")
    result = allocate(
        {
            "name": "No delay cost",
            "capacity_factor": 1,
            "demand_adjustments": [{"demand_id": merdeka["id"], "delay_cost_per_day": 0}],
        }
    )
    hero = _bucket(result, "Shah Alam Works", "G40")
    assert _line(hero, "INT-MERDEKA")["allocated_quantity"] < 400
    assert _line(hero, "EXT-GAMUDA")["allocated_quantity"] > 200


def test_external_penalty_can_outrank_a_weaker_internal_line() -> None:
    """Reverse of the hero: a low-delay internal line must not automatically win."""
    result = allocate(None)
    bucket = _bucket(result, "Pasir Gudang Works", "PCS")
    ecrl = _line(bucket, "INT-ECRL")
    setia = _line(bucket, "EXT-SETIA")
    assert ecrl["unit_expected_rm"] > setia["unit_expected_rm"]
    assert ecrl["unserved_quantity"] < 0.2
    assert setia["unserved_quantity"] > 30


def test_override_cannot_invent_dated_supply() -> None:
    world = load_world()
    hero_demands = [row for row in world["demands"] if row["plant_id"] == 1 and row["product_id"] == 1]
    targets = {int(row["id"]): float(row["requested_quantity"]) for row in hero_demands}
    fit = feasibility(world, 1, 1, targets)
    assert fit["feasible"] is False


def test_ready_mix_is_not_stocked_and_precast_safety_stock_is_reserved() -> None:
    world = load_world()
    ready = next(row for row in world["inventory"] if row["plant_id"] == 1 and row["product_id"] == 1)
    assert ready["on_hand"] == 0
    assert ready["safety_stock"] == 0
    assert ready["usable"] == 0
    precast = next(row for row in world["inventory"] if row["plant_id"] == 1 and row["product_id"] == 3)
    assert precast["usable"] == precast["on_hand"] - precast["safety_stock"]
    assert precast["usable"] < precast["on_hand"]


def test_unconfirmed_remainder_is_a_lower_weighted_tranche() -> None:
    hero = _bucket(allocate(None), "Shah Alam Works", "G40")
    sunway = _line(hero, "EXT-SUNWAY")
    assert sunway["confirmed_quantity"] == 180
    assert sunway["requested_quantity"] == 220
    assert "180" in sunway["tranche_note"]
    assert "75%" in sunway["tranche_note"]
    assert "40" in sunway["tranche_note"]


def test_excess_window_follows_the_as_of_date() -> None:
    from app.impact import build_impact

    impact = build_impact()
    assert impact["inventory"]["excess_cover_days"] == 14
    assert impact["inventory"]["excess_window_end"] == "2026-10-14"
    assert all("Ready-Mix" not in line["product_name"] for line in impact["inventory"]["lines"])


def test_shared_batching_flag_caps_both_grades_on_a_day() -> None:
    from app.economics import SHARED_READY_MIX_BATCHING

    assert SHARED_READY_MIX_BATCHING is False
    shared = allocate({"name": "Shared batching", "shared_ready_mix_batching": True, "capacity_factor": 1})
    world_caps: dict[tuple[int, str], float] = {}
    from app.engine import load_world

    world = load_world()
    for row in world["calendar"]:
        if row["product_id"] in (1, 2):
            key = (row["plant_id"], row["prod_date"])
            world_caps[key] = max(world_caps.get(key, 0.0), float(row["available_capacity"]))
    used: dict[tuple[int, str], float] = {}
    for bucket in shared["buckets"]:
        if bucket["product_code"] not in ("G40", "G50"):
            continue
        for line in bucket["allocations"]:
            for slot in line["production_by_day"]:
                key = (bucket["plant_id"], slot["date"])
                used[key] = used.get(key, 0.0) + float(slot["quantity"])
    assert used
    for key, quantity in used.items():
        assert quantity <= world_caps[key] + 0.2


def test_quality_score_is_the_share_of_checks_that_passed() -> None:
    report = assess()
    assert report["checks"] == report["passed"] + report["failed"]
    assert report["quality_percent"] == round(100.0 * report["passed"] / report["checks"], 1)


def test_month_surplus_can_coexist_with_a_dated_shortfall() -> None:
    totals = allocate(None)["totals"]
    assert totals["horizon_surplus_m3"] > 0
    assert totals["dated_shortfall_m3"] > 0


def test_practice_proxy_is_not_the_earliest_date_rule() -> None:
    result = allocate(None)
    hero = _bucket(result, "Shah Alam Works", "G40")
    practice = next(row for row in hero["policies"] if row["policy_code"] == "practice")
    earliest = next(row for row in hero["policies"] if row["policy_code"] == "earliest")
    optimised = next(row for row in hero["policies"] if row["policy_code"] == "optimised")
    assert practice["method"].lower().startswith("illustrative")
    assert practice["expected_consequence_rm"] != optimised["expected_consequence_rm"]
    assert "practice_expected_consequence_rm" in result["totals"]
    assert result["totals"]["value_protected_vs_practice_rm"] == round(
        result["totals"]["practice_expected_consequence_rm"] - result["totals"]["expected_consequence_rm"], 2
    )
    assert _line(hero, "EXT-GAMUDA")["allocated_quantity"] < 1
    assert earliest["policy_code"] != practice["policy_code"]


def test_forecast_is_scored_and_kept_out_of_the_order_book() -> None:
    from app.forecast import build_forecast

    report = build_forecast()
    assert report["evaluation"]["points"] >= 6
    assert report["evaluation"]["mae_m3"] > 0
    assert report["evaluation"]["mape"] is None or 0 < report["evaluation"]["mape"] < 1
    assert report["october_planning_forecast_m3"] > 0
    book = sum(float(row["requested_quantity"]) for row in load_world()["demands"])
    assert abs(report["october_planning_forecast_m3"] - book) > 100
    assert sum(week["confirmed_m3"] for week in report["october_weeks"]) > 0
    assert sum(week["planning_demand_m3"] for week in report["october_weeks"]) == round(
        sum(week["confirmed_m3"] + week["forecast_class_in_book_m3"] for week in report["october_weeks"]), 2
    )


def test_review_owner_is_named_when_a_threshold_is_crossed() -> None:
    hero = _bucket(allocate(None), "Shah Alam Works", "G40")
    review = hero["decision_review"]
    assert review["owner"] != "Plant scheduler"
    assert review["triggers"]
    assert review["assumption"] is True


def test_carrying_cost_is_not_the_inventory_balance() -> None:
    from app.economics import carrying_cost
    from app.impact import build_impact

    impact = build_impact()
    value = impact["inventory"]["inventory_value_rm"]
    assert impact["inventory"]["carrying_cost_rm"] == carrying_cost(value)
    assert impact["inventory"]["carrying_cost_rm"] < value
    assert impact["columns"][0]["key"] == "best"
    assert impact["columns"][1]["key"] == "earliest"


def test_headline_gap_matches_the_earliest_date_rule_and_shows_a_range() -> None:
    from app.engine import control_tower
    from app.impact import build_impact

    result = allocate(None)
    earliest_gap = result["totals"]["value_protected_vs_earliest_rm"]
    proxy_gap = result["totals"]["value_protected_vs_practice_rm"]
    tower = control_tower()
    impact = build_impact()
    band = tower["kpis"]["value_protected_range"]
    best_gap = result["totals"]["value_protected_vs_best_rule_rm"]
    assert tower["kpis"]["value_protected_rm"] == best_gap
    assert impact["value_protected_rm"] == best_gap
    assert band["base_rm"] == best_gap
    assert band["seeded_rm"] == best_gap
    assert {row["label"] for row in band["cases"]} == {"all_linear", "seeded", "all_lump"}
    assert best_gap >= -0.05
    assert band["all_linear_rm"] >= -0.05
    assert band["all_lump_rm"] >= -0.05
    assert proxy_gap > 0
    assert impact["upper_bound"]["gap_rm"] == proxy_gap
    assert "not observed savings" in band["formula"].lower()
    assert impact["verify_contracts"]["order_count"] == 18


def test_projected_closing_stock_does_not_go_negative() -> None:
    hero = _bucket(allocate(None), "Shah Alam Works", "G40")
    projection = hero["inventory_projection"]
    assert projection["projected_closing_on_hand_m3"] >= 0
    assert projection["projected_closing_on_hand_m3"] == round(
        projection["opening_on_hand_m3"] - projection["drawn_from_inventory_m3"], 2
    )


def _sa_g40_ids() -> tuple[int, int]:
    world = load_world()
    plant = next(row["id"] for row in world["plants"] if row["name"] == "Shah Alam Works")
    product = next(row["id"] for row in world["products"] if row["code"] == "G40")
    return int(plant), int(product)


def test_pan_borneo_delay_is_continuous_as_confirmed_quantity_changes() -> None:
    from app.economics import score_parent_unserved

    world = load_world()
    borneo = next(row for row in world["demands"] if row["demand_code"] == "INT-BORNEO")
    delays = []
    previous = None
    for confirmed in (0, 1, 80):
        row = dict(borneo)
        row["confirmed_quantity"] = confirmed
        scored = score_parent_unserved(row, row["requested_quantity"])
        delays.append(scored["delay_cost_incurred_rm"])
        assert scored["penalty_at_risk_rm"] == 0
        if previous is not None and confirmed == 1:
            assert abs(scored["expected_consequence_rm"] - previous) < 700
        previous = scored["expected_consequence_rm"]
    assert delays == [31500, 31532.81, 34125]


def test_contractual_penalty_sits_on_the_confirmed_tranche() -> None:
    from app.economics import score_parent_unserved

    world = load_world()
    sunway = next(row for row in world["demands"] if row["demand_code"] == "EXT-SUNWAY")
    full = score_parent_unserved(sunway, sunway["requested_quantity"])
    remainder_only = score_parent_unserved(sunway, 40)
    assert full["penalty_at_risk_rm"] == 8000
    assert remainder_only["penalty_at_risk_rm"] == 0


def test_lump_trigger_can_be_a_percentage_of_confirmed() -> None:
    from app.economics import score_parent_unserved

    world = load_world()
    gamuda = dict(next(row for row in world["demands"] if row["demand_code"] == "EXT-GAMUDA"))
    gamuda["lump_sum_trigger"] = "50"
    below = score_parent_unserved(gamuda, 100)
    above = score_parent_unserved(gamuda, 151)
    assert below["penalty_at_risk_rm"] == 0
    assert above["penalty_at_risk_rm"] == 30000


def test_recommendation_is_the_typed_plan_and_reports_regret() -> None:
    hero = _bucket(allocate(None), "Shah Alam Works", "G40")
    assert hero["solver"] == "CBC mixed-integer programme"
    assert hero["recommendation_basis"] == "typed_milp"
    assert hero["objective_epsilon_rm"] == 10
    assert "RM10" in hero["explanation"]["tie_break"]
    assert "Earliness" in hero["explanation"]["tradeoff"]
    comparison = hero["comparison"]
    assert comparison["proportional_regret_under_true_terms_rm"] > 0
    assert "true terms" in comparison["note"]
    assert hero["headline_gap"]["effect_rm"] != 0
    assert hero["party_burden"]["before"]["external"]["unserved_m3"] != hero["party_burden"]["after"]["external"]["unserved_m3"]
    assert allocate(None)["totals"]["headline_gap_effect_rm"] != 0


def test_unverified_penalty_type_uses_minimax() -> None:
    from app.engine import solve_bucket

    world = load_world()
    plant_id, product_id = _sa_g40_ids()
    for row in world["demands"]:
        if row["demand_code"] == "EXT-GAMUDA":
            row["penalty_type_unverified"] = 1
    bucket = solve_bucket(world, plant_id, product_id)
    assert bucket["recommendation_basis"] == "minimax_unverified"
    assert bucket["unverified_minimax"]["chosen"] in {"lump_sum", "per_m3"}
    assert bucket["unverified_minimax"]["max_regret_linear_plan_rm"] >= 0


def test_fragility_is_reported_for_a_constrained_bucket() -> None:
    from app.engine import assess_fragility

    plant_id, product_id = _sa_g40_ids()
    report = assess_fragility(plant_id, product_id)
    assert "fragile" in report
    assert report["summary"]
    assert report["plant_id"] == plant_id
    assert "flip_share" in report
    assert 0 <= report["flip_share"] <= 1


def test_recommendation_beats_simple_rules_and_capacity_is_monotone() -> None:
    from app.economics import OBJECTIVE_EPSILON_RM, SOLVER_TIME_LIMIT_SECONDS
    from app.engine import apply_scenario, solve_bucket

    result = allocate(None)
    for bucket in result["buckets"]:
        assert bucket["solver_status"] == "Optimal"
        assert bucket["solver_time_limit_seconds"] == SOLVER_TIME_LIMIT_SECONDS
        recommended = bucket["expected_consequence_rm"]
        for policy in bucket["policies"]:
            if policy["policy_code"] == "optimised":
                continue
            assert recommended <= policy["expected_consequence_rm"] + OBJECTIVE_EPSILON_RM + 1
        assert bucket["headline_gap"]["true_rm"] >= -0.05
        assert bucket["headline_gap"]["best_rule"] in {"internal", "external", "earliest", "penalty_delay", "complete_or_skip", "unit_expected"}
    plant_id, product_id = _sa_g40_ids()
    world = load_world()
    base = solve_bucket(world, plant_id, product_id, include_comparison=False, include_expedite=False)
    bigger = solve_bucket(
        apply_scenario(world, {"name": "More capacity", "capacity_factor": 1.1, "plant_id": plant_id, "product_id": product_id}),
        plant_id,
        product_id,
        include_comparison=False,
        include_expedite=False,
    )
    assert bigger["expected_consequence_rm"] <= base["expected_consequence_rm"] + 1


def test_heuristics_leave_no_idle_supply_that_could_finish_a_skipped_order() -> None:
    from app.economics import penalty_type_of
    from app.engine import _collapse_plan, _expanded, _greedy, _parent_greedy, _supply_by_date

    world = load_world()
    for plant in world["plants"]:
        for product in world["products"]:
            raw = [row for row in world["demands"] if row["plant_id"] == plant["id"] and row["product_id"] == product["id"]]
            if not raw:
                continue
            days = sorted(
                row["prod_date"]
                for row in world["calendar"]
                if row["plant_id"] == plant["id"] and row["product_id"] == product["id"]
            )
            cap = {
                row["prod_date"]: float(row["available_capacity"])
                for row in world["calendar"]
                if row["plant_id"] == plant["id"] and row["product_id"] == product["id"]
            }
            usable = float(next(row["usable"] for row in world["inventory"] if row["plant_id"] == plant["id"] and row["product_id"] == product["id"]))
            parents, tranches = _expanded(raw)
            plans = {name: _collapse_plan(parents, tranches, _greedy(tranches, days, cap, usable, name)) for name in ("internal", "external", "earliest", "practice")}
            for name in ("penalty_delay", "complete_or_skip", "unit_expected"):
                plans[name] = _parent_greedy(parents, days, cap, usable, name)
            for name, plan in plans.items():
                used = {day: 0.0 for day in days}
                inventory_used = 0.0
                for slot in plan.values():
                    inventory_used += float(slot.get("from_inventory") or 0.0)
                    for item in slot.get("by_day") or []:
                        used[item["date"]] += float(item["quantity"])
                remaining = {day: max(0.0, cap[day] - used[day]) for day in days}
                remaining_inv = max(0.0, usable - inventory_used)
                for demand in parents:
                    slot = plan[int(demand["id"])]
                    if float(slot["allocated"]) > 0.5:
                        continue
                    need = float(demand["requested_quantity"])
                    if penalty_type_of(demand) == "lump_sum":
                        need = min(need, float(demand.get("confirmed_quantity") or 0.0))
                    if need <= 0.5:
                        continue
                    supply = _supply_by_date(days, remaining, remaining_inv, str(demand["required_date"]))
                    assert supply + 0.5 < need, (name, demand["demand_code"], supply, need)


def test_partial_service_label_and_minimum_useful_default() -> None:
    hero = _bucket(allocate(None), "Shah Alam Works", "G50")
    penang = _line(hero, "INT-PENANG")
    assert penang["minimum_useful_delivery_m3"] == 0
    assert "partial_service_no_penalty_avoided" in penang
