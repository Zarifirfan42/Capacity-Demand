import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Assumptions, ErrorNote, Kpi, PageHeader, Panel } from "../components";
import { longDate, m3, num, rm, when } from "../format";
import type { DecisionRow } from "../types";

type Tower = {
  question: string;
  horizon: { start: string; end: string };
  insight: string;
  assumptions: string[];
  kpis: {
    total_demand_m3: number;
    available_capacity_m3: number;
    usable_inventory_m3: number;
    capacity_gap_m3: number;
    aggregate_gap_m3: number;
    horizon_surplus_m3: number;
    internal_demand_m3: number;
    external_demand_m3: number;
    inventory_at_risk_rm: number;
    inventory_at_risk_note: string;
    margin_at_risk_rm: number;
    programme_days_at_risk: number;
    value_protected_rm: number;
    constrained_buckets: number;
  };
  hotspots: {
    plant_id: number;
    product_id: number;
    plant_name: string;
    product_name: string;
    shortfall_m3: number;
    crunch_date: string;
    expected_consequence_rm: number;
    programme_days: number;
    why: string;
  }[];
  policy_totals: { optimised_rm: number; earliest_rm: number; internal_first_rm: number; external_first_rm: number };
  recent_decisions: DecisionRow[];
};

export function ControlTowerPage() {
  const [data, setData] = useState<Tower | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Tower>("/api/control-tower")
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <p>Loading the planning position…</p>;
  const k = data.kpis;
  const policies = [
    { name: "Earliest date", value: data.policy_totals.earliest_rm },
    { name: "External first", value: data.policy_totals.external_first_rm },
    { name: "Internal first", value: data.policy_totals.internal_first_rm },
    { name: "Recommended", value: data.policy_totals.optimised_rm },
  ];

  return (
    <div className="page">
      <PageHeader
        kicker={`${longDate(data.horizon.start)} – ${longDate(data.horizon.end)}`}
        title="Control Tower"
        lede={data.question}
      />
      <div className="banner risk">
        <strong>The month is not short of cubic metres. It is short on the dates that matter.</strong>
        {data.insight}
      </div>
      <div className="kpi-grid">
        <Kpi label="Total demand" value={m3(k.total_demand_m3)} hint="Internal and external orders in one book" />
        <Kpi label="Available capacity" value={m3(k.available_capacity_m3)} hint={`Plus ${m3(k.usable_inventory_m3)} usable inventory`} />
        <Kpi label="Capacity gap" value={m3(k.capacity_gap_m3)} tone="risk" hint={`Dated shortfall. Month-total surplus is ${m3(k.horizon_surplus_m3)}.`} />
        <Kpi label="Internal demand" value={m3(k.internal_demand_m3)} hint="Group projects and plant work" />
        <Kpi label="External demand" value={m3(k.external_demand_m3)} hint="Customer orders" />
        <Kpi label="Inventory at risk" value={rm(k.inventory_at_risk_rm)} hint={k.inventory_at_risk_note} />
        <Kpi label="Margin at risk" value={rm(k.margin_at_risk_rm)} tone="risk" hint="Contribution margin on the unserved fraction" />
        <Kpi label="Programme days at risk" value={num(k.programme_days_at_risk, 1)} tone="risk" hint="Internal delay days scaled by the unserved fraction" />
        <Kpi label="Estimated value protected" value={rm(k.value_protected_rm)} tone="good" hint="Expected consequence avoided versus serving the earliest date first" />
      </div>

      <div className="split">
        <Panel title="Where demand beats dated supply" sub={`${k.constrained_buckets} plant-product books cannot be fully served.`}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Plant / product</th>
                  <th>Crunch</th>
                  <th className="num">Shortfall</th>
                  <th className="num">Expected consequence</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.hotspots.map((row) => (
                  <tr key={`${row.plant_id}-${row.product_id}`}>
                    <td>
                      <strong>{row.plant_name}</strong>
                      <div className="note">{row.product_name}</div>
                    </td>
                    <td className="nowrap">{longDate(row.crunch_date)}</td>
                    <td className="num">{m3(row.shortfall_m3)}</td>
                    <td className="num">{rm(row.expected_consequence_rm)}</td>
                    <td>
                      <Link to={`/allocation?plant=${row.plant_id}&product=${row.product_id}`}>Open decision</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Consequence by decision rule" sub="Lower is better. The recommendation has no internal or external preference.">
          <div className="chart-box short">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={policies} layout="vertical" margin={{ left: 16, right: 12 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tickFormatter={(value) => `${Math.round(Number(value) / 1000)}k`} />
                <YAxis type="category" dataKey="name" width={110} />
                <Tooltip formatter={(value) => rm(Number(value))} />
                <Bar dataKey="value" fill="#1d4e89" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <Panel title="Recorded decisions" sub="The system recommends. A named planner approves or changes the result.">
        {data.recent_decisions.length === 0 ? (
          <p className="note">No decision has been recorded yet. Open an allocation, review the reason, and record it.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Planner</th>
                  <th>Scope</th>
                  <th>Status</th>
                  <th>Reason</th>
                  <th className="num">Consequence</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_decisions.map((row) => (
                  <tr key={row.id}>
                    <td className="nowrap">{when(row.created_at)}</td>
                    <td>{row.username}</td>
                    <td>
                      {row.plant_name}
                      <div className="note">{row.product_name}</div>
                    </td>
                    <td><span className={`badge ${row.status === "modified" ? "warn" : "ok"}`}>{row.status}</span></td>
                    <td className="reason">{row.override_reason}</td>
                    <td className="num">{rm(row.consequence_final)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <Assumptions items={data.assumptions} />
    </div>
  );
}
