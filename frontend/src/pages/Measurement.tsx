import { useEffect, useState } from "react";
import { api } from "../api";
import { ErrorNote, Kpi, PageHeader, Panel } from "../components";
import { m3, rm } from "../format";
import { usePlanner } from "../planner";

type Range = { n: number; min_rm: number | null; max_rm: number | null; mean_rm: number | null };

type Measurement = {
  primary: string;
  rollout: string;
  live: {
    paired_shadow: { n: number; mean_gap_rm: number | null; inconclusive: boolean; definition: string };
    cash_paid_rm: number;
    programme_days_lost: number;
    override_share: { modified: number; decisions: number; quote_percentage: boolean };
    burden: { n: number; internal_unserved_share: number | null; external_unserved_share: number | null; internal_consequence_share: number | null };
    snapshots: number;
    cash_tied_up: { n: number; mean_30_rm: number | null; mean_60_rm: number | null; mean_90_rm: number | null; definition: string };
    expedite: { n: number; approved: number; declined: number; estimated_avoided_rm: number; label: string };
    forecast_diagnostic: { label: string; method: string; sign: string; mae_m3: number };
    override_learning: {
      ex_ante: Range & { definition: string; quote_mean: boolean };
      realised: { definition: string; overridden: Range; accepted: Range; quote_difference: boolean; note: string };
    };
  };
  illustration: {
    watermark: string;
    missing?: boolean;
    note?: string;
    informal_plan?: string;
    method?: string;
    success?: { paired_mean_gap_rm: number; failure_rule_fired: string | null; damages_per_constrained_day_baseline_rm: number | null; damages_per_constrained_day_pilot_rm: number | null };
    failure?: { paired_mean_gap_rm: number; failure_rule_fired: string | null; damages_per_constrained_day_baseline_rm: number | null; damages_per_constrained_day_pilot_rm: number | null };
  };
};

export function MeasurementPage() {
  const { name } = usePlanner();
  const [data, setData] = useState<Measurement | null>(null);
  const [error, setError] = useState("");
  const [plants, setPlants] = useState<{ id: number; name: string }[]>([]);
  const [products, setProducts] = useState<{ id: number; code: string; name: string }[]>([]);
  const [stock, setStock] = useState({ plantId: 1, productId: 3, date: "2026-10-08", onHand: 0, safety: 0 });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api<Measurement>("/api/measurement").then(setData).catch((err: Error) => setError(err.message));
  }, [tick]);

  useEffect(() => {
    api<{ plants: { id: number; name: string }[]; products: { id: number; code: string; name: string }[] }>("/api/meta")
      .then((meta) => {
        setPlants(meta.plants);
        setProducts(meta.products.filter((row) => row.code === "PCS"));
      })
      .catch(() => undefined);
  }, []);

  async function saveStock() {
    setError("");
    try {
      await api("/api/inventory-snapshots", {
        method: "POST",
        body: JSON.stringify({
          plant_id: stock.plantId,
          product_id: stock.productId,
          as_of_date: stock.date,
          on_hand: stock.onHand,
          safety_stock: stock.safety,
          entered_by: name || "Plant",
        }),
      });
      setTick((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not store the stock count.");
    }
  }

  if (error) return <ErrorNote message={error} />;
  if (!data) return <p>Loading the measurement protocol…</p>;
  const live = data.live;
  const share = live.burden.internal_unserved_share;

  return (
    <div className="page">
      <PageHeader
        kicker="Pre-registered"
        title="Measurement"
        lede={data.primary}
      />
      <p className="note">{data.rollout}</p>
      <div className="kpi-grid">
        <Kpi label="Paired shadow decisions" value={String(live.paired_shadow.n)} hint={live.paired_shadow.inconclusive ? "Inconclusive below 8 pairs." : `Mean gap ${rm(live.paired_shadow.mean_gap_rm || 0)}`} />
        <Kpi label="Cash paid on LDs" value={rm(live.cash_paid_rm)} hint="Penalty actually paid. Not modelled penalty." />
        <Kpi label="Programme days lost" value={String(live.programme_days_lost)} hint="From the site diary fields on actuals." />
        <Kpi label="Weekly stock counts" value={String(live.snapshots)} hint="Excess precast cash uses these counts, not the 1 Oct snapshot." />
      </div>
      <Panel title="Paired shadow gap" sub={live.paired_shadow.definition}>
        <p>{live.paired_shadow.inconclusive ? `n = ${live.paired_shadow.n}. Inconclusive until 8 paired decisions.` : `Mean ${rm(live.paired_shadow.mean_gap_rm || 0)} across ${live.paired_shadow.n} pairs.`}</p>
      </Panel>
      <Panel title="Burden guard" sub="Share of unserved cubic metres and of modelled consequence. A shift of pain is reported, not hidden.">
        <p>{live.burden.n === 0 ? "No decision recorded yet." : `Internal share of unserved m³: ${share == null ? "—" : `${Math.round(share * 100)}%`}. External share: ${live.burden.external_unserved_share == null ? "—" : `${Math.round(live.burden.external_unserved_share * 100)}%`}. Internal share of expected consequence: ${live.burden.internal_consequence_share == null ? "—" : `${Math.round(live.burden.internal_consequence_share * 100)}%`}. n = ${live.burden.n}.`}</p>
      </Panel>
      <Panel title="Cash tied up in excess precast" sub={live.cash_tied_up.definition}>
        <p>{live.cash_tied_up.n === 0 ? "No weekly stock count yet." : `n = ${live.cash_tied_up.n}. Mean excess cash at 30 days ${rm(live.cash_tied_up.mean_30_rm || 0)}, at 60 days ${rm(live.cash_tied_up.mean_60_rm || 0)}, at 90 days ${rm(live.cash_tied_up.mean_90_rm || 0)}.`}</p>
        <div className="field"><label>Plant</label>
          <select value={stock.plantId} onChange={(event) => setStock({ ...stock, plantId: Number(event.target.value) })}>
            {plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
          </select>
        </div>
        <div className="field"><label>Precast product</label>
          <select value={stock.productId} onChange={(event) => setStock({ ...stock, productId: Number(event.target.value) })}>
            {products.map((product) => <option key={product.id} value={product.id}>{product.code}</option>)}
          </select>
        </div>
        <div className="field"><label>Count date</label><input type="date" value={stock.date} onChange={(event) => setStock({ ...stock, date: event.target.value })} /></div>
        <div className="field"><label>On hand m³</label><input type="number" min={0} value={stock.onHand} onChange={(event) => setStock({ ...stock, onHand: Number(event.target.value) })} /></div>
        <div className="field"><label>Safety stock m³</label><input type="number" min={0} value={stock.safety} onChange={(event) => setStock({ ...stock, safety: Number(event.target.value) })} /></div>
        <button className="btn" onClick={() => void saveStock()}>Store weekly stock count as {name || "plant"}</button>
      </Panel>
      <Panel title="Override learning" sub="Ex-ante is paired. Realised shows n and a range. There is no win rate.">
        <p>{live.override_learning.ex_ante.definition}</p>
        <p>Ex-ante n = {live.override_learning.ex_ante.n}{live.override_learning.ex_ante.n > 0 ? `, range ${rm(live.override_learning.ex_ante.min_rm || 0)} to ${rm(live.override_learning.ex_ante.max_rm || 0)}` : ""}. {live.override_learning.ex_ante.quote_mean ? "" : "Mean is not quoted below 8 overrides."}</p>
        <p>{live.override_learning.realised.note}</p>
        <p>Overridden n = {live.override_learning.realised.overridden.n}. Accepted n = {live.override_learning.realised.accepted.n}.</p>
      </Panel>
      <Panel title="Expedite records" sub="Estimated avoided is counterfactual. The page shows counts.">
        <p>n = {live.expedite.n}. Approved {live.expedite.approved}. Declined {live.expedite.declined}. {live.expedite.label}: {rm(live.expedite.estimated_avoided_rm)}.</p>
      </Panel>
      <Panel title="Forecast error" sub={live.forecast_diagnostic.label}>
        <p>{live.forecast_diagnostic.method}</p>
        <p>{live.forecast_diagnostic.sign} MAE {m3(live.forecast_diagnostic.mae_m3)}.</p>
      </Panel>
      <Panel title={data.illustration.watermark || "Synthetic illustration"} sub="Not results. Kept off Control Tower.">
        {data.illustration.missing ? <p>{data.illustration.note}</p> : (
          <>
            <p>{data.illustration.informal_plan}</p>
            <p>{data.illustration.method}</p>
            <p>Illustrated run: paired mean {rm(data.illustration.success?.paired_mean_gap_rm || 0)}. Failure rule: {data.illustration.success?.failure_rule_fired || "not fired"}.</p>
            <p>Second run, built to fail: {data.illustration.failure?.failure_rule_fired || "the failure rule did not fire"}.</p>
          </>
        )}
      </Panel>
    </div>
  );
}
