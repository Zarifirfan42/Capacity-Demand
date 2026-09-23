import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Assumptions, ErrorNote, Kpi, PageHeader, Panel } from "../components";
import { m3, num, rm, when } from "../format";
import type { DecisionRow } from "../types";

type Column = {
  key: string;
  label: string;
  detail: string;
  unserved_m3: number;
  margin_at_risk_rm: number;
  penalty_at_risk_rm: number;
  delay_cost_rm: number;
  gross_consequence_rm: number;
  expected_consequence_rm: number;
  programme_days: number;
};

type Impact = {
  columns: Column[];
  reference_policies: Column[];
  value_protected_rm: number;
  value_protected_note: string;
  approved_notes: string[];
  assumptions: string[];
  inventory: {
    inventory_value_rm: number;
    safety_stock_value_rm: number;
    excess_inventory_m3: number;
    excess_inventory_value_rm: number;
    working_capital_rm: number;
    working_capital_note: string;
    inventory_consumed_value_rm: number;
    inventory_at_risk_rm: number;
    lines: {
      plant_name: string;
      product_name: string;
      on_hand_m3: number;
      safety_stock_m3: number;
      excess_m3: number;
      inventory_value_rm: number;
      excess_value_rm: number;
      unit_value_rm: number;
      unit_value_is_assumption: boolean;
    }[];
  };
  expedite: {
    note: string;
    emergency_cost_if_clearing_worthwhile_shortfalls_rm: number;
    net_benefit_if_those_are_expedited_rm: number;
    emergency_cost_if_clearing_low_consequence_shortfalls_rm: number;
    actions: {
      plant_name: string;
      product_name: string;
      customer_or_project: string;
      unserved_quantity: number;
      expedite_cost_rm: number;
      worth_expediting: boolean;
      net_benefit_rm: number;
      emergency_cost_is_assumption: boolean;
    }[];
  };
  decisions: DecisionRow[];
};

export function ImpactPage() {
  const [data, setData] = useState<Impact | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Impact>("/api/impact").then(setData).catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <p>Calculating business impact…</p>;

  const pilot = data.columns.find((column) => column.key === "pilot");
  const chart = [
    { name: "Margin", ...Object.fromEntries(data.columns.map((column) => [column.label, column.margin_at_risk_rm])) },
    { name: "Penalties", ...Object.fromEntries(data.columns.map((column) => [column.label, column.penalty_at_risk_rm])) },
    { name: "Programme delay", ...Object.fromEntries(data.columns.map((column) => [column.label, column.delay_cost_rm])) },
  ];
  const colors = ["#8aa0b4", "#0e6b57", "#1d4e89"];

  return (
    <div className="page">
      <PageHeader
        kicker="Baseline versus pilot"
        title="Business Impact"
        lede="Baseline is an earliest-required-date planner using the same plants, products, and dates. The pilot is the consequence-minimising allocation. Approved figures replace a bucket only after a person records a decision."
      />
      <div className="banner good">
        <strong>Estimated value protected versus earliest-date planning: {rm(data.value_protected_rm)}</strong>
        {data.value_protected_note}
      </div>
      <div className="kpi-grid">
        <Kpi label="Inventory value" value={rm(data.inventory.inventory_value_rm)} hint="Assumption. On-hand × prototype unit cost." />
        <Kpi label="Excess inventory" value={rm(data.inventory.excess_inventory_value_rm)} hint={`${m3(data.inventory.excess_inventory_m3)} above safety stock and 14-day demand.`} />
        <Kpi label="Working capital tied up" value={rm(data.inventory.working_capital_rm)} hint={data.inventory.working_capital_note} />
        <Kpi label="Margin deferred in the pilot" value={rm(pilot?.margin_at_risk_rm)} tone="risk" />
        <Kpi label="Programme days in the pilot" value={num(pilot?.programme_days, 1)} tone="risk" hint={`Delay cost ${rm(pilot?.delay_cost_rm)}`} />
        <Kpi label="Emergency actions worth pricing" value={rm(data.expedite.emergency_cost_if_clearing_worthwhile_shortfalls_rm)} hint={`Assumption. Net benefit if approved: ${rm(data.expedite.net_benefit_if_those_are_expedited_rm)}.`} />
      </div>

      <Panel title="Before and after" sub="Lost margin, penalties, and programme delay under each rule.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                {data.columns.map((column) => <th key={column.key}>{column.label}</th>)}
              </tr>
            </thead>
            <tbody>
              <tr><td>Unserved quantity</td>{data.columns.map((column) => <td key={column.key}>{m3(column.unserved_m3)}</td>)}</tr>
              <tr><td>Lost / deferred margin</td>{data.columns.map((column) => <td key={column.key}>{rm(column.margin_at_risk_rm)}</td>)}</tr>
              <tr><td>Contractual penalties</td>{data.columns.map((column) => <td key={column.key}>{rm(column.penalty_at_risk_rm)}</td>)}</tr>
              <tr><td>Programme delay impact</td>{data.columns.map((column) => <td key={column.key}>{rm(column.delay_cost_rm)}</td>)}</tr>
              <tr><td>Programme days</td>{data.columns.map((column) => <td key={column.key}>{num(column.programme_days, 1)}</td>)}</tr>
              <tr><td>Expected business consequence</td>{data.columns.map((column) => <td key={column.key}><strong>{rm(column.expected_consequence_rm)}</strong></td>)}</tr>
              <tr><td>Gross consequence</td>{data.columns.map((column) => <td key={column.key}>{rm(column.gross_consequence_rm)}</td>)}</tr>
            </tbody>
          </table>
        </div>
        <p className="note">{data.columns.map((column) => column.detail).join(" ")}</p>
        {data.approved_notes.map((note) => <p key={note} className="note">{note}</p>)}
        <div className="chart-box">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis tickFormatter={(value) => `${Math.round(Number(value) / 1000)}k`} />
              <Tooltip formatter={(value) => rm(Number(value))} />
              <Legend />
              {data.columns.map((column, index) => <Bar key={column.key} dataKey={column.label} fill={colors[index]} />)}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="What a blanket preference would cost" sub="Shown so the absence of an internal or external rule is visible.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Rule</th><th className="num">Expected consequence</th><th className="num">Programme days</th><th className="num">Unserved</th></tr>
            </thead>
            <tbody>
              {data.reference_policies.map((column) => (
                <tr key={column.key}>
                  <td>{column.label}</td>
                  <td className="num">{rm(column.expected_consequence_rm)}</td>
                  <td className="num">{num(column.programme_days, 1)}</td>
                  <td className="num">{m3(column.unserved_m3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="split">
        <Panel title="Inventory and working capital" sub="Unit values are assumptions.">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stock</th>
                  <th className="num">On hand</th>
                  <th className="num">Excess</th>
                  <th className="num">Value</th>
                </tr>
              </thead>
              <tbody>
                {data.inventory.lines.map((line) => (
                  <tr key={`${line.plant_name}-${line.product_name}`}>
                    <td>{line.plant_name}<div className="note">{line.product_name} · {rm(line.unit_value_rm)}/m³ assumption</div></td>
                    <td className="num">{m3(line.on_hand_m3)}</td>
                    <td className="num">{m3(line.excess_m3)}</td>
                    <td className="num">{rm(line.inventory_value_rm)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note">Safety stock protected: {rm(data.inventory.safety_stock_value_rm)}. Usable inventory drawn into the recommended plan, valued at the same assumption: {rm(data.inventory.inventory_consumed_value_rm)}.</p>
        </Panel>
        <Panel title="Emergency logistics and production" sub={data.expedite.note}>
          <p>Clearing only the shortfalls where emergency cost is below the consequence: {rm(data.expedite.emergency_cost_if_clearing_worthwhile_shortfalls_rm)}.</p>
          <p>Clearing the shortfalls that are not worth it would cost {rm(data.expedite.emergency_cost_if_clearing_low_consequence_shortfalls_rm)} and is not recommended.</p>
          <ul>
            {data.expedite.actions.filter((row) => row.worth_expediting).map((row) => (
              <li key={`${row.plant_name}-${row.customer_or_project}`}>{row.customer_or_project} at {row.plant_name}: {m3(row.unserved_quantity)}, emergency {rm(row.expedite_cost_rm)}, net benefit {rm(row.net_benefit_rm)}.</li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel title="Approved decisions in this impact" sub="Until a planner records a decision, the approved column stays on the recommendation.">
        {data.decisions.length === 0 ? <p className="note">No decision recorded yet.</p> : (
          <table>
            <thead>
              <tr><th>When</th><th>Planner</th><th>Scope</th><th>Status</th><th className="num">Recorded consequence</th></tr>
            </thead>
            <tbody>
              {data.decisions.map((row) => (
                <tr key={row.id}>
                  <td>{when(row.created_at)}</td>
                  <td>{row.username}</td>
                  <td>{row.plant_name}<div className="note">{row.product_name}</div></td>
                  <td>{row.status}</td>
                  <td className="num">{rm(row.consequence_final)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <Assumptions items={data.assumptions} />
    </div>
  );
}
