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
    assert _line(hero, "EXT-GAMUDA")["allocated_quantity"] == 300
    assert _line(hero, "EXT-JKR")["unserved_quantity"] > 179
    policies = {row["policy_code"]: row["expected_consequence_rm"] for row in hero["policies"]}
    assert policies["optimised"] < policies["internal"]
    assert policies["optimised"] < policies["external"]
    assert "why_not" in _line(hero, "EXT-JKR")


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
    assert _line(hero, "INT-MERDEKA")["allocated_quantity"] < 1
    assert _line(hero, "EXT-GAMUDA")["allocated_quantity"] > 0


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


def test_zero_demand_is_ignored_and_safety_stock_is_not_usable() -> None:
    world = load_world()
    stock = next(row for row in world["inventory"] if row["plant_id"] == 1 and row["product_id"] == 1)
    assert stock["usable"] == stock["on_hand"] - stock["safety_stock"]
    assert stock["usable"] < stock["on_hand"]


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
    # Same supply. The proxy serves the earlier firm order. The optimiser leaves that order short.
    assert _line(hero, "EXT-JKR")["allocated_quantity"] < 1
    practice_jkr = next(row for row in practice["served"] if "JKR" in row["name"])
    assert practice_jkr["allocated_m3"] > 100
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
    assert any(column["key"] == "practice" for column in impact["columns"])


def test_projected_closing_stock_does_not_go_negative() -> None:
    hero = _bucket(allocate(None), "Shah Alam Works", "G40")
    projection = hero["inventory_projection"]
    assert projection["projected_closing_on_hand_m3"] >= 0
    assert projection["projected_closing_on_hand_m3"] == round(
        projection["opening_on_hand_m3"] - projection["drawn_from_inventory_m3"], 2
    )
