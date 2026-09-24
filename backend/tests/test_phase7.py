"""Phase 7: economics guard, queue, and demo expiry."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import connect
from app.engine import allocate
from app.extractor import SAMPLE_OCR
from app.intake import catalogue, confirm_intake, prepare_intake
from app.main import DemandCreate, create_demand
from app.queue import expire_demo, queue_for, reset_demo, start_demo
from app.seed import seed


def _db(monkeypatch, tmp_path):
    monkeypatch.setenv("CDI_DB", str(tmp_path / "cdi.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CDI_INTAKE_MODE", raising=False)
    seed()


def _acks(**extra):
    body = {
        "action": "save_new",
        "intent": "new",
        "source": "Simulated OCR",
        "acknowledge_zero_penalty": True,
        "acknowledge_zero_delay": True,
    }
    body.update(extra)
    return body


def test_sample_po_on_the_default_margin_does_not_move_headline_gaps(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    before = allocate(None)
    plants, products = catalogue()
    with connect() as conn:
        prepared = prepare_intake(SAMPLE_OCR, "Simulated OCR", plants, products, as_of="2026-09-24", conn=conn, force_mode="regex")
        with pytest.raises(ValueError, match="margin"):
            confirm_intake(conn, _acks(draft=prepared["draft"]), "scheduler")
        saved = confirm_intake(conn, _acks(draft=prepared["draft"], accept_default_margin=True), "scheduler")
        row = conn.execute(
            "SELECT economics_basis, contribution_margin FROM demands WHERE demand_code = ?",
            (saved["demand_code"],),
        ).fetchone()
        basis = row["economics_basis"]
        margin = row["contribution_margin"]
    after = allocate(None)
    assert basis == "default_assumption"
    assert margin > 0
    assert after["totals"]["value_protected_vs_earliest_rm"] == before["totals"]["value_protected_vs_earliest_rm"]
    assert after["totals"]["value_protected_vs_best_rule_rm"] == before["totals"]["value_protected_vs_best_rule_rm"]
    assert after["economics_incomplete"]["n"] == 1
    assert after["economics_incomplete"]["without_expected_consequence_rm"] == before["totals"]["expected_consequence_rm"]
    matched = [
        line
        for bucket in after["buckets"]
        for line in bucket["allocations"]
        if line["demand_code"] == saved["demand_code"]
    ]
    assert matched
    assert matched[0]["economics_badge"] == "Economics incomplete"
    assert after["economics_incomplete"]["with_expected_consequence_rm"] != after["economics_incomplete"]["without_expected_consequence_rm"]


def test_typed_margin_is_in_the_objective_and_not_badged(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    plants, products = catalogue()
    with connect() as conn:
        prepared = prepare_intake(SAMPLE_OCR, "Simulated OCR", plants, products, as_of="2026-09-24", conn=conn, force_mode="regex")
        draft = {**prepared["draft"], "contribution_margin": 9600, "contractual_penalty": 0, "delay_cost_per_day": 0}
        saved = confirm_intake(conn, _acks(draft=draft), "scheduler")
        basis = conn.execute("SELECT economics_basis FROM demands WHERE demand_code = ?", (saved["demand_code"],)).fetchone()["economics_basis"]
    result = allocate(None)
    assert basis == "entered"
    assert result["economics_incomplete"]["n"] == 0
    assert result["economics_incomplete"]["with_expected_consequence_rm"] == result["economics_incomplete"]["without_expected_consequence_rm"]
    assert any(line["demand_code"] == saved["demand_code"] and line["economics_badge"] == "" for bucket in result["buckets"] for line in bucket["allocations"])


def test_api_create_refuses_a_blank_margin(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    body = DemandCreate(
        demand_type="External",
        customer_or_project="Blank margin",
        customer_type="Main contractor",
        plant_id=1,
        product_id=1,
        required_date="2026-10-09",
        requested_quantity=10,
        confirmed_quantity=10,
        demand_status="Firm",
        confidence_level="Confirmed",
        contribution_margin=0,
        contractual_penalty=0,
        delay_days_if_unserved=0,
        delay_cost_per_day=0,
        source="Manual",
    )
    with pytest.raises(HTTPException) as denied:
        create_demand(body, "scheduler")
    assert denied.value.status_code == 400


def test_lori_quantity_stays_empty(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    plants, products = catalogue()
    prepared = prepare_intake("10 lori grade 50 Isnin", "WhatsApp", plants, products, as_of="2026-09-24", force_mode="regex")
    assert prepared["draft"]["requested_quantity"] is None
    assert any("not converted" in warning for warning in prepared["warnings"])


def test_two_orders_and_the_sunway_revision(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    plants, products = catalogue()
    two = prepare_intake(
        "120 m3 G40 Shah Alam 9 Oct 2026 for Gamuda and 40 m3 G50 Pasir Gudang 16 Oct 2026 for Sunway",
        "Email intake",
        plants,
        products,
        as_of="2026-09-24",
        force_mode="regex",
    )
    assert two["draft_count"] == 2
    assert len(two["drafts"]) == 2
    assert two["draft"]["requested_quantity"] == 120
    with connect() as conn:
        revised = prepare_intake(
            "Please revise the Sunway Puteri Cove order to 180 m3 Grade 40 at Shah Alam Works on 8 Oct 2026.",
            "Email intake",
            plants,
            products,
            as_of="2026-09-24",
            conn=conn,
            force_mode="regex",
        )
    assert "Sunway" in revised["draft"]["customer_or_project"]
    assert revised["match"]["demand_code"] == "EXT-SUNWAY"


def test_queue_is_sorted_and_links_carry_the_decision(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    opened = start_demo("Demo scheduler")
    commercial = queue_for("commercial_owner", "Farah Lim")
    planner = queue_for("project_planner", "Nur Aina")
    scheduler = queue_for("scheduler", "Demo scheduler")
    supervisor = queue_for("plant_supervisor", "")
    assert any(f"decision={opened['id']}" in item["path"] for item in commercial["items"])
    assert any(f"decision={opened['id']}" in item["path"] for item in planner["items"])
    assert any(item["kind"] == "signoff" and f"decision={opened['id']}" in item["path"] for item in scheduler["items"])
    assert any(item["kind"] == "stock_count" for item in supervisor["items"])
    deadlines = [item["deadline"] for item in scheduler["items"]]
    assert deadlines == sorted(deadlines)
    other = queue_for("commercial_owner", "Someone Else")
    assert all(f"decision={opened['id']}" not in item["path"] for item in other["items"])


def test_simulate_expiry_calls_the_real_default_and_reset_keeps_the_book(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    with connect() as conn:
        before = conn.execute("SELECT COUNT(*) AS n FROM demands").fetchone()["n"]
    opened = start_demo("Demo scheduler")
    first = expire_demo()
    second = expire_demo()
    assert opened["id"] in first["defaulted"]
    assert opened["id"] not in second["defaulted"]
    with connect() as conn:
        status = conn.execute("SELECT status, demo FROM decisions WHERE id = ?", (opened["id"],)).fetchone()
    assert status["status"] == "defaulted"
    assert status["demo"] == 1
    reset_demo()
    with connect() as conn:
        gone = conn.execute("SELECT COUNT(*) AS n FROM decisions WHERE demo = 1").fetchone()["n"]
        after = conn.execute("SELECT COUNT(*) AS n FROM demands").fetchone()["n"]
    assert gone == 0
    assert after == before
