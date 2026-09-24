"""Phase 5 decision rights. Each test uses its own database."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.auth import require_admin, require_roles, require_writer
from app.db import connect
from app.engine import load_world, solve_bucket
from app.governance import (
    add_constraint,
    apply_due_settings,
    apply_expired_defaults,
    deadline_for,
    declare_delay,
    propose_setting,
    approve_setting,
    record_signoff,
    settings,
)
from app.main import (
    ActualIn,
    ActualLineIn,
    AllocationLineIn,
    DecisionIn,
    cron_governance,
    record_actual,
    record_decision,
)
from app.seed import seed


def _db(monkeypatch, tmp_path):
    monkeypatch.setenv("CDI_DB", str(tmp_path / "cdi.db"))
    for key in (
        "CDI_PASSCODE_SCHEDULER",
        "CDI_PASSCODE_PLANT_SUPERVISOR",
        "CDI_PASSCODE_PROJECT_PLANNER",
        "CDI_PASSCODE_COMMERCIAL_OWNER",
        "CDI_PASSCODE_ADMIN",
        "CDI_CRON_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    seed()


def _pcs_allocations(changed: bool) -> tuple[list[AllocationLineIn], list[dict]]:
    world = load_world()
    bucket = solve_bucket(world, 2, 3, include_comparison=False, include_expedite=False)
    lines = []
    for line in bucket["allocations"]:
        qty = float(line["allocated_quantity"])
        if changed and line["demand_code"] == "INT-ECRL":
            qty = max(0.0, qty - 1)
        lines.append(AllocationLineIn(demand_id=line["demand_id"], allocated_quantity=qty))
    return lines, bucket["allocations"]


def test_viewer_write_is_forbidden_and_a_bad_passcode_is_unauthenticated(monkeypatch) -> None:
    monkeypatch.delenv("CDI_PASSCODE_SCHEDULER", raising=False)
    with pytest.raises(HTTPException) as viewer:
        require_writer("viewer", "scheduler-demo")
    assert viewer.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        require_writer(None, None)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as wrong_role:
        require_admin("scheduler", "scheduler-demo")
    assert wrong_role.value.status_code == 403
    assert require_roles("project_planner")("project_planner", "planner-demo") == "project_planner"


def test_matching_recommendation_inside_the_lines_does_not_wait(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    allocations, _rows = _pcs_allocations(False)
    saved = record_decision(
        DecisionIn(username="Scheduler", plant_id=2, product_id=3, override_reason="Accepted the recommendation.", allocations=allocations, terms_confirmed=True, chosen_plan="typed"),
        "scheduler",
    )
    assert saved["status"] == "approved"
    assert saved["decision"]["required_signatories"] == []


def test_changed_allocation_needs_the_owners_and_not_the_preparer(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    allocations, _rows = _pcs_allocations(True)
    saved = record_decision(
        DecisionIn(username="Scheduler", plant_id=2, product_id=3, override_reason="Held one cubic metre back for the yard.", reason_category="Operational constraint", allocations=allocations, terms_confirmed=True, chosen_plan="typed"),
        "scheduler",
    )
    assert saved["status"] == "awaiting_signoff"
    owners = {(row["username"], row["role"]) for row in saved["decision"]["required_signatories"]}
    assert ("Daniel Ong", "project_planner") in owners
    assert ("Priya Nair", "commercial_owner") in owners
    decision_id = saved["decision"]["id"]
    with pytest.raises(ValueError):
        record_signoff(decision_id, "project_planner", "Nur Aina", "recommended", "I do not own this order.")
    with pytest.raises(ValueError):
        record_signoff(decision_id, "project_planner", "Scheduler", "recommended", "I prepared this recommendation.")
    record_signoff(decision_id, "project_planner", "Daniel Ong", "recommended", "The yard can spare one cubic metre.")
    closed = record_signoff(decision_id, "commercial_owner", "Priya Nair", "proposed", "The external order stays as recommended.")
    assert closed["status"] == "approved"
    with connect() as conn:
        final = json.loads(conn.execute("SELECT final_json FROM decisions WHERE id = ?", (decision_id,)).fetchone()["final_json"])
    lines = [
        ActualLineIn(demand_id=line["demand_id"], delivered_m3=line["requested_quantity"], actual_delivery_date="2026-10-20", programme_days_lost=0, penalty_paid_rm=0)
        for line in final["allocations"]
    ]
    record_actual(decision_id, ActualIn(username="Scheduler", lines=lines), "scheduler")
    with connect() as conn:
        stored = conn.execute("SELECT actual_json, status FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    assert stored["status"] == "approved"
    assert stored["actual_json"]


def test_disagreement_uses_the_lower_expected_consequence(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    recommended = {"allocations": [{"demand_id": 1, "allocated_quantity": 1, "requested_quantity": 1, "customer_or_project": "A"}]}
    proposed = {"allocations": [{"demand_id": 1, "allocated_quantity": 0, "requested_quantity": 1, "customer_or_project": "A"}]}
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final,
                required_signatories_json, deadline
            ) VALUES(?, 'Scheduler', 2, 3, 'Pasir Gudang Works', 'PCS', ?, ?, 'changed', 'awaiting_signoff', 100, 250, 0, 1, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                json.dumps(recommended),
                json.dumps(proposed),
                json.dumps([
                    {"username": "Daniel Ong", "role": "project_planner", "orders": ["ECRL"]},
                    {"username": "Priya Nair", "role": "commercial_owner", "orders": ["Setia"]},
                ]),
                (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            ),
        )
        decision_id = cursor.lastrowid
    record_signoff(decision_id, "project_planner", "Daniel Ong", "recommended", "I want the recommendation.")
    closed = record_signoff(decision_id, "commercial_owner", "Priya Nair", "proposed", "I want the other plan.")
    assert closed["status"] == "approved"
    with connect() as conn:
        final = json.loads(conn.execute("SELECT final_json FROM decisions WHERE id = ?", (decision_id,)).fetchone()["final_json"])
    assert final["allocations"][0]["allocated_quantity"] == 1


def test_constraint_is_priced_capped_and_ledgered(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    world = load_world()
    setia = next(row for row in world["demands"] if row["demand_code"] == "EXT-SETIA")
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final,
                required_signatories_json
            ) VALUES('t', 'Scheduler', 2, 3, 'Pasir Gudang Works', 'PCS', '{}', ?, 'changed', 'awaiting_signoff', 13250, 13250, 35, 35, ?)
            """,
            (
                json.dumps({"allocations": [{"demand_id": setia["id"], "requested_quantity": 35, "allocated_quantity": 0, "customer_or_project": "Setia"}]}),
                json.dumps([{"username": "Priya Nair", "role": "commercial_owner", "orders": ["Setia"]}]),
            ),
        )
        decision_id = cursor.lastrowid
    with pytest.raises(ValueError):
        add_constraint(decision_id, "Priya Nair", "commercial_owner", "cap", 10, "email", "Not evidence.", setia["id"])
    priced = add_constraint(decision_id, "Priya Nair", "commercial_owner", "cap", 10, "site diary", "Access is limited.", setia["id"])
    assert "Honouring this costs" in priced["label"]
    assert priced["owner"] == "Priya Nair"
    with connect() as conn:
        ledger = conn.execute("SELECT owner_name, cost_rm FROM constraint_ledger").fetchone()
    assert ledger["owner_name"] == "Priya Nair"
    assert ledger["cost_rm"] == priced["cost_rm"]
    add_constraint(decision_id, "Priya Nair", "commercial_owner", "days", 3, "crew roster", "Crew is short this week.", setia["id"])
    with pytest.raises(ValueError):
        add_constraint(decision_id, "Priya Nair", "commercial_owner", "reserve", 5, "access permit", "A third limit is too many.", None)


def test_expiry_stamps_the_deadline_and_a_second_run_is_a_no_op(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    deadline = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final,
                required_signatories_json, deadline
            ) VALUES('t', 'Scheduler', 2, 3, 'P', 'PCS', '{"allocations":[]}', '{"allocations":[]}', '', 'awaiting_signoff', 1, 2, 0, 0, ?, ?)
            """,
            (json.dumps([{"username": "Daniel Ong", "role": "project_planner", "orders": ["ECRL"]}]), deadline),
        )
    first = apply_expired_defaults()
    second = apply_expired_defaults()
    assert len(first["defaulted"]) == 1
    assert second["defaulted"] == []
    with connect() as conn:
        row = conn.execute("SELECT status, defaulted_at FROM decisions").fetchone()
    assert row["status"] == "defaulted"
    assert row["defaulted_at"] == deadline


def test_urgent_window_when_the_due_date_is_inside_a_day(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    created = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
    urgent = datetime.fromisoformat(deadline_for(created, "2026-10-14"))
    standard = datetime.fromisoformat(deadline_for(created, "2026-10-20"))
    assert urgent - created == timedelta(hours=2)
    assert standard - created == timedelta(hours=24)


def test_declaration_needs_a_source_and_credibility_starts_at_three(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    world = load_world()
    ecrl = next(row for row in world["demands"] if row["demand_code"] == "INT-ECRL")
    with pytest.raises(ValueError):
        declare_delay(ecrl["id"], "Daniel Ong", 5000, "guess", "No source was attached.")
    first = declare_delay(ecrl["id"], "Daniel Ong", 5000, "programme float report", "First declaration from the float report.")
    assert first["credibility"] == 1
    with pytest.raises(ValueError):
        declare_delay(ecrl["id"], "Daniel Ong", 9000, "holding cost", "Too soon to revise the figure.")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("UPDATE delay_declarations SET created_at = ? WHERE demand_id = ?", (old, ecrl["id"]))
        for _ in range(2):
            conn.execute(
                """
                INSERT INTO delay_declarations(
                    demand_id, project_name, declared_rm_per_day, source, reason, username, created_at, credibility, effective_rm_per_day
                ) VALUES(?, ?, 20000, 'holding cost', 'Earlier declaration.', 'Daniel Ong', ?, 1, 20000)
                """,
                (ecrl["id"], ecrl["customer_or_project"], old),
            )
        conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final, actual_json
            ) VALUES('t', 's', 2, 3, 'P', 'PCS', '{}', '{}', '', 'approved', 0, 0, 0, 0, ?)
            """,
            (json.dumps({"lines": [{"demand_id": ecrl["id"], "programme_days_lost": 1}]}),),
        )
    revised = declare_delay(ecrl["id"], "Daniel Ong", 20000, "holding cost", "Revised after the cooling-off period.")
    assert revised["credibility"] < 1
    assert revised["effective_rm_per_day"] < 20000


def test_threshold_change_waits_for_three_seats_and_the_next_cycle(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    opening = settings()["review_programme_days"]
    proposal = propose_setting("review_programme_days", "3", "The October book was reviewed too often.", "Siti Kamal", "project_planner")
    approve_setting(proposal["id"], "plant_supervisor", "Plant Lead")
    approve_setting(proposal["id"], "commercial_owner", "Farah Lim")
    assert settings()["review_programme_days"] == opening
    approved = approve_setting(proposal["id"], "project_planner", "Hafiz Rahman")
    assert approved["status"] == "approved"
    assert apply_due_settings(datetime(2026, 10, 15, tzinfo=timezone.utc)) == []
    assert settings()["review_programme_days"] == opening
    applied = apply_due_settings(datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert applied == [proposal["id"]]
    assert settings()["review_programme_days"] == 3
    assert apply_due_settings(datetime(2026, 11, 3, tzinfo=timezone.utc)) == []


def test_cron_route_is_idempotent(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    deadline = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final,
                required_signatories_json, deadline
            ) VALUES('t', 'Scheduler', 2, 3, 'P', 'PCS', '{"allocations":[]}', '{"allocations":[]}', '', 'awaiting_signoff', 1, 2, 0, 0, '[]', ?)
            """,
            (deadline,),
        )
    # An empty required list means nobody is missing, so plant a required owner.
    with connect() as conn:
        conn.execute(
            "UPDATE decisions SET required_signatories_json = ? WHERE status = 'awaiting_signoff'",
            (json.dumps([{"username": "Daniel Ong", "role": "project_planner"}]),),
        )
    first = cron_governance(None)
    second = cron_governance(None)
    assert len(first["defaulted"]) == 1
    assert second["defaulted"] == []
    monkeypatch.setenv("CDI_CRON_SECRET", "cron-secret")
    with pytest.raises(HTTPException) as blocked:
        cron_governance(None)
    assert blocked.value.status_code == 401
    assert cron_governance("Bearer cron-secret")["defaulted"] == []
