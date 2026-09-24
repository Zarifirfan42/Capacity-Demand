import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "./api";
import { usePlanner } from "./planner";

const LINKS: readonly (readonly [string, string, string])[] = [
  ["/", "01", "Control Tower"],
  ["/demand", "02", "Demand Hub"],
  ["/capacity", "03", "Capacity Planner"],
  ["/allocation", "04", "Allocation Decision"],
  ["/scenarios", "05", "Scenario Simulator"],
  ["/impact", "06", "Business Impact"],
  ["/measurement", "07", "Measurement"],
];

const ROLE_LABELS: Record<string, string> = {
  viewer: "Viewer",
  scheduler: "Scheduler",
  plant_supervisor: "Plant supervisor",
  project_planner: "Project planner",
  commercial_owner: "Commercial owner",
  admin: "Admin",
};

export function Layout() {
  const { name, setName, role, setRole, passcode, setPasscode } = usePlanner();
  const [hints, setHints] = useState<Record<string, string>>({});
  const [roles, setRoles] = useState<string[]>(["viewer", "scheduler", "plant_supervisor", "project_planner", "commercial_owner", "admin"]);
  const [identity, setIdentity] = useState("");
  const [mockBadge, setMockBadge] = useState("");
  useEffect(() => {
    api<{ hints: Record<string, string>; roles: string[]; identity_note: string }>("/api/auth-status")
      .then((row) => {
        setHints(row.hints || {});
        setRoles(row.roles);
        setIdentity(row.identity_note);
      })
      .catch(() => undefined);
    api<{ badge: string | null }>("/api/intake/status")
      .then((row) => setMockBadge(row.badge || ""))
      .catch(() => undefined);
  }, []);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>CAPACITY & DEMAND</strong>
          <span>Allocation decision system for constrained plants. October 2026.</span>
        </div>
        <nav className="nav">
          {LINKS.map(([to, index, label]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              <small>{index}</small>
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="planner">
          <label htmlFor="planner">Planner on the audit trail</label>
          <input id="planner" value={name} onChange={(event) => setName(event.target.value)} />
          <label htmlFor="role">Role</label>
          <select id="role" value={role} onChange={(event) => setRole(event.target.value)}>
            {roles.map((item) => <option key={item} value={item}>{ROLE_LABELS[item] || item}</option>)}
          </select>
          <label htmlFor="passcode">Passcode for this role</label>
          <input id="passcode" type="password" value={passcode} onChange={(event) => setPasscode(event.target.value)} />
          {hints[role] ? <p className="note">Local demo passcode for this role: {hints[role]}. A configured server secret is not shown.</p> : null}
          {identity ? <p className="note">{identity}</p> : null}
        </div>
      </aside>
      <main className="main">
        {mockBadge ? <div className="mock-banner">{mockBadge}</div> : null}
        <Outlet />
      </main>
    </div>
  );
}
