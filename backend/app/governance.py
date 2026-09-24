"""Sign-off, constraints, declared delay cost, and threshold proposals.

The October book is unchanged until a person records a decision. Opening
settings are the review lines already on the page.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.db import connect, fetch_all, fetch_one, set_meta
from app.economics import (
    REVIEW_EXPECTED_CONSEQUENCE_RM,
    REVIEW_PENALTY_RM,
    REVIEW_PROGRAMME_DAYS,
    round_rm,
)

OWNERS = {
    "INT-MERDEKA": ("Nur Aina", "project_planner"),
    "INT-ELMINA": ("Nur Aina", "project_planner"),
    "INT-PENANG": ("Hafiz Rahman", "project_planner"),
    "INT-BORNEO": ("Hafiz Rahman", "project_planner"),
    "INT-SILO": ("Siti Kamal", "project_planner"),
    "INT-RTS": ("Siti Kamal", "project_planner"),
    "INT-KWASA": ("Daniel Ong", "project_planner"),
    "INT-ECRL": ("Daniel Ong", "project_planner"),
    "EXT-GAMUDA": ("Farah Lim", "commercial_owner"),
    "EXT-SUNWAY": ("Farah Lim", "commercial_owner"),
    "EXT-YTL": ("Farah Lim", "commercial_owner"),
    "EXT-JKR": ("Goh Wei Ming", "commercial_owner"),
    "EXT-MITRA": ("Goh Wei Ming", "commercial_owner"),
    "EXT-IJM": ("Goh Wei Ming", "commercial_owner"),
    "EXT-WCT": ("Priya Nair", "commercial_owner"),
    "EXT-BINA": ("Priya Nair", "commercial_owner"),
    "EXT-MRCB": ("Priya Nair", "commercial_owner"),
    "EXT-SETIA": ("Priya Nair", "commercial_owner"),
}

DEFAULTS = {
    "review_programme_days": str(REVIEW_PROGRAMME_DAYS),
    "review_expected_rm": str(REVIEW_EXPECTED_CONSEQUENCE_RM),
    "review_penalty_rm": str(REVIEW_PENALTY_RM),
    "signoff_hours": "24",
    "urgent_signoff_hours": "2",
    "expedite_limit_rm": "5000",
    "constraint_cap_per_period": "2",
    "declaration_cooling_days": "7",
    "credibility_min_n": "3",
}
EVIDENCE_TYPES = ("site diary", "crew roster", "access permit")
DECLARATION_SOURCES = ("client LD clause", "holding cost", "programme float report")
APPROVER_ROLES = ("plant_supervisor", "project_planner", "commercial_owner")
NEXT_CYCLE = "2026-11-01"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def ensure_owners(conn) -> None:
    for code, (name, role) in OWNERS.items():
        conn.execute(
            """
            UPDATE demands
            SET owner_name = ?, owner_role = ?
            WHERE demand_code = ? AND (owner_name IS NULL OR owner_name = '')
            """,
            (name, role, code),
        )


def ensure_settings(conn) -> None:
    for key, value in DEFAULTS.items():
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING", (key, value))


def settings() -> dict[str, float]:
    with connect() as conn:
        rows = fetch_all(conn, "SELECT key, value FROM settings")
    found = {row["key"]: row["value"] for row in rows}
    return {key: float(found.get(key, value)) for key, value in DEFAULTS.items()}


def fallback_owners(lines: list[dict]) -> list[dict]:
    """When review is required but no order changed or went unserved, take one owner of each side that exists."""
    internal = next((line for line in lines if line.get("owner_role") == "project_planner" and line.get("owner_name")), None)
    external = next((line for line in lines if line.get("owner_role") == "commercial_owner" and line.get("owner_name")), None)
    chosen = [line for line in (internal, external) if line is not None]
    owners: dict[tuple[str, str], dict] = {}
    for line in chosen:
        key = (str(line["owner_name"]), str(line["owner_role"]))
        owners[key] = {"username": key[0], "role": key[1], "orders": [line.get("customer_or_project")]}
    return list(owners.values())


def affected_owners(recommended: list[dict], final: list[dict]) -> list[dict]:
    """Owners of orders whose allocation changed or which end up unserved."""
    rec = {int(line["demand_id"]): line for line in recommended}
    owners: dict[tuple[str, str], dict] = {}
    for line in final:
        demand_id = int(line["demand_id"])
        recommended_qty = float(rec.get(demand_id, {}).get("allocated_quantity") or 0)
        allocated = float(line.get("allocated_quantity") or 0)
        unserved = float(line.get("unserved_quantity") or 0)
        changed = abs(allocated - recommended_qty) > 0.1
        if not changed and unserved <= 0.05:
            continue
        name = (line.get("owner_name") or "").strip()
        role = (line.get("owner_role") or "").strip()
        if not name or not role:
            continue
        owners[(name, role)] = {
            "username": name,
            "role": role,
            "orders": owners.get((name, role), {}).get("orders", []) + [line.get("customer_or_project")],
        }
    return list(owners.values())


def review_required(triggers: list[str], changed: bool, fragile: bool, unverified: bool) -> bool:
    return bool(triggers) or changed or fragile or unverified


def deadline_for(created: datetime, first_required: str | None, values: dict[str, float] | None = None) -> str:
    values = values or settings()
    hours = float(values["signoff_hours"])
    if first_required:
        due = datetime.fromisoformat(first_required).replace(tzinfo=timezone.utc)
        if due - created <= timedelta(hours=24):
            hours = float(values["urgent_signoff_hours"])
    return _iso(created + timedelta(hours=hours))


def place_hold(plant_id: int, product_id: int, schedule: dict) -> None:
    from app.operations import write_commitment

    with connect() as conn:
        for day, qty in schedule.get("by_date", {}).items():
            conn.execute(
                """
                UPDATE capacity_calendar
                SET held_production = held_production + ?
                WHERE plant_id = ? AND product_id = ? AND prod_date = ?
                """,
                (float(qty), plant_id, product_id, day),
            )


def release_hold(decision: dict) -> None:
    schedule = json.loads(decision["hold_json"]) if decision.get("hold_json") else {}
    with connect() as conn:
        for day, qty in (schedule.get("by_date") or {}).items():
            conn.execute(
                """
                UPDATE capacity_calendar
                SET held_production = MAX(0, held_production - ?)
                WHERE plant_id = ? AND product_id = ? AND prod_date = ?
                """,
                (float(qty), decision["plant_id"], decision["product_id"], day),
            )


def signoff_rows(decision_id: int) -> list[dict]:
    with connect() as conn:
        return fetch_all(conn, "SELECT * FROM decision_signoffs WHERE decision_id = ? ORDER BY id", (decision_id,))


def missing_signoffs(decision: dict) -> list[dict]:
    required = json.loads(decision.get("required_signatories_json") or "[]")
    signed = {(row["username"], row["role"]) for row in signoff_rows(int(decision["id"]))}
    return [row for row in required if (row["username"], row["role"]) not in signed]


def record_signoff(decision_id: int, role: str, username: str, plan_choice: str, reason: str) -> dict:
    with connect() as conn:
        decision = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if decision is None:
        raise ValueError("Decision not found.")
    if decision["status"] != "awaiting_signoff":
        raise ValueError("This decision is not waiting for sign-off.")
    if username.strip() == decision["username"].strip():
        raise ValueError("The person who prepared the recommendation cannot sign it.")
    required = json.loads(decision.get("required_signatories_json") or "[]")
    match = next((row for row in required if row["username"] == username.strip() and row["role"] == role), None)
    if match is None:
        raise ValueError("Sign-off is limited to an owner of an order whose allocation changed or is unserved.")
    if any(row["username"] == username.strip() for row in signoff_rows(decision_id)):
        raise ValueError("This person has already signed.")
    if len(reason.strip()) < 8:
        raise ValueError("A sign-off needs a short reason.")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO decision_signoffs(decision_id, role, username, plan_choice, reason, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (decision_id, role, username.strip(), plan_choice, reason.strip(), _iso(_now())),
        )
        decision = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if not missing_signoffs(decision):
        _close_signoff(decision)
    return decision_view(decision_id)


def _plans_from_signoffs(rows: list[dict]) -> set[str]:
    return {row["plan_choice"] for row in rows}


def _commit_plan(decision: dict, plan: dict) -> None:
    from app.engine import load_world
    from app.operations import plant_mode, schedule_commitment, write_commitment

    if plant_mode(int(decision["plant_id"])) != "pilot":
        return
    world = load_world()
    targets = {int(line["demand_id"]): float(line.get("allocated_quantity") or 0) for line in plan.get("allocations", [])}
    schedule = schedule_commitment(world, int(decision["plant_id"]), int(decision["product_id"]), targets)
    write_commitment(int(decision["plant_id"]), int(decision["product_id"]), schedule)
    with connect() as conn:
        conn.execute("UPDATE decisions SET committed_json = ? WHERE id = ?", (json.dumps(schedule), decision["id"]))


def _close_signoff(decision: dict) -> None:
    """When every required owner has signed, apply the cheaper plan unless a constraint replaced it."""
    rows = signoff_rows(int(decision["id"]))
    choices = _plans_from_signoffs(rows)
    recommended = json.loads(decision["recommended_json"])
    final = json.loads(decision["final_json"])
    if len(choices) > 1 and not _constraints(int(decision["id"])):
        recommended_cost = float(decision["consequence_recommended"])
        final_cost = float(decision["consequence_final"])
        chosen = recommended if recommended_cost <= final_cost else final
        note = "Reviewers chose different plans. The lower expected consequence under the stated terms applies."
    else:
        chosen = final
        note = ""
    release_hold(decision)
    with connect() as conn:
        conn.execute(
            """
            UPDATE decisions
            SET status = 'approved', final_json = ?, hold_json = NULL, override_reason = CASE WHEN override_reason = '' THEN ? ELSE override_reason END
            WHERE id = ?
            """,
            (json.dumps(chosen), note, decision["id"]),
        )
    _commit_plan(decision, chosen)


def _constraints_this_month(plant_id: int, product_id: int) -> int:
    month = _now().strftime("%Y-%m")
    with connect() as conn:
        row = fetch_one(
            conn,
            """
            SELECT COUNT(*) AS n
            FROM decision_constraints c
            JOIN decisions d ON d.id = c.decision_id
            WHERE d.plant_id = ? AND d.product_id = ? AND substr(c.created_at, 1, 7) = ?
            """,
            (plant_id, product_id, month),
        )
    return int(row["n"] if row else 0)


def _constraints(decision_id: int) -> list[dict]:
    with connect() as conn:
        return fetch_all(conn, "SELECT * FROM decision_constraints WHERE decision_id = ? ORDER BY id", (decision_id,))


def add_constraint(decision_id: int, username: str, role: str, kind: str, quantity: float, evidence_type: str, note: str, demand_id: int | None) -> dict:
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError("Evidence must be a site diary, a crew roster, or an access permit.")
    if kind not in {"cap", "reserve", "days"}:
        raise ValueError("A constraint is a cap, a reserve, or a days limit.")
    if quantity <= 0:
        raise ValueError("The limit has to be greater than zero.")
    with connect() as conn:
        decision = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if decision is None or decision["status"] != "awaiting_signoff":
        raise ValueError("A constraint can be added while sign-off is open.")
    required = json.loads(decision.get("required_signatories_json") or "[]")
    if not any(row["username"] == username.strip() and row["role"] == role for row in required):
        raise ValueError("Only an owner of an affected order can add a constraint.")
    values = settings()
    if _constraints_this_month(int(decision["plant_id"]), int(decision["product_id"])) >= int(values["constraint_cap_per_period"]):
        raise ValueError("This plant and product already has the maximum number of constraints for the month.")
    final = json.loads(decision["final_json"])
    line = next((row for row in final.get("allocations", []) if demand_id and int(row["demand_id"]) == int(demand_id)), None)
    if kind == "cap":
        if line is None:
            raise ValueError("A cap names the order it limits.")
        ceiling = min(float(line["requested_quantity"]) * 0.4, 80.0)
        if quantity > ceiling + 1e-6:
            raise ValueError("A cap cannot exceed the smaller of 40% of the order and 80 m³.")
    if kind == "days" and quantity > 14:
        raise ValueError("A days limit cannot exceed 14.")
    if kind == "days" and line is None:
        raise ValueError("A days limit names the order.")
    solved = _resolve_with_constraint(decision, kind, quantity, demand_id)
    cost = round_rm(float(solved["expected_consequence_rm"]) - float(decision["consequence_recommended"]))
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO decision_constraints(
                decision_id, demand_id, username, kind, quantity, evidence_type, note, cost_rm, bound, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (decision_id, demand_id, username.strip(), kind, quantity, evidence_type, note.strip(), cost, _iso(_now())),
        )
        constraint_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO constraint_ledger(decision_id, constraint_id, owner_name, cost_rm, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (decision_id, constraint_id, username.strip(), cost, _iso(_now())),
        )
        conn.execute(
            "UPDATE decisions SET final_json = ?, consequence_final = ? WHERE id = ?",
            (json.dumps(solved["final"]), solved["expected_consequence_rm"], decision_id),
        )
    return {"id": constraint_id, "cost_rm": cost, "label": f"Honouring this costs {cost:,.2f} RM versus the recommendation.", "owner": username.strip()}


def _resolve_with_constraint(decision: dict, kind: str, quantity: float, demand_id: int | None) -> dict:
    from app.engine import load_world, solve_bucket

    world = load_world()
    if kind == "reserve":
        remaining = float(quantity)
        rows = [row for row in world["calendar"] if row["plant_id"] == decision["plant_id"] and row["product_id"] == decision["product_id"]]
        for row in reversed(rows):
            take = min(float(row["available_capacity"]), remaining)
            row["available_capacity"] = float(row["available_capacity"]) - take
            remaining -= take
    for demand in world["demands"]:
        if demand_id and int(demand["id"]) != int(demand_id):
            continue
        if int(demand["plant_id"]) != int(decision["plant_id"]) or int(demand["product_id"]) != int(decision["product_id"]):
            continue
        if kind == "cap":
            demand["allocation_cap_m3"] = float(quantity)
        if kind == "days":
            demand["max_programme_days"] = float(quantity)
    bucket = solve_bucket(world, int(decision["plant_id"]), int(decision["product_id"]), include_comparison=False, include_expedite=False)
    return {
        "expected_consequence_rm": float(bucket["expected_consequence_rm"]),
        "final": {"allocations": bucket["allocations"], "totals": {"expected_consequence_rm": bucket["expected_consequence_rm"]}, "reason": "Constraint applied and the book was solved again."},
    }


def declare_delay(demand_id: int, username: str, declared_rm_per_day: float, source: str, reason: str) -> dict:
    if source not in DECLARATION_SOURCES:
        raise ValueError("The source must be a client LD clause, a holding cost, or a programme float report.")
    if declared_rm_per_day < 0:
        raise ValueError("Declared delay cost cannot be negative.")
    if len(reason.strip()) < 8:
        raise ValueError("A revision needs a short reason.")
    values = settings()
    with connect() as conn:
        demand = fetch_one(conn, "SELECT * FROM demands WHERE id = ?", (demand_id,))
        if demand is None or demand["demand_type"] != "Internal":
            raise ValueError("Delay cost is declared on an internal project.")
        if demand.get("owner_name") and demand["owner_name"] != username.strip():
            raise ValueError("The project planner who owns this order declares its delay cost.")
        previous = fetch_all(conn, "SELECT * FROM delay_declarations WHERE demand_id = ? ORDER BY id", (demand_id,))
    if previous:
        last = datetime.fromisoformat(previous[-1]["created_at"])
        if _now() - last < timedelta(days=float(values["declaration_cooling_days"])):
            raise ValueError("A revision has to wait for the cooling-off period.")
    credibility = _credibility(demand["customer_or_project"])
    effective = round_rm(float(declared_rm_per_day) * credibility)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO delay_declarations(
                demand_id, project_name, declared_rm_per_day, source, reason, username, created_at, credibility, effective_rm_per_day
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (demand_id, demand["customer_or_project"], declared_rm_per_day, source, reason.strip(), username.strip(), _iso(_now()), credibility, effective),
        )
    return {"effective_rm_per_day": effective, "credibility": credibility, "project": demand["customer_or_project"]}


def _credibility(project: str) -> float:
    """Shrink a later declaration once at least three realised comparisons exist. Otherwise the factor is 1."""
    values = settings()
    with connect() as conn:
        rows = fetch_all(
            conn,
            """
            SELECT d.demand_id, d.declared_rm_per_day, dem.delay_cost_per_day
            FROM delay_declarations d
            JOIN demands dem ON dem.id = d.demand_id
            WHERE d.project_name = ?
            ORDER BY d.id
            """,
            (project,),
        )
        actuals = fetch_all(conn, "SELECT actual_json FROM decisions WHERE actual_json IS NOT NULL")
    covered: set[int] = set()
    for decision in actuals:
        for line in json.loads(decision["actual_json"]).get("lines", []):
            covered.add(int(line["demand_id"]))
    comparisons = []
    for row in rows:
        if int(row["demand_id"]) not in covered:
            continue
        declared = float(row["declared_rm_per_day"])
        realised = float(row["delay_cost_per_day"])
        if declared <= 0:
            continue
        comparisons.append(min(1.0, realised / declared))
    if len(comparisons) < int(values["credibility_min_n"]):
        return 1.0
    return round(sum(comparisons) / len(comparisons), 4)


def apply_declarations(world: dict) -> None:
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM delay_declarations ORDER BY id")
    latest: dict[int, dict] = {}
    for row in rows:
        latest[int(row["demand_id"])] = row
    for demand in world["demands"]:
        declared = latest.get(int(demand["id"]))
        if declared is None:
            continue
        demand["delay_cost_per_day"] = float(declared["effective_rm_per_day"])
        demand["declared_delay"] = True


def propose_setting(key: str, proposed_value: str, reason: str, username: str, role: str) -> dict:
    if key not in DEFAULTS:
        raise ValueError("That setting is not one of the review lines.")
    if role not in APPROVER_ROLES:
        raise ValueError("A threshold change is proposed by the plant supervisor, a project planner, or a commercial owner.")
    if len(reason.strip()) < 8:
        raise ValueError("The proposal needs a reason.")
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO setting_proposals(key, proposed_value, reason, proposed_by, proposer_role, created_at, status, effective_on)
            VALUES(?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (key, proposed_value, reason.strip(), username.strip(), role, _iso(_now()), NEXT_CYCLE),
        )
        proposal_id = cursor.lastrowid
    return {"id": proposal_id, "effective_on": NEXT_CYCLE, "status": "open"}


def approve_setting(proposal_id: int, role: str, username: str) -> dict:
    if role not in APPROVER_ROLES:
        raise ValueError("Approval comes from the plant supervisor, a project planner, and a commercial owner.")
    with connect() as conn:
        proposal = fetch_one(conn, "SELECT * FROM setting_proposals WHERE id = ?", (proposal_id,))
        if proposal is None or proposal["status"] != "open":
            raise ValueError("That proposal is not open.")
        if proposal["proposed_by"] == username.strip() and proposal["proposer_role"] == role:
            raise ValueError("The proposer does not approve their own seat.")
        existing = fetch_all(conn, "SELECT role FROM setting_approvals WHERE proposal_id = ?", (proposal_id,))
        if any(row["role"] == role for row in existing):
            raise ValueError("That seat has already approved.")
        conn.execute(
            "INSERT INTO setting_approvals(proposal_id, role, username, created_at) VALUES(?, ?, ?, ?)",
            (proposal_id, role, username.strip(), _iso(_now())),
        )
        seats = {row["role"] for row in existing} | {role}
        if set(APPROVER_ROLES) <= seats:
            conn.execute("UPDATE setting_proposals SET status = 'approved' WHERE id = ?", (proposal_id,))
    return {"id": proposal_id, "status": "approved" if set(APPROVER_ROLES) <= seats else "open", "effective_on": NEXT_CYCLE}


def apply_due_settings(now: datetime | None = None) -> list[int]:
    moment = now or _now()
    applied = []
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM setting_proposals WHERE status = 'approved'")
        for row in rows:
            if datetime.fromisoformat(row["effective_on"]).replace(tzinfo=timezone.utc) > moment:
                continue
            set_meta(conn, f"setting-audit:{row['id']}", json.dumps({"key": row["key"], "new": row["proposed_value"], "reason": row["reason"], "at": row["effective_on"]}))
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (row["key"], row["proposed_value"]))
            conn.execute("UPDATE setting_proposals SET status = 'effective' WHERE id = ?", (row["id"],))
            applied.append(int(row["id"]))
    return applied


def apply_expired_defaults(now: datetime | None = None) -> dict:
    """Idempotent. defaulted_at is the deadline, not the time the job ran."""
    moment = now or _now()
    defaulted: list[int] = []
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM decisions WHERE status = 'awaiting_signoff'")
    for decision in rows:
        if not decision.get("deadline"):
            continue
        due = datetime.fromisoformat(decision["deadline"])
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if moment < due or not missing_signoffs(decision):
            continue
        recommended = json.loads(decision["recommended_json"])
        release_hold(decision)
        updated = 0
        with connect() as conn:
            current = fetch_one(conn, "SELECT status FROM decisions WHERE id = ?", (decision["id"],))
            if current is None or current["status"] != "awaiting_signoff":
                continue
            cursor = conn.execute(
                """
                UPDATE decisions
                SET status = 'defaulted', final_json = ?, consequence_final = consequence_recommended,
                    defaulted_at = ?, hold_json = NULL,
                    override_reason = 'Sign-off was missing at the deadline. The recommendation applies.'
                WHERE id = ? AND status = 'awaiting_signoff'
                """,
                (json.dumps(recommended), decision["deadline"], decision["id"]),
            )
            updated = cursor.rowcount
        if updated:
            _commit_plan(decision, recommended)
            defaulted.append(int(decision["id"]))
    apply_due_settings(moment)
    return {"defaulted": defaulted}


def mark_constraints_bound(decision_id: int) -> None:
    with connect() as conn:
        decision = fetch_one(conn, "SELECT actual_json FROM decisions WHERE id = ?", (decision_id,))
        constraints = _constraints(decision_id)
    if not decision or not decision.get("actual_json"):
        return
    actuals = {int(line["demand_id"]): line for line in json.loads(decision["actual_json"]).get("lines", [])}
    with connect() as conn:
        for row in constraints:
            line = actuals.get(int(row["demand_id"])) if row.get("demand_id") else None
            bound = 0
            if line and row["kind"] == "cap":
                bound = 1 if float(line["delivered_m3"]) <= float(row["quantity"]) + 0.05 else 0
            if line and row["kind"] == "days":
                bound = 1 if float(line.get("programme_days_lost") or 0) + 0.05 >= float(row["quantity"]) else 0
            if row["kind"] == "reserve":
                bound = 1
            conn.execute("UPDATE decision_constraints SET bound = ? WHERE id = ?", (bound, row["id"]))


def decision_view(decision_id: int) -> dict:
    with connect() as conn:
        decision = fetch_one(conn, "SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if decision is None:
        raise ValueError("Decision not found.")
    return {
        "id": decision["id"],
        "status": decision["status"],
        "username": decision["username"],
        "deadline": decision.get("deadline"),
        "defaulted_at": decision.get("defaulted_at"),
        "required_signatories": json.loads(decision.get("required_signatories_json") or "[]"),
        "missing": missing_signoffs(decision) if decision["status"] == "awaiting_signoff" else [],
        "signoffs": signoff_rows(decision_id),
        "constraints": _constraints(decision_id),
    }


def governance_metrics() -> dict:
    with connect() as conn:
        signoffs = fetch_all(conn, "SELECT * FROM decision_signoffs ORDER BY id")
        decisions = fetch_all(conn, "SELECT * FROM decisions")
        constraints = fetch_all(conn, "SELECT * FROM decision_constraints")
    hours = []
    accepted = 0
    for row in signoffs:
        decision = next((item for item in decisions if item["id"] == row["decision_id"]), None)
        if decision is None:
            continue
        start = datetime.fromisoformat(decision["created_at"])
        signed = datetime.fromisoformat(row["created_at"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if signed.tzinfo is None:
            signed = signed.replace(tzinfo=timezone.utc)
        hours.append((signed - start).total_seconds() / 3600)
        if row["plan_choice"] in {"recommended", "typed"}:
            accepted += 1
    ordered = sorted(hours)
    median = ordered[len(ordered) // 2] if ordered else None
    awaiting = [row for row in decisions if row["status"] == "awaiting_signoff"]
    defaulted = [row for row in decisions if row["status"] == "defaulted"]
    non_response: dict[str, int] = {}
    for decision in defaulted:
        for missing in json.loads(decision.get("required_signatories_json") or "[]"):
            signed = {(row["username"], row["role"]) for row in signoffs if row["decision_id"] == decision["id"]}
            if (missing["username"], missing["role"]) not in signed:
                non_response[missing["role"]] = non_response.get(missing["role"], 0) + 1
    bound = [row for row in constraints if row["bound"] is not None]
    rate = None if not signoffs else round(accepted / len(signoffs), 4)
    rubber = bool(signoffs) and len(signoffs) >= 8 and rate is not None and rate >= 0.99 and median is not None and median < (1 / 60)
    return {
        "signoffs_n": len(signoffs),
        "signoffs_per_week": len(signoffs),
        "median_hours_to_sign": None if median is None else round(median, 2),
        "acceptance_rate": rate,
        "acceptance_n": len(signoffs),
        "rubber_stamp_warning": rubber,
        "rubber_stamp_note": "Acceptance near 100% with a median sign-off under a minute, once n is at least 8, is a rubber-stamp warning.",
        "defaulted_n": len(defaulted),
        "awaiting_n": len(awaiting),
        "non_response_by_role": non_response,
        "constraints_n": len(constraints),
        "constraints_bound_n": sum(1 for row in bound if row["bound"]),
        "constraints_checked_n": len(bound),
    }


def consult(decision_id: int, username: str, note: str) -> dict:
    if len(note.strip()) < 8:
        raise ValueError("A consultation note needs a short reason.")
    with connect() as conn:
        conn.execute(
            "INSERT INTO consultations(decision_id, username, note, created_at) VALUES(?, ?, ?, ?)",
            (decision_id, username.strip(), note.strip(), _iso(_now())),
        )
    return {"decision_id": decision_id, "username": username.strip()}
