"""Per-role demo secrets. Reads stay open. A viewer who writes is refused with 403."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

ROLE_ENV = {
    "scheduler": "CDI_PASSCODE_SCHEDULER",
    "plant_supervisor": "CDI_PASSCODE_PLANT_SUPERVISOR",
    "project_planner": "CDI_PASSCODE_PROJECT_PLANNER",
    "commercial_owner": "CDI_PASSCODE_COMMERCIAL_OWNER",
    "admin": "CDI_PASSCODE_ADMIN",
}
LOCAL_DEFAULTS = {
    "scheduler": "scheduler-demo",
    "plant_supervisor": "supervisor-demo",
    "project_planner": "planner-demo",
    "commercial_owner": "commercial-demo",
    "admin": "admin-demo",
}
WRITE_ROLES = list(ROLE_ENV)


def passcode_for(role: str) -> str:
    env = ROLE_ENV.get(role)
    if env is None:
        return ""
    return os.getenv(env, LOCAL_DEFAULTS[role])


def auth_status() -> dict:
    hints = {role: LOCAL_DEFAULTS[role] for role, env in ROLE_ENV.items() if env not in os.environ}
    return {
        "hints": hints,
        "roles": ["viewer", *WRITE_ROLES],
        "identity_note": "A real deployment would use the company identity provider, for example Microsoft 365 sign-in. These per-role secrets are the demo stand-in.",
    }


def require_roles(*allowed: str):
    def checker(
        x_demo_role: str | None = Header(default=None),
        x_demo_passcode: str | None = Header(default=None),
    ) -> str:
        role = (x_demo_role or "").strip().lower()
        if role == "viewer":
            raise HTTPException(status_code=403, detail="A viewer can read. This action needs a role that is allowed to write.")
        if role not in ROLE_ENV:
            raise HTTPException(status_code=401, detail="Enter a role and its passcode before recording.")
        if (x_demo_passcode or "") != passcode_for(role):
            raise HTTPException(status_code=401, detail="The passcode does not match that role.")
        if role not in allowed:
            raise HTTPException(status_code=403, detail=f"This action is for {', '.join(allowed)}.")
        return role

    return checker


require_writer = require_roles("scheduler")
require_admin = require_roles("admin")
