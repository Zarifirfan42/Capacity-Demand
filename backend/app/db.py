"""SQLite access for the Capacity & Demand Intelligence prototype."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cdi.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plants (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    location TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    inventory_value_per_m3 REAL NOT NULL,
    emergency_cost_per_m3 REAL NOT NULL,
    emergency_cost_is_assumption INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS capacity_calendar (
    id INTEGER PRIMARY KEY,
    plant_id INTEGER NOT NULL REFERENCES plants(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    prod_date TEXT NOT NULL,
    daily_capacity REAL NOT NULL,
    planned_production REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (plant_id, product_id, prod_date)
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY,
    plant_id INTEGER NOT NULL REFERENCES plants(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    as_of_date TEXT NOT NULL,
    on_hand REAL NOT NULL,
    safety_stock REAL NOT NULL,
    UNIQUE (plant_id, product_id)
);

CREATE TABLE IF NOT EXISTS demands (
    id INTEGER PRIMARY KEY,
    demand_code TEXT NOT NULL UNIQUE,
    demand_type TEXT NOT NULL CHECK (demand_type IN ('Internal', 'External')),
    customer_or_project TEXT NOT NULL,
    customer_type TEXT NOT NULL,
    plant_id INTEGER NOT NULL REFERENCES plants(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    required_date TEXT NOT NULL,
    requested_quantity REAL NOT NULL,
    confirmed_quantity REAL NOT NULL,
    demand_status TEXT NOT NULL,
    confidence_level TEXT NOT NULL CHECK (confidence_level IN ('Confirmed', 'Probable', 'Forecast')),
    contribution_margin REAL NOT NULL,
    contractual_penalty REAL NOT NULL,
    project_criticality TEXT NOT NULL,
    delay_days_if_unserved REAL NOT NULL,
    delay_cost_per_day REAL NOT NULL,
    source TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    username TEXT NOT NULL,
    plant_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    plant_name TEXT NOT NULL,
    product_name TEXT NOT NULL,
    recommended_json TEXT NOT NULL,
    final_json TEXT NOT NULL,
    override_reason TEXT NOT NULL,
    status TEXT NOT NULL,
    consequence_recommended REAL NOT NULL,
    consequence_final REAL NOT NULL,
    unserved_recommended REAL NOT NULL,
    unserved_final REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def reset_data(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DELETE FROM decisions;
        DELETE FROM demands;
        DELETE FROM inventory;
        DELETE FROM capacity_calendar;
        DELETE FROM products;
        DELETE FROM plants;
        DELETE FROM meta;
        """
    )


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else dict(row)
