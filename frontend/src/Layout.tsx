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

export function Layout() {
  const { name, setName, role, setRole, passcode, setPasscode } = usePlanner();
  const [hint, setHint] = useState<string | null>(null);
  useEffect(() => {
    api<{ planner_hint: string | null }>("/api/auth-status").then((row) => setHint(row.planner_hint)).catch(() => undefined);
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
            <option value="viewer">Viewer</option>
            <option value="planner">Planner</option>
            <option value="admin">Admin</option>
          </select>
          <label htmlFor="passcode">Passcode for writes</label>
          <input id="passcode" type="password" value={passcode} onChange={(event) => setPasscode(event.target.value)} />
          {hint ? <p className="note">Local demo planner passcode: {hint}. The admin passcode is set on the server and is not shown here.</p> : null}
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
