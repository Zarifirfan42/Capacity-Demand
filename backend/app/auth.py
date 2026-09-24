"""Shared demo passcodes. Reads stay open. Writes and the demo reset do not."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def planner_passcode() -> str:
    return os.getenv("CDI_PLANNER_PASSCODE", "planner-demo")


def admin_passcode() -> str | None:
    return os.getenv("CDI_ADMIN_PASSCODE") or None


def auth_status() -> dict:
    using_local_default = "CDI_PLANNER_PASSCODE" not in os.environ
    return {
        "planner_hint": "planner-demo" if using_local_default else None,
        "reset_available": admin_passcode() is not None,
        "roles": ["viewer", "planner", "admin"],
    }


def require_writer(
    x_demo_role: str | None = Header(default=None),
    x_demo_passcode: str | None = Header(default=None),
) -> str:
    role = (x_demo_role or "").strip().lower()
    code = x_demo_passcode or ""
    if role == "admin" and admin_passcode() and code == admin_passcode():
        return "admin"
    if role == "planner" and code == planner_passcode():
        return "planner"
    raise HTTPException(status_code=401, detail="Enter the planner or admin passcode before recording.")


def require_admin(
    x_demo_role: str | None = Header(default=None),
    x_demo_passcode: str | None = Header(default=None),
) -> str:
    if admin_passcode() is None:
        raise HTTPException(status_code=403, detail="Set CDI_ADMIN_PASSCODE before the demo can be reset.")
    role = (x_demo_role or "").strip().lower()
    if role == "admin" and (x_demo_passcode or "") == admin_passcode():
        return "admin"
    raise HTTPException(status_code=401, detail="Resetting the demo requires the admin passcode.")
