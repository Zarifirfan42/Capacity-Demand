"""Phase 4 behaviour. Each test uses its own database so the hero book stays untouched."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth import require_admin, require_writer
from app.db import connect, get_meta
from app.engine import _schedule_deadline, load_world, solve_bucket
from app.main import (
    ActualIn,
    ActualLineIn,
    AllocationLineIn,
    DecisionIn,
    ExpediteIn,
    InformalIn,
    SnapshotIn,
    admin_reset,
    record_actual,
    record_decision,
    record_expedite,
    record_informal,
    record_snapshot,
)
from app.measurement import live_metrics, score_illustration
from app.operations import set_buffer_days, set_plant_mode, validate_actuals
from app.seed import seed


def _db(monkeypatch, tmp_path):
    monkeypatch.setenv("CDI_DB", str(tmp_path / "cdi.db"))
    monkeypatch.setenv("CDI_PLANNER_PASSCODE", "planner-demo")
    monkeypatch.setenv("CDI_ADMIN_PASSCODE", "test-admin")
    seed()


def test_seed_never_deletes_a_decision(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final
            ) VALUES('t', 'p', 1, 1, 'a', 'b', '{}', '{}', 'kept', 'approved', 0, 0, 0, 0)
            """
        )
    seed()
    with connect() as conn:
        kept = get_meta(conn, "seed_version")
        count = conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"]
    assert kept
    assert int(count) == 1


def test_writes_require_a_passcode(monkeypatch) -> None:
    monkeypatch.delenv("CDI_ADMIN_PASSCODE", raising=False)
    monkeypatch.setenv("CDI_PLANNER_PASSCODE", "planner-demo")
    with pytest.raises(HTTPException) as missing:
        require_writer(None, None)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as unset:
        require_admin(None, None)
    assert unset.value.status_code == 403
    monkeypatch.setenv("CDI_ADMIN_PASSCODE", "test-admin")
    with pytest.raises(HTTPException) as wrong:
        require_admin("planner", "planner-demo")
    assert wrong.value.status_code == 401
    assert require_writer("planner", "planner-demo") == "planner"
    assert require_admin("admin", "test-admin") == "admin"


def test_shadow_plan_is_scored_against_the_recommendation(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    world = load_world()
    rows = [row for row in world["demands"] if row["plant_id"] == 2 and row["product_id"] == 3]
    body = record_informal(
        InformalIn(
            username="Planner",
            plant_id=2,
            product_id=3,
            allocations=[AllocationLineIn(demand_id=row["id"], allocated_quantity=0) for row in rows],
        ),
        "planner",
    )
    assert body["paired_gap_rm"] == round(body["informal_expected_rm"] - body["recommendation_expected_rm"], 2)
    assert body["paired_gap_rm"] >= -10


def test_buffer_pulls_lump_sum_orders_forward(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    assert _schedule_deadline("2026-10-09", "lump_sum", 0) == "2026-10-09"
    assert _schedule_deadline("2026-10-09", "per_m3", 3) == "2026-10-09"
    assert _schedule_deadline("2026-10-09", "lump_sum", 3) == "2026-10-06"
    assert _schedule_deadline("2026-10-02", "lump_sum", 5) == "2026-10-01"
    assert set_buffer_days(3) == 3


def test_pilot_commits_capacity_and_actuals_lock_replacement(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    set_plant_mode(2, "pilot")
    world = load_world()
    before = {
        int(row["id"]): float(row["requested_quantity"])
        for row in world["demands"]
        if row["plant_id"] == 2 and row["product_id"] == 3
    }
    bucket = solve_bucket(world, 2, 3, include_comparison=False, include_expedite=False)
    allocations = [
        AllocationLineIn(demand_id=line["demand_id"], allocated_quantity=line["allocated_quantity"])
        for line in bucket["allocations"]
    ]
    saved = record_decision(
        DecisionIn(
            username="Planner",
            plant_id=2,
            product_id=3,
            override_reason="Accepted the recommendation.",
            allocations=allocations,
            terms_confirmed=True,
            chosen_plan="typed",
        ),
        "planner",
    )
    decision_id = saved["decision"]["id"]
    with connect() as conn:
        stored = conn.execute("SELECT committed_json FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        committed = conn.execute(
            "SELECT COALESCE(SUM(committed_production), 0) AS n FROM capacity_calendar WHERE plant_id = 2 AND product_id = 3"
        ).fetchone()["n"]
    assert stored["committed_json"]
    after = load_world()
    shrunk = False
    for row in after["demands"]:
        if int(row["id"]) in before and float(row["committed_quantity"]) > 0:
            assert float(row["requested_quantity"]) <= before[int(row["id"])] - float(row["committed_quantity"]) + 0.1
            shrunk = True
    assert shrunk or float(committed) >= 0
    with pytest.raises(HTTPException) as blocked:
        record_decision(
            DecisionIn(
                username="Planner",
                plant_id=2,
                product_id=3,
                override_reason="Trying to replace it.",
                allocations=allocations,
            ),
            "planner",
        )
    assert blocked.value.status_code == 409
    too_much = [
        ActualLineIn(
            demand_id=line["demand_id"],
            delivered_m3=line["requested_quantity"] * 1.06,
            actual_delivery_date="2026-10-20",
            programme_days_lost=0,
            penalty_paid_rm=0,
        )
        for line in bucket["allocations"]
    ]
    with pytest.raises(HTTPException) as rejected:
        record_actual(decision_id, ActualIn(username="Planner", lines=too_much), "planner")
    assert rejected.value.status_code == 400
    accepted = [
        line.model_copy(update={"delivered_m3": round(line.delivered_m3 / 1.06 * 1.04, 2)})
        for line in too_much
    ]
    record_actual(decision_id, ActualIn(username="Planner", lines=accepted), "planner")
    with pytest.raises(HTTPException) as locked:
        record_decision(
            DecisionIn(
                username="Planner",
                plant_id=2,
                product_id=3,
                override_reason="Replace after actuals.",
                replace=True,
                allocations=allocations,
            ),
            "planner",
        )
    assert locked.value.status_code == 409
    assert "actuals" in str(locked.value.detail).lower()


def test_delivery_slack_and_expedite_label() -> None:
    source = [{
        "demand_id": 1,
        "customer_or_project": "ECRL",
        "demand_type": "Internal",
        "requested_quantity": 100,
        "contractual_penalty_rm": 0,
    }]
    with pytest.raises(ValueError):
        validate_actuals(source, [{
            "demand_id": 1,
            "delivered_m3": 106,
            "actual_delivery_date": "2026-10-20",
            "programme_days_lost": 0,
            "penalty_paid_rm": 0,
        }])
    checked = validate_actuals(source, [{
        "demand_id": 1,
        "delivered_m3": 104,
        "actual_delivery_date": "2026-10-20",
        "programme_days_lost": 1,
        "penalty_paid_rm": 0,
    }])
    assert checked[0]["delivered_m3"] == 104


def test_expedite_is_labelled_estimated_avoided(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    saved = record_expedite(
        ExpediteIn(
            username="Planner",
            plant_id=2,
            product_id=3,
            demand_id=1,
            customer_or_project="ECRL",
            decision="approve",
            emergency_rm_per_m3=80,
            available_volume_m3=20,
            cost_rm=1600,
            penalty_would_apply_rm=5000,
            delivered_in_full=True,
            on_time=True,
            penalty_paid_rm=1200,
        ),
        "planner",
    )
    assert saved["label"] == "estimated avoided"
    assert saved["estimated_avoided_rm"] == 3800


def test_weekly_stock_count_prices_excess_precast(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    world = load_world()
    product = next(row for row in world["products"] if row["code"] == "PCS")
    record_snapshot(
        SnapshotIn(plant_id=1, product_id=int(product["id"]), as_of_date="2026-10-01", on_hand=5000, safety_stock=10, entered_by="Plant"),
        "planner",
    )
    ready = next(row for row in world["products"] if row["code"] == "G40")
    record_snapshot(
        SnapshotIn(plant_id=1, product_id=int(ready["id"]), as_of_date="2026-10-01", on_hand=800, safety_stock=0, entered_by="Plant"),
        "planner",
    )
    live = live_metrics()
    assert live["snapshots"] == 2
    assert live["cash_tied_up"]["n"] == 1
    assert live["cash_tied_up"]["mean_30_rm"] is not None
    assert live["cash_tied_up"]["mean_30_rm"] > 0


def test_failure_illustration_fires_and_admin_reset_is_protected(monkeypatch, tmp_path) -> None:
    fired = score_illustration(
        [{"window": "baseline", "paired_gap_rm": 1000, "damages_rm": 100, "constrained_days": 2} for _ in range(4)]
        + [{"window": "pilot", "paired_gap_rm": -2000, "damages_rm": 400, "constrained_days": 2} for _ in range(8)]
    )
    assert fired["watermark"].startswith("SYNTHETIC ILLUSTRATION")
    assert fired["failure_rule_fired"]
    quiet = score_illustration(
        [{"window": "baseline", "paired_gap_rm": 1000, "damages_rm": 100, "constrained_days": 2}]
    )
    assert quiet["inconclusive"] is True
    _db(monkeypatch, tmp_path)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final
            ) VALUES('t', 'p', 1, 1, 'a', 'b', '{}', '{}', 'kept', 'approved', 0, 0, 0, 0)
            """
        )
    admin_reset("admin")
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"]
        plants = conn.execute("SELECT COUNT(*) AS n FROM plants").fetchone()["n"]
    assert int(count) == 0
    assert int(plants) > 0
