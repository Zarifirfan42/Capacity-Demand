"""HTTP API for the Capacity & Demand Intelligence prototype."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.auth import auth_status, require_admin, require_roles, require_writer
from app.db import connect, fetch_all, fetch_one, persistence_backend, reset_data
from app.economics import ASSUMPTIONS, HORIZON_END, HORIZON_START
from app.engine import (
    allocate,
    apply_scenario,
    assess_fragility,
    capacity_view,
    control_tower,
    feasibility,
    load_world,
    parse_user_date,
    score_targets,
    solve_bucket,
    value_of_information,
)
from app.extractor import SAMPLE_EMAIL, SAMPLE_OCR
from app.intake import confirm_intake, intake_metrics, intake_status, prepare_intake
from app.forecast import build_forecast
from app.impact import build_impact
from app.measurement import build_illustration, build_measurement
from app.operations import (
    assert_replaceable,
    buffer_days,
    log_replacement,
    plant_mode,
    release_commitment,
    schedule_commitment,
    set_buffer_days,
    set_plant_mode,
    validate_actuals,
    write_commitment,
)
from app.quality import assess
from app.seed import seed

REASON_CATEGORIES = (
    "Customer commitment",
    "Project criticality",
    "Contractual obligation",
    "Operational constraint",
    "Management decision",
    "Data issue",
    "Other",
)

app = FastAPI(title="Capacity & Demand Intelligence", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_ready = False


@app.on_event("startup")
def _startup() -> None:
    global _ready
    seed()
    _ready = True


@app.middleware("http")
async def _seed_once(request, call_next):
    """Serverless instances may skip the startup hook. Seed before the first call."""
    global _ready
    if not _ready:
        seed()
        _ready = True
    return await call_next(request)


class InventoryAdjustment(BaseModel):
    plant_id: int
    product_id: int
    on_hand_delta_m3: float


class DemandAdjustment(BaseModel):
    demand_id: int
    requested_quantity: float | None = None
    required_date: str | None = None
    contribution_margin: float | None = None
    contractual_penalty: float | None = None
    delay_days_if_unserved: float | None = None
    delay_cost_per_day: float | None = None
    project_criticality: str | None = None
    confidence_level: Literal["Confirmed", "Probable", "Forecast"] | None = None
    unit_expected_factor: float | None = Field(default=None, ge=0.5, le=1.5)


class ScenarioIn(BaseModel):
    name: str = "Scenario"
    capacity_factor: float = Field(default=1.0, ge=0.5, le=1.5)
    plant_id: int | None = None
    product_id: int | None = None
    inventory_adjustments: list[InventoryAdjustment] = []
    demand_adjustments: list[DemandAdjustment] = []


class CompareIn(BaseModel):
    scenarios: list[ScenarioIn] = Field(min_length=1, max_length=3)


class ExtractIn(BaseModel):
    text: str = ""
    source: Literal["Email intake", "Simulated OCR", "WhatsApp", "Manual"] = "Email intake"
    as_of: str | None = None
    image_base64: str | None = None


class ConfirmIn(BaseModel):
    action: Literal["save_new", "apply_change", "cancel", "manual"]
    source: Literal["Email intake", "Simulated OCR", "WhatsApp", "Manual"] = "Manual"
    input_hash: str = ""
    mode: str = "manual"
    extractor: str = "manual"
    model: str = ""
    latency_ms: int = 0
    intent: str = "new"
    proposed: dict = {}
    draft: dict = {}
    matched_demand_id: int | None = None
    confirm_seconds: float = 0
    received_at: str | None = None
    contacts_kept_local: list[str] = []
    accept_ceiling: bool = False
    owner_name: str = ""
    owner_role: str = ""


class DemandCreate(BaseModel):
    demand_type: Literal["Internal", "External"]
    customer_or_project: str = Field(min_length=2, max_length=80)
    customer_type: str = Field(min_length=2, max_length=60)
    plant_id: int
    product_id: int
    required_date: str
    requested_quantity: float = Field(gt=0, le=10000)
    confirmed_quantity: float = Field(ge=0, le=10000)
    demand_status: str = Field(min_length=2, max_length=40)
    confidence_level: Literal["Confirmed", "Probable", "Forecast"]
    contribution_margin: float = Field(ge=0, le=100000000)
    contractual_penalty: float = Field(ge=0, le=100000000)
    project_criticality: str = "n/a"
    delay_days_if_unserved: float = Field(ge=0, le=365)
    delay_cost_per_day: float = Field(ge=0, le=100000000)
    source: str = "Email intake"
    notes: str = ""


class AllocationLineIn(BaseModel):
    demand_id: int
    allocated_quantity: float = Field(ge=0)


class OverrideIn(BaseModel):
    plant_id: int
    product_id: int
    allocations: list[AllocationLineIn]
    scenario: ScenarioIn | None = None


class DecisionIn(OverrideIn):
    username: str = Field(min_length=2, max_length=80)
    override_reason: str = Field(min_length=3, max_length=1000)
    reason_category: str = ""
    chosen_plan: str = ""
    terms_confirmed: bool = False
    replace: bool = False


class ActualLineIn(BaseModel):
    demand_id: int
    delivered_m3: float = Field(ge=0)
    actual_delivery_date: str
    programme_days_lost: float = 0
    penalty_paid_rm: float = 0


class ActualIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    lines: list[ActualLineIn]
    note: str = ""


def _scenario_dict(scenario: ScenarioIn | None) -> dict | None:
    if scenario is None:
        return None
    payload = scenario.model_dump()
    for adj in payload["demand_adjustments"]:
        if adj.get("required_date"):
            adj["required_date"] = parse_user_date(adj["required_date"])
        if adj.get("requested_quantity") is not None and adj["requested_quantity"] <= 0:
            raise HTTPException(status_code=400, detail="Scenario quantity must be greater than zero.")
    return payload


def _targets(world: dict, plant_id: int, product_id: int, lines: list[AllocationLineIn]) -> dict[int, float]:
    demands = [row for row in world["demands"] if row["plant_id"] == plant_id and row["product_id"] == product_id]
    if not demands:
        raise HTTPException(status_code=404, detail="No demand for that plant and product.")
    incoming = {line.demand_id: line.allocated_quantity for line in lines}
    missing = [row["demand_code"] for row in demands if row["id"] not in incoming]
    if missing:
        raise HTTPException(status_code=400, detail=f"Allocation is missing {', '.join(missing)}.")
    extra = set(incoming) - {row["id"] for row in demands}
    if extra:
        raise HTTPException(status_code=400, detail="Allocation includes a demand line from another plant or product.")
    return incoming


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "solver": "CBC linear programme", "persistence": persistence_backend()}


@app.get("/api/meta")
def meta() -> dict:
    world = load_world()
    return {
        "horizon": {"start": HORIZON_START, "end": HORIZON_END},
        "plants": world["plants"],
        "products": world["products"],
        "assumptions": ASSUMPTIONS,
        "confidence_levels": ["Confirmed", "Probable", "Forecast"],
        "demand_types": ["Internal", "External"],
        "criticality_levels": ["Critical", "High", "Medium", "Low", "n/a"],
        "samples": {"ocr": SAMPLE_OCR, "email": SAMPLE_EMAIL},
        "demands": [
            {
                "id": row["id"],
                "demand_code": row["demand_code"],
                "customer_or_project": row["customer_or_project"],
                "demand_type": row["demand_type"],
                "plant_id": row["plant_id"],
                "product_id": row["product_id"],
                "required_date": row["required_date"],
                "requested_quantity": row["requested_quantity"],
                "contribution_margin": row["contribution_margin"],
                "contractual_penalty": row["contractual_penalty"],
                "delay_days_if_unserved": row["delay_days_if_unserved"],
                "delay_cost_per_day": row["delay_cost_per_day"],
                "project_criticality": row["project_criticality"],
                "confidence_level": row["confidence_level"],
            }
            for row in world["demands"]
        ],
    }


@app.get("/api/control-tower")
def get_control_tower() -> dict:
    payload = control_tower()
    with connect() as conn:
        payload["recent_decisions"] = fetch_all(
            conn,
            """
            SELECT id, created_at, username, plant_name, product_name, status, override_reason,
                   consequence_recommended, consequence_final, unserved_recommended, unserved_final
            FROM decisions
            ORDER BY id DESC
            LIMIT 8
            """,
        )
    return payload


@app.get("/api/demands")
def list_demands(
    plant_id: int | None = None,
    product_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    customer: str | None = None,
    demand_status: str | None = None,
    confidence_level: str | None = None,
    demand_type: str | None = None,
) -> dict:
    clauses = ["1=1"]
    params: list = []
    if plant_id:
        clauses.append("d.plant_id = ?")
        params.append(plant_id)
    if product_id:
        clauses.append("d.product_id = ?")
        params.append(product_id)
    if date_from:
        clauses.append("d.required_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("d.required_date <= ?")
        params.append(date_to)
    if customer:
        clauses.append("d.customer_or_project LIKE ?")
        params.append(f"%{customer.strip()}%")
    if demand_status:
        clauses.append("d.demand_status = ?")
        params.append(demand_status)
    if confidence_level:
        clauses.append("d.confidence_level = ?")
        params.append(confidence_level)
    if demand_type:
        clauses.append("d.demand_type = ?")
        params.append(demand_type)
    sql = f"""
        SELECT d.*, p.name AS plant_name, p.code AS plant_code, r.name AS product_name, r.code AS product_code
        FROM demands d
        JOIN plants p ON p.id = d.plant_id
        JOIN products r ON r.id = d.product_id
        WHERE {' AND '.join(clauses)}
        ORDER BY d.required_date, d.id
    """
    with connect() as conn:
        rows = fetch_all(conn, sql, tuple(params))
        statuses = [row["demand_status"] for row in fetch_all(conn, "SELECT DISTINCT demand_status FROM demands ORDER BY 1")]
    internal = sum(row["requested_quantity"] for row in rows if row["demand_type"] == "Internal")
    external = sum(row["requested_quantity"] for row in rows if row["demand_type"] == "External")
    return {
        "rows": rows,
        "count": len(rows),
        "totals": {
            "requested_m3": round(internal + external, 2),
            "internal_m3": round(internal, 2),
            "external_m3": round(external, 2),
            "contribution_margin_rm": round(sum(row["contribution_margin"] for row in rows), 2),
            "contractual_penalty_rm": round(sum(row["contractual_penalty"] for row in rows), 2),
        },
        "statuses": statuses,
    }


@app.get("/api/intake/status")
def get_intake_status() -> dict:
    return intake_status()


@app.post("/api/demands/extract")
def extract(
    body: ExtractIn,
    role: str = Depends(require_writer),
    x_forwarded_for: str | None = Header(default=None),
) -> dict:
    world = load_world()
    client = (x_forwarded_for or "local").split(",")[0].strip() or "local"
    with connect() as conn:
        return prepare_intake(
            body.text,
            body.source,
            world["plants"],
            world["products"],
            as_of=body.as_of,
            role=role,
            client_ip=client,
            conn=conn,
            image_supplied=bool(body.image_base64),
        )


@app.post("/api/intake/confirm")
def intake_confirm(
    body: ConfirmIn,
    role: str = Depends(require_writer),
    x_forwarded_for: str | None = Header(default=None),
) -> dict:
    client = (x_forwarded_for or "local").split(",")[0].strip() or "local"
    with connect() as conn:
        try:
            return confirm_intake(conn, body.model_dump(), role, client)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/demands")
def create_demand(body: DemandCreate, _: str = Depends(require_writer)) -> dict:
    try:
        required = parse_user_date(body.required_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.confirmed_quantity - body.requested_quantity > 0.01:
        raise HTTPException(status_code=400, detail="Confirmed quantity cannot exceed requested quantity.")
    if body.source not in ("Email intake", "Simulated OCR", "WhatsApp", "Manual"):
        raise HTTPException(status_code=400, detail="New lines must come from intake or a manual entry.")
    prefix = "INT" if body.demand_type == "Internal" else "EXT"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        plant = fetch_one(conn, "SELECT id FROM plants WHERE id = ?", (body.plant_id,))
        product = fetch_one(conn, "SELECT id FROM products WHERE id = ?", (body.product_id,))
        if plant is None or product is None:
            raise HTTPException(status_code=400, detail="Plant or product was not recognised.")
        seq = fetch_one(conn, "SELECT COUNT(*) AS n FROM demands WHERE demand_code LIKE ?", (f"{prefix}-IN-%",))
        code = f"{prefix}-IN-{(seq['n'] if seq else 0) + 1:03d}"
        conn.execute(
            """
            INSERT INTO demands(
                demand_code, demand_type, customer_or_project, customer_type, plant_id, product_id,
                required_date, requested_quantity, confirmed_quantity, demand_status, confidence_level,
                contribution_margin, contractual_penalty, project_criticality, delay_days_if_unserved,
                delay_cost_per_day, source, notes, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                body.demand_type,
                body.customer_or_project.strip(),
                body.customer_type.strip(),
                body.plant_id,
                body.product_id,
                required,
                body.requested_quantity,
                body.confirmed_quantity,
                body.demand_status.strip(),
                body.confidence_level,
                body.contribution_margin,
                body.contractual_penalty,
                body.project_criticality,
                body.delay_days_if_unserved,
                body.delay_cost_per_day,
                body.source,
                body.notes.strip(),
                now,
            ),
        )
        row = fetch_one(conn, "SELECT * FROM demands WHERE demand_code = ?", (code,))
    return {"demand": row}


@app.delete("/api/demands/{demand_id}")
def delete_demand(demand_id: int, _: str = Depends(require_writer)) -> dict:
    with connect() as conn:
        row = fetch_one(conn, "SELECT * FROM demands WHERE id = ?", (demand_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="Demand line not found.")
        if "-IN-" not in row["demand_code"]:
            raise HTTPException(status_code=400, detail="Source-system demand stays in the book. Remove only lines added in this prototype.")
        conn.execute("DELETE FROM demands WHERE id = ?", (demand_id,))
    return {"deleted": demand_id}


@app.get("/api/capacity")
def get_capacity(plant_id: int = Query(...), product_id: int = Query(...)) -> dict:
    world = load_world()
    if not any(row["id"] == plant_id for row in world["plants"]):
        raise HTTPException(status_code=404, detail="Plant not found.")
    if not any(row["id"] == product_id for row in world["products"]):
        raise HTTPException(status_code=404, detail="Product not found.")
    return capacity_view(plant_id, product_id)


def _known_bucket(plant_id: int, product_id: int) -> None:
    world = load_world()
    if not any(row["id"] == plant_id for row in world["plants"]):
        raise HTTPException(status_code=404, detail="Plant not found.")
    if not any(row["id"] == product_id for row in world["products"]):
        raise HTTPException(status_code=404, detail="Product not found.")


@app.get("/api/contract-flips")
def get_contract_flips(plant_id: int = Query(...), product_id: int = Query(...)) -> dict:
    _known_bucket(plant_id, product_id)
    report = value_of_information()
    lines = [row for row in report["lines"] if row["plant_id"] == plant_id and row["product_id"] == product_id and row["allocation_changed"] and row["types_unverified"]]
    return {"lines": lines, "order_count_note": report["order_count_note"]}


@app.get("/api/fragility")
def get_fragility(plant_id: int = Query(...), product_id: int = Query(...)) -> dict:
    _known_bucket(plant_id, product_id)
    return assess_fragility(plant_id, product_id)


@app.post("/api/allocate")
def run_allocation(scenario: ScenarioIn | None = None) -> dict:
    try:
        return allocate(_scenario_dict(scenario))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/allocate/override")
def preview_override(body: OverrideIn) -> dict:
    try:
        scenario = _scenario_dict(body.scenario)
        world = apply_scenario(load_world(), scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    targets = _targets(world, body.plant_id, body.product_id, body.allocations)
    fit = feasibility(world, body.plant_id, body.product_id, targets)
    scored = score_targets(world, body.plant_id, body.product_id, targets) if fit["feasible"] else None
    bucket = solve_bucket(world, body.plant_id, body.product_id)
    recommended = {
        "expected_consequence_rm": bucket["expected_consequence_rm"],
        "gross_consequence_rm": bucket["gross_consequence_rm"],
        "unserved_m3": bucket["unserved_m3"],
        "margin_at_risk_rm": bucket["margin_at_risk_rm"],
        "programme_days": bucket["programme_days"],
    }
    delta = None
    if scored is not None:
        delta = {
            "expected_consequence_rm": round(scored["expected_consequence_rm"] - recommended["expected_consequence_rm"], 2),
            "gross_consequence_rm": round(scored["gross_consequence_rm"] - recommended["gross_consequence_rm"], 2),
            "unserved_m3": round(scored["unserved_m3"] - recommended["unserved_m3"], 2),
            "programme_days": round(scored["programme_days"] - recommended["programme_days"], 2),
        }
    changed = False
    by_id = {line["demand_id"]: line for line in bucket["allocations"]}
    for demand_id, allocated in targets.items():
        if abs(allocated - by_id[demand_id]["allocated_quantity"]) > 0.1:
            changed = True
            break
    return {
        "feasible": fit["feasible"],
        "message": fit["message"],
        "changed_from_recommendation": changed,
        "edited": None if scored is None else {"totals": {key: value for key, value in scored.items() if key != "allocations"}, "allocations": scored["allocations"]},
        "recommended": recommended,
        "delta_versus_recommendation": delta,
    }


@app.post("/api/decisions")
def record_decision(body: DecisionIn, _: str = Depends(require_writer)) -> dict:
    try:
        scenario = _scenario_dict(body.scenario)
        world = apply_scenario(load_world(), scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        previous = assert_replaceable(body.plant_id, body.product_id, body.replace)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if previous is not None:
        from app.governance import release_hold

        release_hold(previous)
        release_commitment(previous)
        world = apply_scenario(load_world(), scenario)
    targets = _targets(world, body.plant_id, body.product_id, body.allocations)
    fit = feasibility(world, body.plant_id, body.product_id, targets)
    if not fit["feasible"]:
        if previous is not None:
            write_commitment(body.plant_id, body.product_id, json.loads(previous.get("committed_json") or "{}"))
            with connect() as conn:
                conn.execute("UPDATE decisions SET status = ? WHERE id = ?", (previous["status"], previous["id"]))
        raise HTTPException(status_code=400, detail=fit["message"])
    bucket = solve_bucket(world, body.plant_id, body.product_id)
    scored = score_targets(world, body.plant_id, body.product_id, targets)
    by_id = {line["demand_id"]: line for line in bucket["allocations"]}
    changed = any(abs(allocated - by_id[demand_id]["allocated_quantity"]) > 0.1 for demand_id, allocated in targets.items())
    reason = body.override_reason.strip()
    if changed and len(reason) < 8:
        raise HTTPException(status_code=400, detail="A modified allocation needs a reason of at least a short sentence.")
    category = body.reason_category.strip()
    if changed:
        if not category:
            category = "Other"
        if category not in REASON_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Override category must be one of: {', '.join(REASON_CATEGORIES)}.")
    else:
        reason = "Accepted as recommended"
        category = ""
    open_terms = [
        row
        for row in value_of_information()["lines"]
        if row["plant_id"] == body.plant_id
        and row["product_id"] == body.product_id
        and row["allocation_changed"]
        and row["types_unverified"]
    ]
    if open_terms and not body.terms_confirmed:
        names = ", ".join(row["statement"] for row in open_terms)
        raise HTTPException(
            status_code=400,
            detail=f"A commercial owner has to confirm the unverified contract term before sign-off. {names}",
        )
    fragile = False
    if bucket.get("comparison", {}).get("plans_differ"):
        fragile = assess_fragility(body.plant_id, body.product_id)["fragile"]
        if fragile and body.chosen_plan not in {"typed", "proportional"}:
            raise HTTPException(
                status_code=400,
                detail="This recommendation is fragile and the two plans differ. Choose the typed plan or the proportional comparison.",
            )
    from app.governance import affected_owners, deadline_for, fallback_owners, place_hold, review_required

    owners = affected_owners(bucket["allocations"], scored["allocations"])
    needs_review = review_required(bucket["decision_review"]["triggers"], changed, fragile, bool(open_terms))
    if needs_review and not owners:
        owners = fallback_owners(scored["allocations"])
    needs_signoff = needs_review and bool(owners)
    if needs_signoff:
        status = "awaiting_signoff"
    else:
        status = "modified" if changed else "approved"
    first_due = min((str(line["required_date"]) for line in scored["allocations"]), default=None)
    decision_deadline = deadline_for(datetime.now(timezone.utc), first_due) if needs_signoff else None
    recommended_payload = {
        "scenario_name": world.get("scenario_name"),
        "allocations": bucket["allocations"],
        "totals": {
            "unserved_m3": bucket["unserved_m3"],
            "margin_at_risk_rm": bucket["margin_at_risk_rm"],
            "penalty_at_risk_rm": bucket["penalty_at_risk_rm"],
            "delay_cost_rm": bucket["delay_cost_rm"],
            "gross_consequence_rm": bucket["gross_consequence_rm"],
            "expected_consequence_rm": bucket["expected_consequence_rm"],
            "programme_days": bucket["programme_days"],
            "consequence_avoided_rm": bucket["consequence_avoided_rm"],
        },
    }
    final_payload = {
        "scenario_name": world.get("scenario_name"),
        "allocations": scored["allocations"],
        "totals": {key: value for key, value in scored.items() if key != "allocations"},
        "reason": reason,
    }
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        plant = fetch_one(conn, "SELECT name FROM plants WHERE id = ?", (body.plant_id,))
        product = fetch_one(conn, "SELECT name FROM products WHERE id = ?", (body.product_id,))
        cursor = conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final,
                reason_category, chosen_plan, terms_confirmed
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                body.username.strip(),
                body.plant_id,
                body.product_id,
                plant["name"],
                product["name"],
                json.dumps(recommended_payload),
                json.dumps(final_payload),
                reason,
                status,
                bucket["expected_consequence_rm"],
                scored["expected_consequence_rm"],
                bucket["unserved_m3"],
                scored["unserved_m3"],
                category,
                body.chosen_plan,
                1 if body.terms_confirmed else 0,
            ),
        )
        decision_id = cursor.lastrowid
        saved = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if needs_signoff:
        schedule = schedule_commitment(world, body.plant_id, body.product_id, targets)
        place_hold(body.plant_id, body.product_id, schedule)
        with connect() as conn:
            conn.execute(
                """
                UPDATE decisions
                SET deadline = ?, required_signatories_json = ?, hold_json = ?, preparer_role = 'scheduler'
                WHERE id = ?
                """,
                (decision_deadline, json.dumps(owners), json.dumps(schedule), decision_id),
            )
            saved = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    elif plant_mode(body.plant_id) == "pilot":
        schedule = schedule_commitment(world, body.plant_id, body.product_id, targets)
        write_commitment(body.plant_id, body.product_id, schedule)
        with connect() as conn:
            conn.execute("UPDATE decisions SET committed_json = ? WHERE id = ?", (json.dumps(schedule), decision_id))
            saved = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if previous is not None:
        log_replacement(int(previous["id"]), int(decision_id), body.username.strip())
    return {"decision": _hydrate_decision(saved), "status": status, "reason_categories": list(REASON_CATEGORIES)}


def _hydrate_decision(row: dict) -> dict:
    row["recommended"] = json.loads(row.pop("recommended_json"))
    row["final"] = json.loads(row.pop("final_json"))
    raw_actual = row.get("actual_json")
    row["actual"] = json.loads(raw_actual) if raw_actual else None
    row.pop("actual_json", None)
    raw_signatories = row.get("required_signatories_json")
    if raw_signatories is not None:
        row["required_signatories"] = json.loads(raw_signatories or "[]")
        row.pop("required_signatories_json", None)
    return row


@app.get("/api/decisions")
def list_decisions(
    plant_id: int | None = None,
    product_id: int | None = None,
    status: str | None = None,
    reason_category: str | None = None,
) -> dict:
    clauses = ["1=1"]
    params: list = []
    if plant_id:
        clauses.append("plant_id = ?")
        params.append(plant_id)
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if reason_category:
        clauses.append("reason_category = ?")
        params.append(reason_category)
    with connect() as conn:
        rows = fetch_all(conn, f"SELECT * FROM decisions WHERE {' AND '.join(clauses)} ORDER BY id DESC", tuple(params))
    return {"rows": [_hydrate_decision(row) for row in rows], "reason_categories": list(REASON_CATEGORIES)}


@app.post("/api/decisions/{decision_id}/actual")
def record_actual(decision_id: int, body: ActualIn, _: str = Depends(require_writer)) -> dict:
    """Record delivered quantities and cash. This locks the decision."""
    with connect() as conn:
        row = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="Decision not found.")
        final = json.loads(row["final_json"])
        try:
            lines = validate_actuals(final.get("allocations", []), [line.model_dump() for line in body.lines])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = {
            "note": body.note.strip(),
            "lines": lines,
            "entered_by": body.username.strip(),
            "basis": "Entered from the dispatch log, site diary, and commercial register. Synthetic until those documents exist.",
        }
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE decisions
            SET actual_json = ?, actual_note = ?, actual_recorded_at = ?, actual_username = ?
            WHERE id = ?
            """,
            (json.dumps(payload), body.note.strip(), now, body.username.strip(), decision_id),
        )
        saved = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    from app.governance import mark_constraints_bound

    mark_constraints_bound(decision_id)
    return {"decision": _hydrate_decision(saved)}


@app.get("/api/quality")
def quality() -> dict:
    return assess()


@app.get("/api/planning-view")
def planning_view() -> dict:
    world = load_world()
    buckets = {"Confirmed": 0.0, "Probable": 0.0, "Forecast": 0.0}
    for row in world["demands"]:
        level = row["confidence_level"]
        buckets[level] = buckets.get(level, 0.0) + float(row["requested_quantity"])
    return {
        "by_confidence_m3": {key: round(value, 2) for key, value in buckets.items()},
        "planning_view_m3": round(sum(buckets.values()), 2),
        "certainty_note": "Confirmed, Probable, and Forecast are planning-certainty classes. The 100%, 75%, and 45% figures are judgemental weights, not calibrated probabilities.",
        "forecast_method": "See /api/forecast for the synthetic history and the October planning forecast. That forecast is not copied into this order book.",
    }


class AdjustmentIn(BaseModel):
    plant_id: int
    product_id: int
    adjustment_m3: float
    note: str = ""


@app.get("/api/forecast")
def forecast() -> dict:
    return build_forecast()


@app.post("/api/forecast/adjustment")
def forecast_adjustment(body: AdjustmentIn, _: str = Depends(require_writer)) -> dict:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO forecast_adjustments(plant_id, product_id, month_start, adjustment_m3, note)
            VALUES(?, ?, '2026-10-01', ?, ?)
            ON CONFLICT(plant_id, product_id, month_start) DO UPDATE SET
                adjustment_m3 = excluded.adjustment_m3,
                note = excluded.note
            """,
            (body.plant_id, body.product_id, body.adjustment_m3, body.note.strip()),
        )
    return build_forecast()


@app.post("/api/scenarios/compare")
def compare_scenarios(body: CompareIn) -> dict:
    try:
        runs = [allocate(_scenario_dict(scenario)) for scenario in body.scenarios]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    comparison = []
    for run in runs:
        totals = run["totals"]
        comparison.append(
            {
                "name": run["scenario_name"],
                "notes": run["scenario_notes"],
                "unserved_m3": totals["dated_shortfall_m3"],
                "allocated_m3": totals["allocated_m3"],
                "margin_at_risk_rm": totals["margin_at_risk_rm"],
                "penalty_at_risk_rm": totals["penalty_at_risk_rm"],
                "delay_cost_rm": totals["delay_cost_rm"],
                "programme_days": totals["programme_days"],
                "gross_consequence_rm": totals["gross_consequence_rm"],
                "expected_consequence_rm": totals["expected_consequence_rm"],
                "available_supply_m3": totals["available_supply_m3"],
                "total_demand_m3": totals["total_demand_m3"],
            }
        )
    baseline = comparison[0]["expected_consequence_rm"] if comparison else 0
    for row in comparison:
        row["delta_versus_first_rm"] = round(row["expected_consequence_rm"] - baseline, 2)
    return {
        "comparison": comparison,
        "runs": [
            {
                "scenario_name": run["scenario_name"],
                "scenario_notes": run["scenario_notes"],
                "totals": run["totals"],
                "buckets": [
                    {
                        "plant_id": bucket["plant_id"],
                        "product_id": bucket["product_id"],
                        "plant_name": bucket["plant_name"],
                        "product_name": bucket["product_name"],
                        "constrained": bucket["constrained"],
                        "available_supply_m3": bucket["available_supply_m3"],
                        "total_demand_m3": bucket["total_demand_m3"],
                        "shortfall_m3": bucket["shortfall_m3"],
                        "expected_consequence_rm": bucket["expected_consequence_rm"],
                        "gross_consequence_rm": bucket["gross_consequence_rm"],
                        "margin_at_risk_rm": bucket["margin_at_risk_rm"],
                        "programme_days": bucket["programme_days"],
                        "allocations": [
                            {
                                "demand_id": line["demand_id"],
                                "demand_code": line["demand_code"],
                                "customer_or_project": line["customer_or_project"],
                                "demand_type": line["demand_type"],
                                "requested_quantity": line["requested_quantity"],
                                "allocated_quantity": line["allocated_quantity"],
                                "unserved_quantity": line["unserved_quantity"],
                                "expected_consequence_rm": line["expected_consequence_rm"],
                                "gross_consequence_rm": line["gross_consequence_rm"],
                                "reason": line["reason"],
                            }
                            for line in bucket["allocations"]
                        ],
                        "explanation": bucket["explanation"],
                    }
                    for bucket in run["buckets"]
                    if bucket["constrained"] or bucket["total_demand_m3"] > 0
                ],
            }
            for run in runs
        ],
    }


@app.get("/api/impact")
def impact() -> dict:
    return build_impact()


@app.get("/api/auth-status")
def get_auth_status() -> dict:
    return auth_status()


@app.get("/api/operations")
def operations_state() -> dict:
    world = load_world()
    return {
        "buffer_days": buffer_days(),
        "persistence": persistence_backend(),
        "plants": [{"id": plant["id"], "name": plant["name"], "mode": plant_mode(plant["id"])} for plant in world["plants"]],
        "auth": auth_status(),
    }


class ModeIn(BaseModel):
    mode: Literal["shadow", "pilot"]


class BufferIn(BaseModel):
    buffer_days: int = Field(ge=0, le=14)


class InformalIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    plant_id: int
    product_id: int
    allocations: list[AllocationLineIn]


class SnapshotIn(BaseModel):
    plant_id: int
    product_id: int
    as_of_date: str
    on_hand: float = Field(ge=0)
    safety_stock: float = Field(ge=0)
    entered_by: str = Field(min_length=2, max_length=80)


class ExpediteIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    plant_id: int
    product_id: int
    demand_id: int
    customer_or_project: str
    decision: Literal["approve", "decline"]
    emergency_rm_per_m3: float = Field(ge=0)
    available_volume_m3: float = Field(ge=0)
    cost_rm: float = Field(ge=0)
    penalty_would_apply_rm: float = Field(ge=0)
    delivered_in_full: bool | None = None
    on_time: bool | None = None
    penalty_paid_rm: float | None = None
    note: str = ""


@app.post("/api/plants/{plant_id}/mode")
def update_mode(plant_id: int, body: ModeIn, _: str = Depends(require_roles("plant_supervisor"))) -> dict:
    try:
        mode = set_plant_mode(plant_id, body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"plant_id": plant_id, "mode": mode}


@app.post("/api/buffer-days")
def update_buffer(body: BufferIn, _: str = Depends(require_roles("plant_supervisor"))) -> dict:
    return {"buffer_days": set_buffer_days(body.buffer_days)}


@app.post("/api/informal-plans")
def record_informal(body: InformalIn, _: str = Depends(require_writer)) -> dict:
    world = load_world()
    targets = _targets(world, body.plant_id, body.product_id, body.allocations)
    fit = feasibility(world, body.plant_id, body.product_id, targets)
    if not fit["feasible"]:
        raise HTTPException(status_code=400, detail=fit["message"])
    bucket = solve_bucket(world, body.plant_id, body.product_id, include_comparison=False, include_expedite=False)
    recommended_targets = {int(line["demand_id"]): float(line["allocated_quantity"]) for line in bucket["allocations"]}
    informal_expected = float(score_targets(world, body.plant_id, body.product_id, targets)["expected_consequence_rm"])
    recommendation_expected = float(bucket["expected_consequence_rm"])
    paired = round(informal_expected - recommendation_expected, 2)
    payload = {
        "allocations": [{"demand_id": demand_id, "allocated_quantity": qty} for demand_id, qty in targets.items()],
    }
    recommendation = {
        "allocations": [{"demand_id": demand_id, "allocated_quantity": qty} for demand_id, qty in recommended_targets.items()],
        "expected_consequence_rm": recommendation_expected,
    }
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO informal_plans(
                created_at, username, plant_id, product_id, quantities_json, recommendation_json,
                informal_expected_rm, recommendation_expected_rm, paired_gap_rm
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                body.username.strip(),
                body.plant_id,
                body.product_id,
                json.dumps(payload),
                json.dumps(recommendation),
                informal_expected,
                recommendation_expected,
                paired,
            ),
        )
        plan_id = cursor.lastrowid
    return {
        "id": plan_id,
        "paired_gap_rm": paired,
        "informal_expected_rm": informal_expected,
        "recommendation_expected_rm": recommendation_expected,
        "definition": "Modelled consequence of the informal plan minus the modelled recommendation, on this same book. Not an observed outcome.",
    }


@app.get("/api/informal-plans")
def list_informal(plant_id: int | None = None, product_id: int | None = None) -> dict:
    clauses = ["1=1"]
    params: list = []
    if plant_id:
        clauses.append("plant_id = ?")
        params.append(plant_id)
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    with connect() as conn:
        rows = fetch_all(conn, f"SELECT * FROM informal_plans WHERE {' AND '.join(clauses)} ORDER BY id DESC", tuple(params))
    for row in rows:
        row["quantities"] = json.loads(row.pop("quantities_json"))
        row["recommendation"] = json.loads(row.pop("recommendation_json"))
    return {"rows": rows}


@app.post("/api/inventory-snapshots")
def record_snapshot(body: SnapshotIn, _: str = Depends(require_roles("plant_supervisor"))) -> dict:
    try:
        as_of = parse_user_date(body.as_of_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO inventory_snapshots(plant_id, product_id, as_of_date, on_hand, safety_stock, entered_by, entered_at, synthetic)
            VALUES(?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(plant_id, product_id, as_of_date) DO UPDATE SET
                on_hand = excluded.on_hand,
                safety_stock = excluded.safety_stock,
                entered_by = excluded.entered_by,
                entered_at = excluded.entered_at
            """,
            (body.plant_id, body.product_id, as_of, body.on_hand, body.safety_stock, body.entered_by.strip(), now),
        )
    return {"as_of_date": as_of, "entered_by": body.entered_by.strip(), "entered_at": now}


@app.post("/api/expedite-decisions")
def record_expedite(body: ExpediteIn, _: str = Depends(require_roles("plant_supervisor"))) -> dict:
    from app.governance import settings as governance_settings

    decision = body.decision
    limit = governance_settings()["expedite_limit_rm"]
    if body.decision == "approve" and body.cost_rm > limit:
        decision = "awaiting_review"
    estimated = None
    if body.delivered_in_full and body.on_time and body.penalty_paid_rm is not None and decision == "approve":
        estimated = round(max(0.0, body.penalty_would_apply_rm - body.penalty_paid_rm), 2)
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO expedite_decisions(
                created_at, username, plant_id, product_id, demand_id, customer_or_project, decision,
                emergency_rm_per_m3, available_volume_m3, cost_rm, penalty_would_apply_rm,
                delivered_in_full, on_time, penalty_paid_rm, estimated_avoided_rm, note
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                body.username.strip(),
                body.plant_id,
                body.product_id,
                body.demand_id,
                body.customer_or_project.strip(),
                decision,
                body.emergency_rm_per_m3,
                body.available_volume_m3,
                body.cost_rm,
                body.penalty_would_apply_rm,
                None if body.delivered_in_full is None else int(body.delivered_in_full),
                None if body.on_time is None else int(body.on_time),
                body.penalty_paid_rm,
                estimated,
                body.note.strip(),
            ),
        )
        row_id = cursor.lastrowid
    return {
        "id": row_id,
        "estimated_avoided_rm": estimated,
        "label": "estimated avoided",
        "status": decision,
        "expedite_limit_rm": limit,
        "note": "Above the plant supervisor's limit, the project planner and the commercial owner both have to approve.",
    }


@app.get("/api/measurement")
def measurement() -> dict:
    from app.governance import governance_metrics

    payload = build_measurement()
    payload["governance"] = governance_metrics()
    with connect() as conn:
        payload["intake"] = intake_metrics(conn)
    return payload


class SignoffIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    plan_choice: Literal["recommended", "proposed"]
    reason: str = Field(min_length=8)


class ConstraintIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    kind: Literal["cap", "reserve", "days"]
    quantity: float = Field(gt=0)
    evidence_type: str
    note: str = ""
    demand_id: int | None = None


class ConsultIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    note: str = Field(min_length=8)


class DeclarationIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    demand_id: int
    declared_rm_per_day: float = Field(ge=0)
    source: str
    reason: str = Field(min_length=8)


class ProposalIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    key: str
    proposed_value: str
    reason: str = Field(min_length=8)


class ApprovalIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)


def _governance_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/api/governance")
def governance_state() -> dict:
    from app.governance import governance_metrics, settings as governance_settings

    with connect() as conn:
        proposals = fetch_all(conn, "SELECT * FROM setting_proposals ORDER BY id DESC")
        approvals = fetch_all(conn, "SELECT * FROM setting_approvals ORDER BY id")
        declarations = fetch_all(conn, "SELECT * FROM delay_declarations ORDER BY id DESC")
        ledger = fetch_all(conn, "SELECT * FROM constraint_ledger ORDER BY id DESC")
    return {
        "settings": governance_settings(),
        "metrics": governance_metrics(),
        "proposals": proposals,
        "approvals": approvals,
        "declarations": declarations,
        "ledger": ledger,
        "next_cycle": "2026-11-01",
        "tie_break": "When the two owners choose different plans and neither records a constraint, the lower expected consequence applies. That makes the model the tie-breaker on purpose.",
    }


@app.post("/api/decisions/{decision_id}/signoff")
def post_signoff(
    decision_id: int,
    body: SignoffIn,
    role: str = Depends(require_roles("project_planner", "commercial_owner")),
) -> dict:
    from app.governance import record_signoff

    try:
        return record_signoff(decision_id, role, body.username, body.plan_choice, body.reason)
    except ValueError as exc:
        raise _governance_error(exc) from exc


@app.post("/api/decisions/{decision_id}/constraints")
def post_constraint(
    decision_id: int,
    body: ConstraintIn,
    role: str = Depends(require_roles("project_planner", "commercial_owner")),
) -> dict:
    from app.governance import add_constraint

    try:
        return add_constraint(decision_id, body.username, role, body.kind, body.quantity, body.evidence_type, body.note, body.demand_id)
    except ValueError as exc:
        raise _governance_error(exc) from exc


@app.post("/api/decisions/{decision_id}/consultation")
def post_consultation(
    decision_id: int,
    body: ConsultIn,
    _: str = Depends(require_roles("plant_supervisor")),
) -> dict:
    from app.governance import consult

    try:
        return consult(decision_id, body.username, body.note)
    except ValueError as exc:
        raise _governance_error(exc) from exc


@app.post("/api/delay-declarations")
def post_declaration(body: DeclarationIn, _: str = Depends(require_roles("project_planner"))) -> dict:
    from app.governance import declare_delay

    try:
        return declare_delay(body.demand_id, body.username, body.declared_rm_per_day, body.source, body.reason)
    except ValueError as exc:
        raise _governance_error(exc) from exc


@app.post("/api/settings/proposals")
def post_proposal(body: ProposalIn, role: str = Depends(require_roles("plant_supervisor", "project_planner", "commercial_owner"))) -> dict:
    from app.governance import propose_setting

    try:
        return propose_setting(body.key, body.proposed_value, body.reason, body.username, role)
    except ValueError as exc:
        raise _governance_error(exc) from exc


@app.post("/api/settings/proposals/{proposal_id}/approve")
def post_approval(
    proposal_id: int,
    body: ApprovalIn,
    role: str = Depends(require_roles("plant_supervisor", "project_planner", "commercial_owner")),
) -> dict:
    from app.governance import approve_setting

    try:
        return approve_setting(proposal_id, role, body.username)
    except ValueError as exc:
        raise _governance_error(exc) from exc


@app.post("/api/expedite-decisions/{expedite_id}/approve")
def approve_expedite(
    expedite_id: int,
    body: ApprovalIn,
    role: str = Depends(require_roles("project_planner", "commercial_owner")),
) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        row = fetch_one(conn, "SELECT * FROM expedite_decisions WHERE id = ?", (expedite_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="Expedite record not found.")
        if row["decision"] != "awaiting_review":
            raise HTTPException(status_code=400, detail="This expedite spend is not waiting for the two-party review.")
        conn.execute(
            "INSERT INTO expedite_approvals(expedite_id, role, username, created_at) VALUES(?, ?, ?, ?) ON CONFLICT(expedite_id, role) DO NOTHING",
            (expedite_id, role, body.username.strip(), now),
        )
        seats = {item["role"] for item in fetch_all(conn, "SELECT role FROM expedite_approvals WHERE expedite_id = ?", (expedite_id,))}
        if {"project_planner", "commercial_owner"} <= seats:
            conn.execute("UPDATE expedite_decisions SET decision = 'approve' WHERE id = ?", (expedite_id,))
            status = "approve"
        else:
            status = "awaiting_review"
    return {"id": expedite_id, "status": status}


@app.get("/api/cron/governance")
def cron_governance(authorization: str | None = Header(default=None)) -> dict:
    from app.governance import apply_expired_defaults

    secret = os.getenv("CDI_CRON_SECRET") or os.getenv("CRON_SECRET")
    if secret and authorization != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Cron secret required.")
    return apply_expired_defaults()


@app.post("/api/admin/reset")
def admin_reset(_: str = Depends(require_admin)) -> dict:
    with connect() as conn:
        reset_data(conn)
    seed()
    return {"status": "reset", "label": "Synthetic demo data restored. Recorded decisions and actuals were deleted by an admin."}


# The built UI is served by Vercel. API routes stay on this app and take priority.
_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if hasattr(app, "frontend"):
    app.frontend("/", directory=str(_frontend_dist), fallback="index.html", check_dir=False)
