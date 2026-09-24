import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { EmptyNote, ErrorNote, PageHeader, Panel } from "../components";
import { m3, num, rm } from "../format";
import type { Meta, MetaDemand } from "../types";

type Draft = {
  name: string;
  capacity_factor: number;
  plant_id: number | "";
  product_id: number | "";
  demand_id: number | "";
  requested_quantity: string;
  required_date: string;
  contribution_margin: string;
  contractual_penalty: string;
  delay_days_if_unserved: string;
  delay_cost_per_day: string;
  project_criticality: string;
  confidence_level: string;
  inventory_plant_id: number | "";
  inventory_product_id: number | "";
  on_hand_delta: string;
};

type ComparisonRow = {
  name: string;
  notes: string[];
  unserved_m3: number;
  margin_at_risk_rm: number;
  programme_days: number;
  expected_consequence_rm: number;
  gross_consequence_rm: number;
  delay_cost_rm: number;
  penalty_at_risk_rm: number;
  delta_versus_first_rm: number;
};

type RunBucket = {
  plant_id: number;
  product_id: number;
  plant_name: string;
  product_name: string;
  constrained: boolean;
  shortfall_m3: number;
  allocations: { demand_code: string; customer_or_project: string; demand_type: string; allocated_quantity: number; unserved_quantity: number; requested_quantity: number }[];
  explanation: { tradeoff: string };
};

function blank(name: string): Draft {
  return {
    name,
    capacity_factor: 1,
    plant_id: "",
    product_id: "",
    demand_id: "",
    requested_quantity: "",
    required_date: "",
    contribution_margin: "",
    contractual_penalty: "",
    delay_days_if_unserved: "",
    delay_cost_per_day: "",
    project_criticality: "",
    confidence_level: "",
    inventory_plant_id: "",
    inventory_product_id: "",
    on_hand_delta: "",
  };
}

function toPayload(draft: Draft) {
  const adjustment: Record<string, string | number> = {};
  if (draft.demand_id !== "") adjustment.demand_id = Number(draft.demand_id);
  if (draft.requested_quantity !== "") adjustment.requested_quantity = Number(draft.requested_quantity);
  if (draft.required_date) adjustment.required_date = draft.required_date;
  if (draft.contribution_margin !== "") adjustment.contribution_margin = Number(draft.contribution_margin);
  if (draft.contractual_penalty !== "") adjustment.contractual_penalty = Number(draft.contractual_penalty);
  if (draft.delay_days_if_unserved !== "") adjustment.delay_days_if_unserved = Number(draft.delay_days_if_unserved);
  if (draft.delay_cost_per_day !== "") adjustment.delay_cost_per_day = Number(draft.delay_cost_per_day);
  if (draft.project_criticality) adjustment.project_criticality = draft.project_criticality;
  if (draft.confidence_level) adjustment.confidence_level = draft.confidence_level;
  const demand_adjustments = adjustment.demand_id ? [adjustment] : [];
  const inventory_adjustments = draft.on_hand_delta !== "" && draft.inventory_plant_id !== "" && draft.inventory_product_id !== ""
    ? [{ plant_id: Number(draft.inventory_plant_id), product_id: Number(draft.inventory_product_id), on_hand_delta_m3: Number(draft.on_hand_delta) }]
    : [];
  return {
    name: draft.name,
    capacity_factor: Number(draft.capacity_factor),
    plant_id: draft.plant_id === "" ? null : Number(draft.plant_id),
    product_id: draft.product_id === "" ? null : Number(draft.product_id),
    inventory_adjustments,
    demand_adjustments,
  };
}

export function ScenarioPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([blank("A · Baseline"), blank("B · Overtime"), blank("C · No Merdeka delay cost")]);
  const [rows, setRows] = useState<ComparisonRow[]>([]);
  const [runs, setRuns] = useState<{ scenario_name: string; buckets: RunBucket[] }[]>([]);
  const [focus, setFocus] = useState("1-1");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Meta>("/api/meta").then((payload) => {
      setMeta(payload);
      const merdeka = payload.demands.find((row) => row.demand_code === "INT-MERDEKA");
      const next: [Draft, Draft, Draft] = [blank("A · Baseline"), blank("B · Shah Alam overtime"), blank("C · Merdeka delay cost removed")];
      const overtime = next[1];
      const withoutDelayCost = next[2];
      overtime.capacity_factor = 1.15;
      overtime.plant_id = 1;
      overtime.product_id = 1;
      if (merdeka) {
        withoutDelayCost.demand_id = merdeka.id;
        withoutDelayCost.delay_cost_per_day = "0";
      }
      setDrafts(next);
      void compare(next);
    }).catch((err: Error) => setError(err.message));
    // compare is stable enough for the first load
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function compare(source = drafts) {
    setBusy(true);
    setError("");
    try {
      const payload = await api<{ comparison: ComparisonRow[]; runs: { scenario_name: string; buckets: RunBucket[] }[] }>("/api/scenarios/compare", {
        method: "POST",
        body: JSON.stringify({ scenarios: source.map(toPayload) }),
      });
      setRows(payload.comparison);
      setRuns(payload.runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scenario run failed.");
    } finally {
      setBusy(false);
    }
  }

  function update(index: number, patch: Partial<Draft>) {
    setDrafts(drafts.map((draft, position) => (position === index ? { ...draft, ...patch } : draft)));
  }

  function fillFromDemand(index: number, demand: MetaDemand) {
    update(index, {
      demand_id: demand.id,
      requested_quantity: String(demand.requested_quantity),
      required_date: demand.required_date,
      contribution_margin: String(demand.contribution_margin),
      contractual_penalty: String(demand.contractual_penalty),
      delay_days_if_unserved: String(demand.delay_days_if_unserved),
      delay_cost_per_day: String(demand.delay_cost_per_day),
      project_criticality: demand.project_criticality,
      confidence_level: demand.confidence_level,
    });
  }

  const chart = rows.map((row) => ({
    name: row.name.split("·")[0]?.trim() ?? row.name,
    Unserved: row.unserved_m3,
    Consequence: Math.round(row.expected_consequence_rm),
  }));
  const focused = runs.map((run) => ({
    name: run.scenario_name,
    bucket: run.buckets.find((bucket) => `${bucket.plant_id}-${bucket.product_id}` === focus),
  }));

  return (
    <div className="page">
      <PageHeader
        kicker="Change the facts, rerun the engine"
        title="Scenario Simulator"
        lede="Capacity, quantity, required date, inventory, delay cost, margin, and criticality can be changed. Each column is a fresh linear programme, not a manual edit of the last answer."
      />
      {error ? <ErrorNote message={error} /> : null}
      {meta && meta.plants.length === 0 ? <EmptyNote message="No plants are loaded, so a scenario has nothing to change." /> : null}
      <div className="scenario-grid">
        {drafts.map((draft, index) => (
          <section key={index} className="scenario-card">
            <h3>Scenario {String.fromCharCode(65 + index)}</h3>
            <div className="mini">
              <input value={draft.name} onChange={(event) => update(index, { name: event.target.value })} />
              <div className="field">
                <label>Available capacity factor ({Math.round(draft.capacity_factor * 100)}%)</label>
                <input type="range" min={0.5} max={1.5} step={0.05} value={draft.capacity_factor} onChange={(event) => update(index, { capacity_factor: Number(event.target.value) })} />
              </div>
              <div className="field">
                <label>Apply factor to plant</label>
                <select value={draft.plant_id} onChange={(event) => update(index, { plant_id: event.target.value === "" ? "" : Number(event.target.value) })}>
                  <option value="">All plants</option>
                  {meta?.plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label>And product</label>
                <select value={draft.product_id} onChange={(event) => update(index, { product_id: event.target.value === "" ? "" : Number(event.target.value) })}>
                  <option value="">All products</option>
                  {meta?.products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Order to edit</label>
                <select
                  value={draft.demand_id}
                  onChange={(event) => {
                    const demand = meta?.demands.find((row) => row.id === Number(event.target.value));
                    if (demand) fillFromDemand(index, demand);
                    else update(index, { demand_id: "" });
                  }}
                >
                  <option value="">None</option>
                  {meta?.demands.map((demand) => <option key={demand.id} value={demand.id}>{demand.demand_code} · {demand.customer_or_project}</option>)}
                </select>
              </div>
              {draft.demand_id !== "" ? (
                <>
                  <div className="field"><label>Quantity m³</label><input value={draft.requested_quantity} onChange={(event) => update(index, { requested_quantity: event.target.value })} /></div>
                  <div className="field"><label>Required date</label><input type="date" value={draft.required_date} onChange={(event) => update(index, { required_date: event.target.value })} /></div>
                  <div className="field"><label>Contribution margin RM</label><input value={draft.contribution_margin} onChange={(event) => update(index, { contribution_margin: event.target.value })} /></div>
                  <div className="field"><label>Penalty RM</label><input value={draft.contractual_penalty} onChange={(event) => update(index, { contractual_penalty: event.target.value })} /></div>
                  <div className="field"><label>Delay days if unserved</label><input value={draft.delay_days_if_unserved} onChange={(event) => update(index, { delay_days_if_unserved: event.target.value })} /></div>
                  <div className="field"><label>Delay cost RM / day</label><input value={draft.delay_cost_per_day} onChange={(event) => update(index, { delay_cost_per_day: event.target.value })} /></div>
                  <div className="field">
                    <label>Criticality</label>
                    <select value={draft.project_criticality} onChange={(event) => update(index, { project_criticality: event.target.value })}>
                      <option value="">Unchanged</option>
                      {meta?.criticality_levels.map((level) => <option key={level}>{level}</option>)}
                    </select>
                  </div>
                  <p className="note">Margin and penalty are totals for the order. Criticality rescales delay cost unless you also type a delay cost.</p>
                </>
              ) : null}
              <div className="field">
                <label>Inventory change m³</label>
                <input value={draft.on_hand_delta} placeholder="e.g. -40" onChange={(event) => update(index, { on_hand_delta: event.target.value })} />
              </div>
              {draft.on_hand_delta !== "" ? (
                <>
                  <select value={draft.inventory_plant_id} onChange={(event) => update(index, { inventory_plant_id: event.target.value === "" ? "" : Number(event.target.value) })}>
                    <option value="">Plant</option>
                    {meta?.plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
                  </select>
                  <select value={draft.inventory_product_id} onChange={(event) => update(index, { inventory_product_id: event.target.value === "" ? "" : Number(event.target.value) })}>
                    <option value="">Product</option>
                    {meta?.products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
                  </select>
                </>
              ) : null}
            </div>
          </section>
        ))}
      </div>
      <div className="btn-row" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={busy} onClick={() => void compare()}>{busy ? "Running…" : "Rerun allocation for A, B, and C"}</button>
      </div>

      {rows.length > 0 ? (
        <>
          <Panel title="Outcome" sub="Allocation, unserved quantity, margin, programme days, and total business consequence.">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Metric</th>
                    {rows.map((row) => <th key={row.name}>{row.name}</th>)}
                  </tr>
                </thead>
                <tbody>
                  <tr><td>Unserved</td>{rows.map((row) => <td key={row.name}>{m3(row.unserved_m3)}</td>)}</tr>
                  <tr><td>Margin impact</td>{rows.map((row) => <td key={row.name}>{rm(row.margin_at_risk_rm)}</td>)}</tr>
                  <tr><td>Penalties</td>{rows.map((row) => <td key={row.name}>{rm(row.penalty_at_risk_rm)}</td>)}</tr>
                  <tr><td>Programme delay cost</td>{rows.map((row) => <td key={row.name}>{rm(row.delay_cost_rm)}</td>)}</tr>
                  <tr><td>Programme days</td>{rows.map((row) => <td key={row.name}>{num(row.programme_days, 1)}</td>)}</tr>
                  <tr><td>Total expected consequence</td>{rows.map((row) => <td key={row.name}><strong>{rm(row.expected_consequence_rm)}</strong></td>)}</tr>
                  <tr><td>Gross consequence</td>{rows.map((row) => <td key={row.name}>{rm(row.gross_consequence_rm)}</td>)}</tr>
                  <tr><td>Versus scenario A</td>{rows.map((row) => <td key={row.name}>{rm(row.delta_versus_first_rm)}</td>)}</tr>
                </tbody>
              </table>
            </div>
            {rows.map((row) => row.notes.map((note) => <p key={note} className="note">{note}</p>))}
          </Panel>
          <Panel title="Expected consequence" sub="The engine is rerun. A lower bar is a smaller business hit.">
            <div className="chart-box short">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="Consequence" fill="#9f1239" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Panel>
          <Panel title="Allocation by scenario" action={
            <select value={focus} onChange={(event) => setFocus(event.target.value)}>
              {runs[0]?.buckets.filter((bucket) => bucket.constrained).map((bucket) => (
                <option key={`${bucket.plant_id}-${bucket.product_id}`} value={`${bucket.plant_id}-${bucket.product_id}`}>
                  {bucket.plant_name} · {bucket.product_name}
                </option>
              ))}
            </select>
          }>
            <div className="scenario-grid">
              {focused.map((item) => (
                <div key={item.name}>
                  <h3>{item.name}</h3>
                  {item.bucket ? (
                    <>
                      <p className="note">Shortfall {m3(item.bucket.shortfall_m3)}. {item.bucket.explanation.tradeoff}</p>
                      <table>
                        <tbody>
                          {item.bucket.allocations.map((line) => (
                            <tr key={line.demand_code}>
                              <td>{line.customer_or_project}<div className="note">{line.demand_type}</div></td>
                              <td className="num">{m3(line.allocated_quantity)} / {m3(line.requested_quantity)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  ) : <p>No book selected.</p>}
                </div>
              ))}
            </div>
          </Panel>
        </>
      ) : null}
    </div>
  );
}
