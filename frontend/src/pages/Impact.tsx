import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Assumptions, EmptyNote, ErrorNote, Kpi, PageHeader, Panel } from "../components";
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
  upper_bound: { label: string; detail: string; gap_rm: number; expected_consequence_rm: number };
  value_protected_vs_earliest_rm?: number;
  best_rule_by_bucket?: { plant_name: string; product_name: string; best_rule_label?: string; gap_rm: number }[];
  verify_contracts?: {
    order_count: number;
    order_count_note: string;
    note: string;
    lines: {
      demand_code: string;
      customer_or_project: string;
      to_penalty_type: string;
      to_delay_type: string;
      gap_change_rm: number;
      allocation_changed: boolean;
      m3_moved: number;
      statement: string;
    }[];
  };
  stress?: {
    constrained_share: number;
    synthetic_constrained_months_per_year: number;
    unconditional: { mean_rm: number; p10_rm: number; p50_rm: number; p90_rm: number; share_gap_positive: number };
    given_constrained: { mean_rm: number; p10_rm: number; p50_rm: number; p90_rm: number };
    annualised_synthetic: { mean_rm: number; p10_rm: number; p90_rm: number; label: string };
    caveat: string;
  } | null;
  value_protected_range: {
    low_rm: number;
    base_rm: number;
    high_rm: number;
    all_linear_rm?: number;
    seeded_rm?: number;
    all_lump_rm?: number;
    earliest_gap_rm?: number;
    gap_nonnegative_note?: string;
    practice_proxy_gap_rm: number;
    practice_proxy_note: string;
    formula: string;
  };
  approved_notes: string[];
  assumptions: string[];
  inventory: {
    inventory_value_rm: number;
    safety_stock_value_rm: number;
    excess_inventory_m3: number;
    excess_inventory_value_rm: number;
    working_capital_rm: number;
    carrying_cost_rm: number;
    excess_cover_days: number;
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
  const [realMonths, setRealMonths] = useState<number | null>(null);

  useEffect(() => {
    api<Impact>("/api/impact").then(setData).catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <p>Calculating business impact…</p>;

  if (data.columns.length === 0) {
    return (
      <div className="page">
        <PageHeader kicker="Same demand, same capacity" title="Business Impact" lede="No impact columns are loaded." />
        <EmptyNote message="No impact columns are loaded." />
      </div>
    );
  }
  const pilot = data.columns.find((column) => column.key === "pilot");
  const band = data.value_protected_range;
  const chart = [
    { name: "Margin", ...Object.fromEntries(data.columns.map((column) => [column.label, column.margin_at_risk_rm])) },
    { name: "Penalties", ...Object.fromEntries(data.columns.map((column) => [column.label, column.penalty_at_risk_rm])) },
    { name: "Programme delay", ...Object.fromEntries(data.columns.map((column) => [column.label, column.delay_cost_rm])) },
  ];
  const colors = ["#8aa0b4", "#1d4e89", "#0e6b57", "#6b5b4b"];

  return (
    <div className="page">
      <PageHeader
        kicker="Same demand, same capacity"
        title="Business Impact"
        lede="Every column uses the same plants, products, dates, inventory, and financial assumptions. Only the allocation rule changes. The headline comparison is the best simple rule. Earliest required date is second. The difference is not observed savings."
      />
      <div className="banner good">
        <strong>Modelled gap versus the best simple rule: {rm(data.value_protected_rm)}</strong>
        Best-rule expected consequence {rm(data.columns.find((column) => column.key === "best")?.expected_consequence_rm)} minus the recommendation {rm(pilot?.expected_consequence_rm)}. All-linear {rm(band.all_linear_rm ?? band.low_rm)}. Seeded mix {rm(band.seeded_rm ?? band.base_rm)}. All-lump {rm(band.all_lump_rm ?? band.high_rm)}. Earliest-date gap {rm(data.value_protected_vs_earliest_rm ?? 0)}.
      </div>
      <Panel title="How this number is calculated" sub="Synthetic orders and stated assumptions. Not cash saved.">
        <p>{band.formula}</p>
        <p className="note">{band.gap_nonnegative_note}</p>
        <p className="note">Practice-proxy footnote: {rm(data.upper_bound.gap_rm)}. {data.upper_bound.detail}</p>
      </Panel>
      {data.best_rule_by_bucket && data.best_rule_by_bucket.length > 0 ? (
        <Panel title="Winning simple rule by plant and product" sub="The headline is the sum of these gaps. Each bucket uses whichever rule scores lowest under the seeded terms.">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Plant</th>
                  <th>Product</th>
                  <th>Winner</th>
                  <th className="num">Gap</th>
                </tr>
              </thead>
              <tbody>
                {data.best_rule_by_bucket.map((row) => (
                  <tr key={`${row.plant_name}-${row.product_name}`}>
                    <td>{row.plant_name}</td>
                    <td>{row.product_name}</td>
                    <td>{row.best_rule_label}</td>
                    <td className="num">{rm(row.gap_rm)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
      {data.verify_contracts ? (
        <Panel title="Verify these contracts first" sub={data.verify_contracts.note}>
          <p className="note">{data.verify_contracts.order_count_note} {data.verify_contracts.order_count} lines.</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Flipped to</th>
                  <th className="num">m³ moved</th>
                  <th className="num">Gap change</th>
                  <th>Allocation</th>
                </tr>
              </thead>
              <tbody>
                {data.verify_contracts.lines.map((row) => (
                  <tr key={row.demand_code}>
                    <td>{row.customer_or_project}</td>
                    <td>{row.to_penalty_type} / {row.to_delay_type}</td>
                    <td className="num">{m3(row.m3_moved)}</td>
                    <td className="num">{rm(row.gap_change_rm)}</td>
                    <td>{row.allocation_changed ? row.statement : "Score only"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
      {data.stress ? (
        <Panel title="Synthetic months" sub="200 seeded months. Not a forecast of this plant.">
          <p>
            Constrained in {Math.round(data.stress.constrained_share * 100)}% of months.
            Unconditional gap p10 {rm(data.stress.unconditional.p10_rm)}, p50 {rm(data.stress.unconditional.p50_rm)}, p90 {rm(data.stress.unconditional.p90_rm)}.
            Given a constrained month, mean {rm(data.stress.given_constrained.mean_rm)}.
          </p>
          <p>Synthetic annualised range, 12 × the unconditional monthly gap: {rm(data.stress.annualised_synthetic.p10_rm)} to {rm(data.stress.annualised_synthetic.p90_rm)}, mean {rm(data.stress.annualised_synthetic.mean_rm)}. {data.stress.annualised_synthetic.label}</p>
          <label>
            Real constrained months per year
            <input
              type="number"
              min={0}
              max={12}
              step={0.1}
              value={realMonths ?? data.stress.synthetic_constrained_months_per_year}
              onChange={(event) => setRealMonths(Number(event.target.value))}
            />
          </label>
          <p>
            At that frequency the annual figure is {rm((realMonths ?? data.stress.synthetic_constrained_months_per_year) * data.stress.given_constrained.mean_rm)}.
            It replaces the synthetic mix of quiet and short months with the plant's own frequency.
          </p>
          <p className="note">{data.stress.caveat}</p>
        </Panel>
      ) : (
        <p className="note">The 200-month stress report has not been written yet. Run python -m app.stress from the backend.</p>
      )}
      <div className="kpi-grid">
        <Kpi label="Inventory value" value={rm(data.inventory.inventory_value_rm)} hint="On-hand × assumed unit cost. This balance is not the carrying cost." />
        <Kpi label="Excess inventory" value={rm(data.inventory.excess_inventory_value_rm)} hint={`${m3(data.inventory.excess_inventory_m3)} of precast above safety stock and ${data.inventory.excess_cover_days}-day demand.`} />
        <Kpi label="Carrying cost this horizon" value={rm(data.inventory.carrying_cost_rm)} hint={data.inventory.working_capital_note} />
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

      <Panel title="What a blanket preference would cost" sub="Internal-first and external-first are in the best-of set. This table is the cost of applying one of them to every plant and product.">
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
