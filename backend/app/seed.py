"""Synthetic book of business for October 2026.

The Shah Alam Grade 40 book is constructed so that orders due by 9 Oct compete
for dated capacity. Ready-mix cannot be stocked, so that supply is capacity
only. Month-total capacity can still cover month-total volume. The problem is
the date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.db import connect, get_meta, init_db, reset_data, set_meta
from app.forecast import history_rows
from app.economics import HORIZON_DAYS, HORIZON_START

SEED_VERSION = "2026-10-hero-5"
START = date.fromisoformat(HORIZON_START)


def _iso(day: date) -> str:
    return day.isoformat()


def _dates() -> list[date]:
    return [START + timedelta(days=offset) for offset in range(HORIZON_DAYS)]


def _capacity_for(plant_code: str, product_code: str, day: date) -> tuple[float, float, str]:
    """Return daily capacity, planned production, and a note."""
    weekday = day.weekday()
    note = ""

    if plant_code == "SA" and product_code == "G40":
        if day in (date(2026, 10, 7), date(2026, 10, 8)):
            return 100.0, 60.0, "Line 2 maintenance. Available capacity cut to 40 m³."
        if weekday == 6:
            return 25.0, 10.0, ""
        if weekday == 5:
            return 70.0, 30.0, ""
        return 180.0, 80.0, ""

    if plant_code == "SA" and product_code == "G50":
        if weekday == 6:
            return 8.0, 5.0, ""
        if weekday == 5:
            return 20.0, 12.0, ""
        return 40.0, 26.0, ""

    if plant_code == "SA" and product_code == "PCS":
        if weekday == 6:
            return 6.0, 4.0, ""
        if weekday == 5:
            return 12.0, 8.0, ""
        return 18.0, 10.0, ""

    if plant_code == "PG" and product_code == "G40":
        if weekday == 6:
            return 14.0, 10.0, ""
        if weekday == 5:
            return 36.0, 24.0, ""
        return 96.0, 68.0, ""

    if plant_code == "PG" and product_code == "G50":
        if day in (date(2026, 10, 14), date(2026, 10, 15)):
            return 24.0, 16.0, "Mixer maintenance. Available capacity cut to 8 m³."
        if weekday == 6:
            return 10.0, 6.0, ""
        if weekday == 5:
            return 24.0, 14.0, ""
        return 48.0, 30.0, ""

    if plant_code == "PG" and product_code == "PCS":
        if weekday == 6:
            return 2.0, 2.0, ""
        if weekday == 5:
            return 6.0, 4.0, ""
        return 10.0, 7.0, ""

    raise KeyError((plant_code, product_code))


PLANTS = [
    (1, "SA", "Shah Alam Works", "Selangor"),
    (2, "PG", "Pasir Gudang Works", "Johor"),
]

PRODUCTS = [
    # id, code, name, unit, inventory RM/m3 (assumption), emergency RM/m3 (assumption)
    # id, code, name, unit, inventory RM/m3 (assumption), emergency RM/m3 (assumption), stockable
    # Ready-mix cannot be held. Precast can.
    (1, "G40", "Ready-Mix Grade 40", "m³", 280.0, 95.0, 0),
    (2, "G50", "Ready-Mix Grade 50", "m³", 340.0, 110.0, 0),
    (3, "PCS", "Precast Wall Panel", "m³", 1200.0, 180.0, 1),
]

INVENTORY = [
    # plant, product, on_hand, safety. Ready-mix is zero because it cannot be stocked.
    (1, 1, 0.0, 0.0),
    (1, 2, 0.0, 0.0),
    (1, 3, 22.0, 16.0),
    (2, 1, 0.0, 0.0),
    (2, 2, 0.0, 0.0),
    (2, 3, 18.0, 12.0),
]


def _demand_rows() -> list[tuple]:
    """One row per competing order. Amounts are totals for the order, in RM."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # code, type, name, customer_type, plant, product, date, requested, confirmed,
    # status, confidence, margin, penalty, criticality, delay_days, delay_per_day, source, notes
    raw = [
        (
            "INT-MERDEKA",
            "Internal",
            "Merdeka Podium",
            "Group project",
            1,
            1,
            "2026-10-09",
            400,
            400,
            "Firm",
            "Confirmed",
            16000,
            0,
            "Critical",
            3,
            20000,
            "Project schedule",
            "Podium pour is on the critical path. A full miss slips the façade contractor by 3 days at RM20,000 per day.",
        ),
        (
            "EXT-GAMUDA",
            "External",
            "Gamuda MRT Feeder",
            "Main contractor",
            1,
            1,
            "2026-10-09",
            300,
            300,
            "Firm",
            "Confirmed",
            18000,
            30000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Confirmed purchase order. Liquidated damages of RM30,000 apply if the 300 m³ is missed.",
        ),
        (
            "EXT-JKR",
            "External",
            "JKR School Cluster",
            "Government",
            1,
            1,
            "2026-10-06",
            180,
            180,
            "Firm",
            "Confirmed",
            5400,
            22000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Due earlier than Merdeka. Low government margin, with liquidated damages of RM22,000.",
        ),
        (
            "EXT-SUNWAY",
            "External",
            "Sunway Puteri Cove",
            "Main contractor",
            1,
            1,
            "2026-10-08",
            220,
            180,
            "Firm",
            "Confirmed",
            11000,
            8000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Residential pour. Moderate margin and a smaller penalty than Gamuda or JKR.",
        ),
        (
            "INT-ELMINA",
            "Internal",
            "Elmina Business Park Phase 3",
            "Group project",
            1,
            1,
            "2026-10-09",
            150,
            0,
            "Open",
            "Probable",
            4500,
            0,
            "High",
            2,
            8000,
            "Project schedule",
            "Site has not issued a firm pour release. Treated as probable, so it does not outrank a confirmed penalty.",
        ),
        (
            "EXT-YTL",
            "External",
            "YTL Next Phase",
            "Developer",
            1,
            1,
            "2026-10-09",
            160,
            0,
            "Forecast",
            "Forecast",
            9600,
            0,
            "n/a",
            0,
            0,
            "Sales forecast",
            "Planner forecast only. Weighted at 45% so it does not displace confirmed orders.",
        ),
        (
            "EXT-MITRA",
            "External",
            "Mitrajaya Warehouse Slab",
            "Main contractor",
            1,
            1,
            "2026-10-24",
            180,
            100,
            "Firm",
            "Confirmed",
            9000,
            6000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Falls after the Shah Alam crunch. Later capacity can cover it without touching the 9 Oct book.",
        ),
        (
            "INT-PENANG",
            "Internal",
            "Penang LRT Depot",
            "Group project",
            1,
            2,
            "2026-10-16",
            210,
            210,
            "Firm",
            "Confirmed",
            6300,
            0,
            "Critical",
            5,
            18000,
            "Project schedule",
            "Depot base slab. Five days of programme delay at RM18,000 per day if the pour is missed.",
        ),
        (
            "EXT-IJM",
            "External",
            "IJM Elevated Station",
            "Main contractor",
            1,
            2,
            "2026-10-16",
            150,
            150,
            "Firm",
            "Confirmed",
            18000,
            20000,
            "n/a",
            0,
            0,
            "Customer PO",
            "High margin Grade 50, due the same day as the Penang depot. Competes for the same line.",
        ),
        (
            "EXT-WCT",
            "External",
            "WCT Highway Package",
            "Main contractor",
            2,
            1,
            "2026-10-21",
            250,
            200,
            "Firm",
            "Confirmed",
            27500,
            20000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Higher commercial consequence per m³ than the internal Pan Borneo package at the same plant.",
        ),
        (
            "INT-BORNEO",
            "Internal",
            "Pan Borneo Package 3B",
            "Group project",
            2,
            1,
            "2026-10-21",
            320,
            80,
            "Open",
            "Confirmed",
            9600,
            0,
            "Medium",
            3,
            14000,
            "Project schedule",
            "Confirmed internal package, but the delay cost per m³ is below WCT's penalty and margin.",
        ),
        (
            "INT-SILO",
            "Internal",
            "Pasir Gudang Silo Expansion",
            "Internal maintenance",
            2,
            1,
            "2026-10-28",
            40,
            0,
            "Open",
            "Probable",
            800,
            0,
            "Low",
            1,
            2000,
            "Manual",
            "Plant's own work. Low criticality and a late date, so it uses leftover capacity.",
        ),
        (
            "INT-RTS",
            "Internal",
            "RTS Link Station Package",
            "Group project",
            2,
            2,
            "2026-10-16",
            180,
            180,
            "Firm",
            "Confirmed",
            5400,
            0,
            "Critical",
            4,
            22000,
            "Project schedule",
            "Cross-border station pour. Programme delay dominates the commercial margin.",
        ),
        (
            "EXT-BINA",
            "External",
            "Bina Puri Jetty Works",
            "Main contractor",
            2,
            2,
            "2026-10-16",
            140,
            140,
            "Firm",
            "Confirmed",
            11200,
            12000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Due the same day as RTS Link, during mixer maintenance on 14-15 Oct.",
        ),
        (
            "INT-KWASA",
            "Internal",
            "Kwasa Damansara Plot 12",
            "Group project",
            1,
            3,
            "2026-10-18",
            90,
            90,
            "Firm",
            "Confirmed",
            3600,
            0,
            "High",
            2,
            9000,
            "Project schedule",
            "Precast walls for the plot 12 podium. Two-day erection hold if panels miss the slot.",
        ),
        (
            "EXT-MRCB",
            "External",
            "MRCB Transit Plaza",
            "Developer",
            1,
            3,
            "2026-10-18",
            60,
            40,
            "Firm",
            "Confirmed",
            5400,
            6000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Architectural panels. Competes with Kwasa for Shah Alam precast beds.",
        ),
        (
            "INT-ECRL",
            "Internal",
            "ECRL Station Precast",
            "Group project",
            2,
            3,
            "2026-10-14",
            40,
            40,
            "Firm",
            "Confirmed",
            1600,
            0,
            "Critical",
            6,
            12000,
            "Project schedule",
            "Six days of station erection delay if the panel set is late.",
        ),
        (
            "EXT-SETIA",
            "External",
            "SP Setia Facade Panels",
            "Developer",
            2,
            3,
            "2026-10-14",
            35,
            35,
            "Firm",
            "Confirmed",
            5250,
            8000,
            "n/a",
            0,
            0,
            "Customer PO",
            "Premium facade panels. High margin, but the programme delay on ECRL is larger.",
        ),
    ]
    rows = []
    for item in raw:
        code = item[0]
        demand_type = item[1]
        customer_type = item[3]
        penalty = float(item[12])
        if demand_type == "External" and customer_type in {"Government", "Main contractor"} and penalty > 0:
            penalty_type = "lump_sum"
        else:
            penalty_type = "per_m3"
        delay_type = {
            "INT-MERDEKA": "lump_days",
            "INT-PENANG": "lump_days",
            "INT-RTS": "lump_days",
            "INT-ECRL": "lump_days",
            "INT-KWASA": "per_day",
            "INT-SILO": "per_day",
        }.get(code, "proportional")
        rows.append((*item, now, penalty_type, delay_type, 0, "any", 1, 1, 0))
    return rows


def seed(force: bool = False) -> None:
    """Fill an empty database. Never deletes decisions, actuals, or other user rows.

    `force` is ignored. Wiping the demo is POST /api/admin/reset, which requires the admin passcode.
    """
    del force
    init_db()
    with connect() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM plants").fetchone()
        count = existing["n"] if hasattr(existing, "keys") else existing[0]
        if int(count) > 0:
            return
        conn.executemany("INSERT INTO plants(id, code, name, location) VALUES(?, ?, ?, ?)", PLANTS)
        conn.executemany(
            """
            INSERT INTO products(id, code, name, unit, inventory_value_per_m3, emergency_cost_per_m3, emergency_cost_is_assumption, stockable)
            VALUES(?, ?, ?, ?, ?, ?, 1, ?)
            """,
            PRODUCTS,
        )
        plant_codes = {row[0]: row[1] for row in PLANTS}
        product_codes = {row[0]: row[1] for row in PRODUCTS}
        calendar = []
        for plant_id, plant_code in plant_codes.items():
            for product_id, product_code in product_codes.items():
                for day in _dates():
                    capacity, planned, note = _capacity_for(plant_code, product_code, day)
                    calendar.append((plant_id, product_id, _iso(day), capacity, planned, note))
        conn.executemany(
            """
            INSERT INTO capacity_calendar(plant_id, product_id, prod_date, daily_capacity, planned_production, note)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            calendar,
        )
        conn.executemany(
            """
            INSERT INTO inventory(plant_id, product_id, as_of_date, on_hand, safety_stock)
            VALUES(?, ?, ?, ?, ?)
            """,
            [(p, r, HORIZON_START, on_hand, safety) for p, r, on_hand, safety in INVENTORY],
        )
        conn.executemany(
            """
            INSERT INTO demands(
                demand_code, demand_type, customer_or_project, customer_type, plant_id, product_id,
                required_date, requested_quantity, confirmed_quantity, demand_status, confidence_level,
                contribution_margin, contractual_penalty, project_criticality, delay_days_if_unserved,
                delay_cost_per_day, source, notes, created_at,
                penalty_type, delay_type, penalty_type_unverified, lump_sum_trigger,
                types_unverified, delay_type_unverified, minimum_useful_delivery_m3
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _demand_rows(),
        )
        conn.executemany(
            """
            INSERT INTO demand_history(plant_id, product_id, demand_type, month_start, quantity_m3)
            VALUES(?, ?, ?, ?, ?)
            """,
            history_rows(),
        )
        set_meta(conn, "seed_version", SEED_VERSION)
        set_meta(conn, "horizon_start", HORIZON_START)
        set_meta(conn, "horizon_end", (START + timedelta(days=HORIZON_DAYS - 1)).isoformat())
