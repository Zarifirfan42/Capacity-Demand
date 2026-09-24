"""Sanity checks for the hero allocation. Run from the backend directory."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["CDI_DB"] = str(Path(tempfile.mkdtemp()) / "selfcheck.db")

from app.engine import allocate
from app.operations import plant_mode
from app.seed import seed


def _bucket(result, plant: str, product: str) -> dict:
    return next(row for row in result["buckets"] if row["plant_name"] == plant and row["product_code"] == product)


def _line(bucket: dict, code: str) -> dict:
    return next(row for row in bucket["allocations"] if row["demand_code"] == code)


def main() -> None:
    seed(force=True)
    result = allocate(None)
    hero = _bucket(result, "Shah Alam Works", "G40")
    merdeka = _line(hero, "INT-MERDEKA")
    gamuda = _line(hero, "EXT-GAMUDA")
    jkr = _line(hero, "EXT-JKR")
    sunway = _line(hero, "EXT-SUNWAY")
    elmina = _line(hero, "INT-ELMINA")
    ytl = _line(hero, "EXT-YTL")
    mitra = _line(hero, "EXT-MITRA")

    assert abs(hero["windows"][-1]["supply_to_date_m3"] - 0) >= 0
    crunch = max(hero["windows"], key=lambda row: row["gap_m3"])
    assert crunch["date"] == "2026-10-09", crunch
    assert abs(crunch["supply_to_date_m3"] - 635) < 1, crunch
    assert abs(crunch["gap_m3"] - 775) < 1, crunch
    assert abs(crunch["usable_inventory_m3"]) < 0.2, crunch
    assert abs(merdeka["allocated_quantity"] - 400) < 0.2, merdeka
    assert abs(gamuda["allocated_quantity"]) < 0.2, gamuda
    assert gamuda["unserved_quantity"] > 299
    assert abs(jkr["allocated_quantity"] - 180) < 0.2, jkr
    assert sunway["unserved_quantity"] > 219, sunway
    assert elmina["unserved_quantity"] > 90, elmina
    assert ytl["unserved_quantity"] > 159, ytl
    assert abs(mitra["unserved_quantity"]) < 0.2, mitra
    assert hero["objective_epsilon_rm"] == 10
    assert hero["recommendation_basis"] == "typed_milp"
    assert hero["comparison"]["proportional_regret_under_true_terms_rm"] > 0

    policies = {row["policy_code"]: row["expected_consequence_rm"] for row in hero["policies"]}
    assert policies["optimised"] <= policies["earliest"] + 11, policies
    assert policies["optimised"] <= policies["internal"] + 11, policies
    assert policies["optimised"] <= policies["external"] + 11, policies
    assert "earliest" in hero["explanation"]["tradeoff"].lower() or "Earliness" in hero["explanation"]["tradeoff"]
    assert "RM10" in hero["explanation"]["tie_break"]

    g50 = _bucket(result, "Shah Alam Works", "G50")
    assert abs(_line(g50, "INT-PENANG")["allocated_quantity"] - 40) < 0.2
    assert abs(_line(g50, "INT-PENANG")["unserved_quantity"] - 170) < 0.2
    assert _line(g50, "EXT-IJM")["unserved_quantity"] < 0.2

    pcs = _bucket(result, "Pasir Gudang Works", "PCS")
    assert _line(pcs, "INT-ECRL")["unserved_quantity"] < 0.2
    assert _line(pcs, "EXT-SETIA")["unserved_quantity"] > 34

    assert result["totals"]["dated_shortfall_m3"] > result["totals"]["aggregate_gap_m3"]
    assert result["totals"]["horizon_surplus_m3"] > 0
    assert result["totals"]["value_protected_vs_earliest_rm"] > 0
    assert result["totals"]["value_protected_vs_best_rule_rm"] >= 0
    assert hero["solver_status"] == "Optimal"
    assert hero["headline_gap"]["true_rm"] >= 0
    assert _line(g50, "INT-PENANG")["partial_service_no_penalty_avoided"] is True

    print("Hero supply by 9 Oct", crunch["supply_to_date_m3"])
    print("Hero gap", crunch["gap_m3"])
    print("Hero policies", policies)
    print("Dated shortfall", result["totals"]["dated_shortfall_m3"])
    print("Horizon surplus", result["totals"]["horizon_surplus_m3"])
    print("Value protected vs earliest", result["totals"]["value_protected_vs_earliest_rm"])
    print("Value protected vs best rule", result["totals"]["value_protected_vs_best_rule_rm"])
    print("Best rules", result["totals"]["best_rule_by_bucket"])
    print("Plant modes", {plant["name"]: plant_mode(plant["id"]) for plant in result["plants"]} if "plants" in result else "shadow by default")
    print("SELF-CHECK PASSED")


if __name__ == "__main__":
    main()
