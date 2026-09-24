import { useEffect, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { ErrorNote, PageHeader, Panel } from "../components";
import { longDate, m3, rm, shortDate } from "../format";
import type { Meta } from "../types";

type Series = {
  date: string;
  daily_capacity_m3: number;
  planned_production_m3: number;
  available_capacity_m3: number;
  internal_demand_due_m3: number;
  external_demand_due_m3: number;
  cumulative_supply_m3: number;
  cumulative_demand_m3: number;
  cumulative_gap_m3: number;
  constrained: boolean;
  note: string;
};

type View = {
  plant: { name: string };
  product: { name: string };
  inventory: { stockable: boolean; on_hand_m3: number; safety_stock_m3: number; usable_m3: number; inventory_value_rm: number; value_is_assumption: boolean };
  formulas: Record<string, string>;
  series: Series[];
  constrained_dates: string[];
  unserved_orders: { demand_code: string; customer_or_project: string; required_date: string; unserved_quantity: number }[];
  shortfall_m3: number;
  note: string;
};

export function CapacityPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [plantId, setPlantId] = useState(1);
  const [productId, setProductId] = useState(1);
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Meta>("/api/meta").then(setMeta).catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    api<View>(`/api/capacity?plant_id=${plantId}&product_id=${productId}`)
      .then(setView)
      .catch((err: Error) => setError(err.message));
  }, [plantId, productId]);

  const chart = (view?.series ?? []).map((row) => ({ ...row, label: shortDate(row.date) }));

  return (
    <div className="page">
      <PageHeader
        kicker="Supply by date"
        title="Capacity Planner"
        lede="Available capacity is what remains after production already committed outside this demand book. Ready-mix cannot be stocked. Precast usable inventory is on-hand stock above safety stock."
      />
      {error ? <ErrorNote message={error} /> : null}
      <div className="filters">
        <div className="field">
          <label>Plant</label>
          <select value={plantId} onChange={(event) => setPlantId(Number(event.target.value))}>
            {meta?.plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Product</label>
          <select value={productId} onChange={(event) => setProductId(Number(event.target.value))}>
            {meta?.products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
          </select>
        </div>
      </div>
      {view ? (
        <>
          {view.inventory.stockable ? (
            <div className="kpi-grid">
              <article className="kpi"><p>On hand</p><strong>{m3(view.inventory.on_hand_m3)}</strong><span>Assumption value {rm(view.inventory.inventory_value_rm)}</span></article>
              <article className="kpi"><p>Safety stock</p><strong>{m3(view.inventory.safety_stock_m3)}</strong><span>Reserved. Not allocated.</span></article>
              <article className="kpi"><p>Usable inventory</p><strong>{m3(view.inventory.usable_m3)}</strong><span>Added to available capacity.</span></article>
            </div>
          ) : (
            <p className="note">Ready-mix cannot be stocked. On-hand, safety stock, and usable inventory are not used for this product.</p>
          )}
          <Panel title="Formulas">
            <div className="detail">
              {Object.entries(view.formulas).map(([key, formula]) => <p key={key}><strong>{key.replaceAll("_", " ")}:</strong> {formula}</p>)}
            </div>
          </Panel>
          <Panel title={`${view.plant.name} · ${view.product.name}`} sub="Bars are orders due that day. The line is uncommitted capacity.">
            <div className="chart-box">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chart}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="label" interval={2} />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="internal_demand_due_m3" name="Internal due" stackId="demand" fill="#1d4e89" />
                  <Bar dataKey="external_demand_due_m3" name="External due" stackId="demand" fill="#0e6b57" />
                  <Line dataKey="available_capacity_m3" name="Available capacity" stroke="#8a5a00" strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Panel>
          <Panel title="Cumulative supply against cumulative demand" sub={view.note}>
            <div className="chart-box">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chart}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="label" interval={2} />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="cumulative_supply_m3" name="Cumulative supply" stroke="#1d4e89" strokeWidth={2} dot={false} />
                  <Line dataKey="cumulative_demand_m3" name="Cumulative demand due" stroke="#9f1239" strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Panel>
          <div className="split">
            <Panel title="Dates where volume due runs ahead of supply" sub={`${view.constrained_dates.length} dates highlighted.`}>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th className="num">Capacity</th>
                      <th className="num">Planned</th>
                      <th className="num">Available</th>
                      <th className="num">Demand due</th>
                      <th className="num">Cumulative gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.series.filter((row) => row.constrained || row.note || row.internal_demand_due_m3 + row.external_demand_due_m3 > 0).map((row) => (
                      <tr key={row.date} className={row.constrained ? "constrained" : ""}>
                        <td className="nowrap">{longDate(row.date)}{row.note ? <div className="note">{row.note}</div> : null}</td>
                        <td className="num">{m3(row.daily_capacity_m3)}</td>
                        <td className="num">{m3(row.planned_production_m3)}</td>
                        <td className="num">{m3(row.available_capacity_m3)}</td>
                        <td className="num">{m3(row.internal_demand_due_m3 + row.external_demand_due_m3)}</td>
                        <td className="num">{row.cumulative_gap_m3 > 0 ? m3(row.cumulative_gap_m3) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel title="Still unserved after the optimiser" sub={`Dated shortfall ${m3(view.shortfall_m3)}. Later capacity does not refill a missed date.`}>
              {view.unserved_orders.length === 0 ? <p>This plant and product can cover its book.</p> : (
                <ul>
                  {view.unserved_orders.map((row) => (
                    <li key={row.demand_code}>{row.customer_or_project}: {m3(row.unserved_quantity)} due {longDate(row.required_date)}</li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      ) : <p>Loading capacity…</p>}
    </div>
  );
}
