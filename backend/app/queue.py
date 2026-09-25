"""Role queues and the labelled demo expiry. Demo rows are marked demo=1."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.db import connect, fetch_all, fetch_one
from app.engine import product_is_stockable
from app.governance import apply_expired_defaults, release_hold

SHARED_NOTE = "Demo rows are shared on this database. Another session sees the same rows. Reset removes only rows marked demo."


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _item(kind: str, title: str, deadline: str, path: str, detail: str) -> dict:
    return {"kind": kind, "title": title, "deadline": deadline, "path": path, "detail": detail}


def queue_for(role: str, username: str = "") -> dict:
    now = datetime.now(timezone.utc)
    today = now.date()
    name = username.strip().lower()
    with connect() as conn:
        decisions = fetch_all(conn, "SELECT * FROM decisions WHERE status != 'replaced' ORDER BY id")
        expedites = fetch_all(conn, "SELECT * FROM expedite_decisions WHERE decision = 'awaiting_review' ORDER BY id")
        proposals = fetch_all(conn, "SELECT * FROM setting_proposals WHERE status = 'open' ORDER BY id")
        approvals = fetch_all(conn, "SELECT * FROM setting_approvals")
        snapshots = fetch_all(conn, "SELECT * FROM inventory_snapshots")
        products = fetch_all(conn, "SELECT * FROM products")
        plants = fetch_all(conn, "SELECT * FROM plants")
        signoffs = fetch_all(conn, "SELECT * FROM decision_signoffs")
    signed = {(int(row["decision_id"]), row["role"], row["username"].strip().lower()) for row in signoffs}
    approved_seats = {}
    for row in approvals:
        approved_seats.setdefault(int(row["proposal_id"]), set()).add(row["role"])
    items: list[dict] = []

    def decision_path(row: dict) -> str:
        return f"/allocation?plant={row['plant_id']}&product={row['product_id']}&decision={row['id']}"

    if role == "plant_supervisor":
        for row in expedites:
            items.append(
                _item(
                    "expedite",
                    f"Expedite approval · {row['customer_or_project']}",
                    str(row["created_at"]),
                    f"/allocation?plant={row['plant_id']}&product={row['product_id']}",
                    f"RM{float(row['cost_rm']):,.0f} is above the plant limit and is waiting for the two owners.",
                )
            )
        for plant in plants:
            for product in products:
                if not product_is_stockable(product):
                    continue
                dated = [
                    str(row["as_of_date"])[:10]
                    for row in snapshots
                    if int(row["plant_id"]) == int(plant["id"]) and int(row["product_id"]) == int(product["id"])
                ]
                latest = max(dated) if dated else ""
                stale = not latest or (today - datetime.fromisoformat(latest).date()).days > 7
                if stale:
                    due = today.isoformat() if not latest else (datetime.fromisoformat(latest).date() + timedelta(days=7)).isoformat()
                    items.append(
                        _item(
                            "stock_count",
                            f"Stock count due · {plant['name']} / {product['name']}",
                            due,
                            "/measurement",
                            "Precast only. Ready-mix is not stocked. A count is due when the last snapshot is missing or older than 7 days.",
                        )
                    )
    if role == "scheduler":
        for row in decisions:
            if row["status"] == "awaiting_signoff":
                items.append(
                    _item(
                        "signoff",
                        f"Plan awaiting sign-off · {row['plant_name']} / {row['product_name']}",
                        str(row.get("deadline") or ""),
                        decision_path(row),
                        f"Prepared by {row['username']}.",
                    )
                )
            if row["status"] in ("approved", "modified", "defaulted") and not row.get("actual_json"):
                created = datetime.fromisoformat(str(row["created_at"]))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                due = str(row.get("deadline") or _iso(created + timedelta(days=2)))
                due_at = datetime.fromisoformat(due)
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=timezone.utc)
                if now > due_at:
                    items.append(
                        _item(
                            "actuals",
                            f"Actuals overdue · {row['plant_name']} / {row['product_name']}",
                            due,
                            decision_path(row),
                            "The decision is closed and actuals are still empty.",
                        )
                    )
    if role in ("project_planner", "commercial_owner"):
        for row in decisions:
            if row["status"] != "awaiting_signoff":
                continue
            required = json.loads(row.get("required_signatories_json") or "[]")
            named = [person for person in required if person.get("role") == role]
            if not named:
                continue
            if name and not any(str(person.get("username") or "").strip().lower() == name for person in named):
                continue
            if any(decision_id == int(row["id"]) and signed_role == role for decision_id, signed_role, _who in signed):
                continue
            who = ", ".join(str(person.get("username") or role) for person in named)
            items.append(
                _item(
                    "signoff",
                    f"Decision names {who} · {row['plant_name']} / {row['product_name']}",
                    str(row.get("deadline") or ""),
                    decision_path(row),
                    "Sign-off is still open.",
                )
            )
    if role in ("plant_supervisor", "project_planner", "commercial_owner"):
        for row in proposals:
            if role in approved_seats.get(int(row["id"]), set()):
                continue
            items.append(
                _item(
                    "threshold",
                    f"Threshold proposal · {row['key']}",
                    str(row["effective_on"]),
                    "/measurement",
                    f"{row['proposed_by']} proposed {row['proposed_value']}. {row['reason']}",
                )
            )
    items.sort(key=lambda row: row["deadline"] or "9999-12-31")
    return {
        "role": role,
        "username": username,
        "items": items,
        "empty": "" if items else "Nothing is waiting for this seat.",
        "shared_demo_note": SHARED_NOTE,
    }


def start_demo(username: str) -> dict:
    with connect() as conn:
        existing = fetch_one(conn, "SELECT * FROM decisions WHERE demo = 1 AND status = 'awaiting_signoff' ORDER BY id DESC")
        if existing is not None:
            return {"id": existing["id"], "status": existing["status"], "deadline": existing["deadline"], "created": False}
        plant = fetch_one(conn, "SELECT * FROM plants WHERE id = 2") or fetch_one(conn, "SELECT * FROM plants ORDER BY id LIMIT 1")
        product = fetch_one(conn, "SELECT * FROM products WHERE id = 3") or fetch_one(conn, "SELECT * FROM products ORDER BY id LIMIT 1")
        if plant is None or product is None:
            raise ValueError("No plant or product is loaded, so a demo decision cannot be opened.")
        now = datetime.now(timezone.utc)
        deadline = _iso(now + timedelta(hours=2))
        required = [
            {"username": "Farah Lim", "role": "commercial_owner"},
            {"username": "Nur Aina", "role": "project_planner"},
        ]
        plan = {"allocations": [], "totals": {"expected_consequence_rm": 0}}
        cursor = conn.execute(
            """
            INSERT INTO decisions(
                created_at, username, plant_id, product_id, plant_name, product_name,
                recommended_json, final_json, override_reason, status,
                consequence_recommended, consequence_final, unserved_recommended, unserved_final,
                reason_category, chosen_plan, terms_confirmed, deadline, required_signatories_json, preparer_role, demo
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, '', 'awaiting_signoff', 0, 0, 0, 0, '', '', 0, ?, ?, 'scheduler', 1)
            """,
            (
                _iso(now),
                username.strip() or "Demo scheduler",
                plant["id"],
                product["id"],
                plant["name"],
                product["name"],
                json.dumps(plan),
                json.dumps(plan),
                deadline,
                json.dumps(required),
            ),
        )
        return {"id": cursor.lastrowid, "status": "awaiting_signoff", "deadline": deadline, "created": True}


def expire_demo() -> dict:
    """Move demo deadlines into the past and call the real default function."""
    past = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    with connect() as conn:
        conn.execute(
            "UPDATE decisions SET deadline = ? WHERE demo = 1 AND status = 'awaiting_signoff'",
            (past,),
        )
    result = apply_expired_defaults()
    result["demo"] = True
    result["note"] = "Simulate expiry is demo mode. It calls the same default function as the daily job."
    return result


def reset_demo() -> dict:
    from app.operations import release_commitment

    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM decisions WHERE demo = 1")
    for row in rows:
        release_hold(row)
        if row.get("committed_json"):
            release_commitment(row)
    ids = [int(row["id"]) for row in rows]
    with connect() as conn:
        if ids:
            marks = ",".join("?" * len(ids))
            for table in ("decision_signoffs", "decision_constraints", "consultations", "constraint_ledger"):
                conn.execute(f"DELETE FROM {table} WHERE decision_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM decisions WHERE id IN ({marks})", ids)
        demand_count = conn.execute("SELECT COUNT(*) AS n FROM demands WHERE demo = 1").fetchone()["n"]
        conn.execute("DELETE FROM demands WHERE demo = 1")
    return {"deleted_decisions": len(ids), "deleted_demands": int(demand_count), "note": SHARED_NOTE}
