import { NavLink, Outlet } from "react-router-dom";
import { usePlanner } from "./planner";

const LINKS: readonly (readonly [string, string, string])[] = [
  ["/", "01", "Control Tower"],
  ["/demand", "02", "Demand Hub"],
  ["/capacity", "03", "Capacity Planner"],
  ["/allocation", "04", "Allocation Decision"],
  ["/scenarios", "05", "Scenario Simulator"],
  ["/impact", "06", "Business Impact"],
];

export function Layout() {
  const { name, setName } = usePlanner();
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
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
