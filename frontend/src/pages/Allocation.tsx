import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Assumptions, ErrorNote, Kpi, PageHeader, Panel } from "../components";
import { longDate, m3, rm, when } from "../format";
import { usePlanner } from "../planner";
import type { AllocationResult, Bucket, DecisionRow } from "../types";

type Preview = {
  feasible: boolean;
  message: string;
  changed_from_recommendation: boolean;
  delta_versus_recommendation: { expected_consequence_rm: number; gross_consequence_rm: number; unserved_m3: number; programme_days: number } | null;
};

const BASELINE = { name: "Baseline", capacity_factor: 1, plant_id: null, product_id: null, inventory_adjustments: [], demand_adjustments: [] };

type Fragility = {
  fragile: boolean;
  summary: string;
  flip_point: string;
};

export function AllocationPage() {
  const { name, role } = usePlanner();
  const [params, setParams] = useSearchParams();
  const [result, setResult] = useState<AllocationResult | null>(null);
  const [decisions, setDecisions] = useState<DecisionRow[]>([]);
  const [key, setKey] = useState("");
  const [edits, setEdits] = useState<Record<number, number>>({});
  const [reason, setReason] = useState("Accepted the recommendation.");
  const [category, setCategory] = useState("Other");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [fragility, setFragility] = useState<Fragility | null>(null);
  const [chosenPlan, setChosenPlan] = useState("");
  const [termLines, setTermLines] = useState<{ statement: string }[]>([]);
  const [termsConfirmed, setTermsConfirmed] = useState(false);
  const [shadowForm, setShadowForm] = useState<{ plantId: number; productId: number; rows: { id: number; customer_or_project: string; requested_quantity: number }[] } | null>(null);
  const [shadowQty, setShadowQty] = useState<Record<number, number>>({});
  const [reveal, setReveal] = useState(0);
  const [bufferDays, setBufferDays] = useState(0);
  const [catalog, setCatalog] = useState<{ plants: { id: number; name: string }[]; products: { id: number; code: string }[] }>({ plants: [], products: [] });
  const [actualDraft, setActualDraft] = useState<Record<number, { delivered: number; date: string; days: number; penalty: number }>>({});
  const [pairedNote, setPairedNote] = useState("");
  const [expediteOrder, setExpediteOrder] = useState("");
  const [expediteDraft, setExpediteDraft] = useState({ rate: 0, volume: 0, would: 0, paid: 0, full: false, onTime: false, decision: "approve" });
  const [signChoice, setSignChoice] = useState<"recommended" | "proposed">("recommended");
  const [signReason, setSignReason] = useState("");
  const [constraintKind, setConstraintKind] = useState<"cap" | "reserve" | "days">("cap");
  const [constraintQty, setConstraintQty] = useState(10);
  const [constraintEvidence, setConstraintEvidence] = useState("site diary");
  const [constraintDemand, setConstraintDemand] = useState(0);
  const [consultNote, setConsultNote] = useState("");

  function refreshDecisions() {
    api<{ rows: DecisionRow[] }>("/api/decisions").then((payload) => setDecisions(payload.rows)).catch(() => undefined);
  }

  useEffect(() => {
    let cancel = false;
    const plant = Number(params.get("plant")) || 1;
    const product = Number(params.get("product")) || 1;
    api<{ plants: { id: number; mode: string }[]; buffer_days: number }>("/api/operations")
      .then(async (ops) => {
        if (!cancel) setBufferDays(ops.buffer_days);
        const mode = ops.plants.find((item) => item.id === plant)?.mode ?? "shadow";
        if (mode === "shadow") {
          const plans = await api<{ rows: { id: number }[] }>(`/api/informal-plans?plant_id=${plant}&product_id=${product}`);
          if (plans.rows.length === 0) {
            const demands = await api<{ rows: { id: number; customer_or_project: string; requested_quantity: number }[] }>(`/api/demands?plant_id=${plant}&product_id=${product}`);
            if (!cancel) {
              setShadowForm({ plantId: plant, productId: product, rows: demands.rows });
              const next: Record<number, number> = {};
              demands.rows.forEach((row) => { next[row.id] = 0; });
              setShadowQty(next);
              setResult(null);
            }
            return;
          }
        }
        const payload = await api<AllocationResult>("/api/allocate", { method: "POST", body: JSON.stringify(BASELINE) });
        if (cancel) return;
        setShadowForm(null);
        setResult(payload);
        const match = payload.buckets.find((bucket) => bucket.plant_id === plant && bucket.product_id === product);
        const first = match ?? payload.buckets.find((bucket) => bucket.constrained) ?? payload.buckets[0];
        if (first) setKey(`${first.plant_id}-${first.product_id}`);
      })
      .catch((err: Error) => { if (!cancel) setError(err.message); });
    refreshDecisions();
    return () => { cancel = true; };
  }, [params, reveal]);

  useEffect(() => {
    api<{ plants: { id: number; name: string }[]; products: { id: number; code: string }[] }>("/api/meta")
      .then((meta) => setCatalog({ plants: meta.plants, products: meta.products }))
      .catch(() => undefined);
  }, []);

  const bucket: Bucket | undefined = useMemo(
    () => result?.buckets.find((item) => `${item.plant_id}-${item.product_id}` === key),
    [result, key],
  );

  useEffect(() => {
    if (!bucket) return;
    api<{ rows: { paired_gap_rm: number; informal_expected_rm: number; recommendation_expected_rm: number }[] }>(
      `/api/informal-plans?plant_id=${bucket.plant_id}&product_id=${bucket.product_id}`,
    )
      .then((payload) => {
        const row = payload.rows[0];
        setPairedNote(row ? `Modelled consequence of the informal plan ${rm(row.informal_expected_rm)} minus the recommendation ${rm(row.recommendation_expected_rm)}. Paired gap ${rm(row.paired_gap_rm)}. Not an observed outcome.` : "");
      })
      .catch(() => setPairedNote(""));
  }, [bucket]);

  useEffect(() => {
    if (!bucket) return;
    setFragility(null);
    setChosenPlan("");
    setTermsConfirmed(false);
    setTermLines([]);
    api<Fragility>(`/api/fragility?plant_id=${bucket.plant_id}&product_id=${bucket.product_id}`)
      .then(setFragility)
      .catch(() => setFragility(null));
    api<{ lines: { statement: string }[] }>(`/api/contract-flips?plant_id=${bucket.plant_id}&product_id=${bucket.product_id}`)
      .then((payload) => setTermLines(payload.lines))
      .catch(() => setTermLines([]));
  }, [bucket]);

  useEffect(() => {
    if (!bucket) return;
    const next: Record<number, number> = {};
    bucket.allocations.forEach((line) => {
      next[line.demand_id] = line.allocated_quantity;
    });
    setEdits(next);
    const draft: Record<number, { delivered: number; date: string; days: number; penalty: number }> = {};
    bucket.allocations.forEach((line) => {
      draft[line.demand_id] = { delivered: line.allocated_quantity, date: String(line.required_date).slice(0, 10), days: 0, penalty: 0 };
    });
    setActualDraft(draft);
    setReason("Accepted the recommendation.");
    setSaved("");
  }, [bucket]);

  useEffect(() => {
    if (!bucket) return;
    const handle = window.setTimeout(() => {
      api<Preview>("/api/allocate/override", {
        method: "POST",
        body: JSON.stringify({
          plant_id: bucket.plant_id,
          product_id: bucket.product_id,
          allocations: bucket.allocations.map((line) => ({
            demand_id: line.demand_id,
            allocated_quantity: Number(edits[line.demand_id] ?? line.allocated_quantity),
          })),
        }),
      })
        .then(setPreview)
        .catch((err: Error) => setPreview({ feasible: false, message: err.message, changed_from_recommendation: true, delta_versus_recommendation: null }));
    }, 350);
    return () => window.clearTimeout(handle);
  }, [bucket, edits]);

  function applyChoice(plan: "typed" | "proportional") {
    if (!bucket?.comparison) return;
    const rows = plan === "typed" ? bucket.comparison.typed_allocations : bucket.comparison.proportional_allocations;
    const next: Record<number, number> = { ...edits };
    for (const row of rows ?? []) next[row.demand_id] = row.allocated_m3;
    setEdits(next);
    setChosenPlan(plan);
  }

  const mustChoose = Boolean(fragility?.fragile && bucket?.comparison?.plans_differ);

  async function record() {
    if (!bucket) return;
    setError("");
    try {
      const response = await api<{ status: string }>("/api/decisions", {
        method: "POST",
        body: JSON.stringify({
          username: name || "Planner",
          plant_id: bucket.plant_id,
          product_id: bucket.product_id,
          override_reason: reason,
          reason_category: category,
          chosen_plan: chosenPlan,
          terms_confirmed: termsConfirmed,
          replace: Boolean(bucket.open_decision && !bucket.open_decision.locked),
          allocations: bucket.allocations.map((line) => ({
            demand_id: line.demand_id,
            allocated_quantity: Number(edits[line.demand_id] ?? 0),
          })),
        }),
      });
      setSaved(
        response.status === "awaiting_signoff"
          ? "Waiting for the owners of the orders whose allocation changed or is unserved."
          : response.status === "modified"
            ? "Modified allocation recorded."
            : "Recommendation approved and recorded.",
      );
      refreshDecisions();
      setReveal((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the decision.");
    }
  }

  async function saveBuffer(days: number) {
    setBufferDays(days);
    try {
      await api("/api/buffer-days", { method: "POST", body: JSON.stringify({ buffer_days: days }) });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not store the buffer.");
    }
  }

  async function switchMode(plantId: number, mode: "shadow" | "pilot") {
    setError("");
    try {
      await api(`/api/plants/${plantId}/mode`, { method: "POST", body: JSON.stringify({ mode }) });
      setReveal((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the plant mode.");
    }
  }

  async function saveActuals() {
    if (!bucket?.open_decision) return;
    setError("");
    try {
      await api(`/api/decisions/${bucket.open_decision.id}/actual`, {
        method: "POST",
        body: JSON.stringify({
          username: name || "Planner",
          lines: bucket.allocations.map((line) => ({
            demand_id: line.demand_id,
            delivered_m3: Number(actualDraft[line.demand_id]?.delivered ?? 0),
            actual_delivery_date: actualDraft[line.demand_id]?.date || line.required_date,
            programme_days_lost: line.demand_type === "Internal" ? Number(actualDraft[line.demand_id]?.days ?? 0) : 0,
            penalty_paid_rm: line.demand_type === "External" ? Number(actualDraft[line.demand_id]?.penalty ?? 0) : 0,
          })),
        }),
      });
      setSaved("Actuals stored. This decision is locked.");
      setReveal((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not store actuals.");
    }
  }

  async function saveExpedite() {
    if (!bucket) return;
    const screen = bucket.expedite_screen.find((row) => row.customer_or_project === expediteOrder) ?? bucket.expedite_screen[0];
    const line = bucket.allocations.find((row) => row.customer_or_project === screen?.customer_or_project);
    if (!screen || !line) return;
    setError("");
    try {
      const response = await api<{ estimated_avoided_rm: number | null; label: string }>("/api/expedite-decisions", {
        method: "POST",
        body: JSON.stringify({
          username: name || "Planner",
          plant_id: bucket.plant_id,
          product_id: bucket.product_id,
          demand_id: line.demand_id,
          customer_or_project: screen.customer_or_project,
          decision: expediteDraft.decision,
          emergency_rm_per_m3: expediteDraft.rate,
          available_volume_m3: expediteDraft.volume,
          cost_rm: expediteDraft.rate * expediteDraft.volume,
          penalty_would_apply_rm: expediteDraft.would,
          delivered_in_full: expediteDraft.full,
          on_time: expediteDraft.onTime,
          penalty_paid_rm: expediteDraft.paid,
        }),
      });
      const avoided = response.estimated_avoided_rm == null ? "not estimated" : rm(response.estimated_avoided_rm);
      setSaved(`Expedite recorded. ${response.label}: ${avoided}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the expedite.");
    }
  }

  async function saveInformal() {
    if (!shadowForm) return;
    setError("");
    try {
      const response = await api<{ paired_gap_rm: number; informal_expected_rm: number; recommendation_expected_rm: number }>("/api/informal-plans", {
        method: "POST",
        body: JSON.stringify({
          username: name || "Planner",
          plant_id: shadowForm.plantId,
          product_id: shadowForm.productId,
          allocations: shadowForm.rows.map((row) => ({ demand_id: row.id, allocated_quantity: Number(shadowQty[row.id] ?? 0) })),
        }),
      });
      setSaved(`Informal plan stored. Modelled consequence of that plan ${rm(response.informal_expected_rm)} minus the recommendation ${rm(response.recommendation_expected_rm)}. Paired gap ${rm(response.paired_gap_rm)}. Not an observed outcome.`);
      setReveal((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not store the informal plan.");
    }
  }

  if (shadowForm) {
    return (
      <div className="page page-allocation">
        <PageHeader kicker="Shadow" title="Informal plan" lede="Record what you would actually run before the recommendation is shown. Both plans are then scored by the model on this same book." />
        {error ? <ErrorNote message={error} /> : null}
        <Panel title="Quantities you would run" sub="This plant is in shadow. The recommendation stays hidden until this plan is stored.">
          <div className="field"><label>Plant</label>
            <select value={shadowForm.plantId} onChange={(event) => setParams({ plant: event.target.value, product: String(shadowForm.productId) })}>
              {catalog.plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
            </select>
          </div>
          <div className="field"><label>Product</label>
            <select value={shadowForm.productId} onChange={(event) => setParams({ plant: String(shadowForm.plantId), product: event.target.value })}>
              {catalog.products.map((product) => <option key={product.id} value={product.id}>{product.code}</option>)}
            </select>
          </div>
          <div className="field"><label>Buffer days for lump-sum orders</label>
            <input type="number" min={0} max={14} value={bufferDays} onChange={(event) => void saveBuffer(Number(event.target.value))} />
          </div>
          {shadowForm.rows.map((row) => (
            <div className="field" key={row.id}>
              <label>{row.customer_or_project} · requested {m3(row.requested_quantity)}</label>
              <input type="number" min={0} value={shadowQty[row.id] ?? 0} onChange={(event) => setShadowQty({ ...shadowQty, [row.id]: Number(event.target.value) })} />
            </div>
          ))}
          <button className="btn primary" onClick={() => void saveInformal()}>Store informal plan as {name || "planner"}</button>
          <button className="btn" onClick={() => void switchMode(shadowForm.plantId, "pilot")}>Switch this plant to pilot</button>
        </Panel>
      </div>
    );
  }

  if (error && !result) return <ErrorNote message={error} />;
  if (!result || !bucket) return <p>Running the allocation engine…</p>;

  const recorded = decisions.some((row) => row.plant_name === bucket.plant_name && row.product_name === bucket.product_name);
  const policies = bucket.policies.map((policy) => ({ name: policy.policy.replace("Minimise business consequence", "Recommended"), value: policy.expected_consequence_rm, code: policy.policy_code }));

  return (
    <div className="page page-allocation">
      <PageHeader
        kicker={bucket.solver}
        title="Allocation Decision"
        lede={bucket.objective}
      />
      {params.get("decision") ? <p className="note">Opened from the queue for decision {params.get("decision")}.</p> : null}
      {result.economics_incomplete && result.economics_incomplete.n > 0 ? (
        <Panel title="Economics incomplete" sub={result.economics_incomplete.note}>
          <p>Recommendation, including default-margin lines: {rm(result.economics_incomplete.with_expected_consequence_rm)}. Headline book without those lines: {rm(result.economics_incomplete.without_expected_consequence_rm)}.</p>
        </Panel>
      ) : null}
      {bucket.allocations.length === 0 ? <p className="note">No orders are in this plant and product.</p> : null}
      <div className="stepper">
        {["Data", "Recommendation", "Human review", "Approve or modify", "Recorded"].map((step, index) => (
          <span key={step} className={index <= (recorded ? 4 : 3) ? "on" : ""}>{step}</span>
        ))}
      </div>
      <div className="bucket-row">
        {result.buckets.filter((item) => item.total_demand_m3 > 0).map((item) => {
          const id = `${item.plant_id}-${item.product_id}`;
          return (
            <button key={id} className={`btn ${id === key ? "on" : ""}`} onClick={() => {
              const mode = item.plant_mode ?? "shadow";
              if (mode === "shadow") {
                setParams({ plant: String(item.plant_id), product: String(item.product_id) });
                return;
              }
              setKey(id);
            }}>
              {item.plant_name.split(" ")[0]} {item.product_code}{item.constrained ? ` · short ${m3(item.shortfall_m3)}` : " · covered"}
            </button>
          );
        })}
      </div>
      {error ? <ErrorNote message={error} /> : null}
      {saved ? <div className="banner good">{saved}</div> : null}
      {bucket.open_decision ? (
        <div className="banner warn">
          <strong>Open decision: you are replacing {bucket.open_decision.username} at {bucket.open_decision.created_at}.</strong>
          {bucket.open_decision.locked ? <p>Actuals are recorded. This decision is locked.</p> : (
            <ul>
              {bucket.open_decision.lines.map((line) => (
                <li key={line.demand_id}>{line.customer_or_project}: committed {m3(line.committed_m3)}, recommendation was {m3(line.recommended_m3)}, screen shows {m3(edits[line.demand_id] ?? 0)}.</li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
      {bucket.plant_mode === "shadow" ? <p className="note">This plant is in shadow. The informal plan is the executed plan. The paired gap is the modelled consequence of that plan minus the modelled recommendation. It is not an observed outcome.</p> : <p className="note">This plant is in pilot. An approval writes committed production onto the calendar.</p>}
      {pairedNote ? <div className="banner good">{pairedNote}</div> : null}
      <div className="field"><label>Buffer days for lump-sum orders</label>
        <input type="number" min={0} max={14} value={bufferDays} onChange={(event) => void saveBuffer(Number(event.target.value))} />
      </div>
      <button className="btn" onClick={() => void switchMode(bucket.plant_id, bucket.plant_mode === "pilot" ? "shadow" : "pilot")}>
        {bucket.plant_mode === "pilot" ? "Return this plant to shadow" : "Switch this plant to pilot"}
      </button>

      <div className="kpi-grid">
        <Kpi label="Available supply" value={m3(bucket.available_supply_m3)} hint={bucket.stockable === false ? "Ready-mix cannot be stocked. Supply is dated capacity only." : `${m3(bucket.available_capacity_m3)} capacity + ${m3(bucket.usable_inventory_m3)} usable inventory`} />
        <Kpi label="Total demand" value={m3(bucket.total_demand_m3)} hint={`${m3(bucket.internal_demand_m3)} internal · ${m3(bucket.external_demand_m3)} external`} />
        <Kpi label="Shortfall" value={m3(bucket.shortfall_m3)} tone={bucket.shortfall_m3 > 0 ? "risk" : "good"} hint="Unserved after required dates are respected" />
        <Kpi label="Expected consequence" value={rm(bucket.expected_consequence_rm)} tone="risk" hint={`Gross if every open order firms: ${rm(bucket.gross_consequence_rm)}`} />
        <Kpi label="Programme days left open" value={String(bucket.programme_days)} hint="Internal days scaled by the unserved fraction" />
        <Kpi label="Margin left open" value={rm(bucket.margin_at_risk_rm)} />
      </div>

      {bucket.decision_review ? (
        <Panel title="Who reviews this recommendation" sub={bucket.decision_review.level}>
          <p><strong>{bucket.decision_review.owner}</strong></p>
          <p>{bucket.decision_review.evidence}</p>
          {bucket.decision_review.triggers.length || fragility?.fragile ? (
            <ul>
              {bucket.decision_review.triggers.map((item) => <li key={item}>{item}</li>)}
              {fragility?.fragile ? <li>The recommendation is fragile. Keeping it under a 10% or 20% shock costs more than the re-solved optimum by the regret bar.</li> : null}
              {termLines.map((row) => <li key={row.statement}>{row.statement}</li>)}
            </ul>
          ) : null}
          <p className="note">The review lines are modelling assumptions. They are not a head-office policy.</p>
        </Panel>
      ) : null}

      <Panel title="Why this allocation" sub="Confirmed cubic metres use a 100% weight. The unconfirmed remainder is a separate tranche at 75%, or 45% when the line is a forecast. The ranking is expected ringgit per cubic metre. It is not an internal or external rule.">
        <div className="explain">
          {bucket.explanation.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
          <ol className="ranking">
            {bucket.explanation.ranking.map((line) => <li key={line}>{line.replace(/^\d+\.\s*/, "")}</li>)}
          </ol>
          {bucket.explanation.tradeoff ? <p className="tradeoff">{bucket.explanation.tradeoff}</p> : null}
          {bucket.explanation.other_close_calls ? <p>{bucket.explanation.other_close_calls}</p> : null}
          {fragility ? (
            <p className={fragility.fragile ? "tradeoff" : "note"}>{fragility.summary}</p>
          ) : (
            <p className="note">Checking whether a 10% or 20% change in one order’s unit expected consequence changes who is left unserved.</p>
          )}
          {bucket.comparison ? (
            <div>
              <p>{bucket.comparison.note}</p>
              <p>
                Typed plan under true terms {rm(bucket.comparison.typed_plan_true_rm)}.
                Proportional plan under true terms {rm(bucket.comparison.proportional_plan_true_rm ?? 0)}.
                Regret of using the proportional plan {rm(bucket.comparison.proportional_regret_under_true_terms_rm ?? 0)}.
              </p>
              <p className="note">
                Scored as if every term were linear: typed plan {rm(bucket.comparison.typed_plan_if_scored_proportional_rm ?? 0)}, proportional plan {rm(bucket.comparison.proportional_plan_if_scored_proportional_rm ?? 0)}.
                Epsilon for the tie-break is {rm(bucket.objective_epsilon_rm ?? 0)}.
              </p>
              {bucket.headline_gap ? <p>{bucket.headline_gap.note} On this plant and product the true gap is {rm(bucket.headline_gap.true_rm)}, the linear scoring of the same two allocations is {rm(bucket.headline_gap.if_scored_proportional_rm)}, and the effect is {rm(bucket.headline_gap.effect_rm)}.</p> : null}
            </div>
          ) : null}
          {bucket.unverified_minimax ? (
            <p className="tradeoff">
              {bucket.unverified_minimax.note} Unverified orders: {bucket.unverified_minimax.orders.join(", ")}. Maximum regret of the linear reading {rm(bucket.unverified_minimax.max_regret_linear_plan_rm)}. Maximum regret of the lump-sum reading {rm(bucket.unverified_minimax.max_regret_lump_plan_rm)}. Chosen reading: {bucket.unverified_minimax.chosen}.
            </p>
          ) : null}
          {bucket.party_burden ? (
            <p>
              {bucket.party_burden.note} Before, internal unserved {m3(bucket.party_burden.before?.internal.unserved_m3 ?? 0)} ({Math.round((bucket.party_burden.before?.internal.unserved_share ?? 0) * 100)}%) and consequence {rm(bucket.party_burden.before?.internal.consequence_rm ?? 0)}; external unserved {m3(bucket.party_burden.before?.external.unserved_m3 ?? 0)} and consequence {rm(bucket.party_burden.before?.external.consequence_rm ?? 0)}. After, internal unserved {m3(bucket.party_burden.after.internal.unserved_m3)} ({Math.round(bucket.party_burden.after.internal.unserved_share * 100)}%) and consequence {rm(bucket.party_burden.after.internal.consequence_rm)}; external unserved {m3(bucket.party_burden.after.external.unserved_m3)} and consequence {rm(bucket.party_burden.after.external.consequence_rm)}.
            </p>
          ) : null}
          {fragility?.fragile && bucket.comparison?.plans_differ ? (
            <div>
              <p className="tradeoff">This bucket is fragile and the two plans differ. Choose one before recording.</p>
              <label className="note">
                <input type="radio" name="plan" checked={chosenPlan === "typed"} onChange={() => applyChoice("typed")} /> Typed recommendation
              </label>
              <label className="note">
                <input type="radio" name="plan" checked={chosenPlan === "proportional"} onChange={() => applyChoice("proportional")} /> Proportional comparison
              </label>
              <div className="split">
                <ul>
                  {(bucket.comparison.typed_allocations ?? []).map((row) => (
                    <li key={`t-${row.demand_id}`}>{row.customer_or_project}: typed {m3(row.allocated_m3)}</li>
                  ))}
                </ul>
                <ul>
                  {(bucket.comparison.proportional_allocations ?? []).map((row) => (
                    <li key={`p-${row.demand_id}`}>{row.customer_or_project}: proportional {m3(row.allocated_m3)}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}
          <p>{bucket.explanation.policy_comparison}</p>
          {bucket.stranded_note ? <p>{bucket.stranded_note}</p> : null}
          {bucket.explanation.maintenance.map((note) => <p key={note}>{note}</p>)}
          {bucket.explanation.expedite ? <p>{bucket.explanation.expedite}</p> : null}
        </div>
      </Panel>

      <div className="split">
        <Panel title="Recommended and unserved" sub="Consequence avoided is the commercial, contractual, and programme impact of the cubic metres that are served.">
          <div className="rec-grid">
            {bucket.cards.map((card) => (
              <article key={card.name} className={`rec ${card.unserved_m3 > 0 && card.allocated_m3 === 0 ? "missed" : "served"}`}>
                <h3>{card.name}</h3>
                <span className={`badge ${card.demand_type === "Internal" ? "internal" : "external"}`}>{card.demand_type}</span>
                <ul>{card.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul>
              </article>
            ))}
          </div>
        </Panel>
        <Panel title="Same capacity, different rules" sub="Expected consequence. Lower is the smaller business hit.">
          <div className="chart-box">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={policies} layout="vertical" margin={{ left: 8, right: 12 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tickFormatter={(value) => `${Math.round(Number(value) / 1000)}k`} />
                <YAxis type="category" dataKey="name" width={148} />
                <Tooltip formatter={(value) => rm(Number(value))} />
                <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                  {policies.map((policy) => <Cell key={policy.code} fill={policy.code === "optimised" ? "#0e6b57" : "#8aa0b4"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <Panel title="Review and override" sub="Edit allocated cubic metres. The engine checks that the edit can still be produced by the required date.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Order</th>
                <th>Due</th>
                <th className="num">RM / m³</th>
                <th className="num">Requested</th>
                <th className="num">Recommended</th>
                <th className="num">Your allocation</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {bucket.allocations.map((line) => (
                <tr key={line.demand_id} className={line.unserved_quantity > 0 ? "constrained" : ""}>
                  <td>
                    <strong>{line.customer_or_project}</strong>
                    {line.economics_badge ? <div className="note">{line.economics_badge}</div> : null}
                    <div className="note">{line.demand_code} · {line.demand_type} · {line.confidence_level} · {line.project_criticality}{line.owner_name ? ` · owner ${line.owner_name}` : ""}</div>
                  </td>
                  <td className="nowrap">{longDate(line.required_date)}</td>
                  <td className="num">{rm(line.unit_expected_rm)}</td>
                  <td className="num">{m3(line.requested_quantity)}</td>
                  <td className="num">{m3(line.allocated_quantity)}</td>
                  <td className="num">
                    <input
                      className="qty"
                      type="number"
                      min={0}
                      max={line.requested_quantity}
                      step={1}
                      value={edits[line.demand_id] ?? line.allocated_quantity}
                      onChange={(event) => setEdits({ ...edits, [line.demand_id]: Number(event.target.value) })}
                    />
                  </td>
                  <td className="reason">{line.why_not || line.reason}{line.partial_service_no_penalty_avoided ? <div className="note">Partial service, no penalty avoided. The cubic metres served leave the penalty and delay unchanged versus missing the order. They earn margin only.</div> : null}{line.tranche_note ? <div className="note">{line.tranche_note}</div> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {preview ? (
          <div className={`banner ${preview.feasible ? (preview.changed_from_recommendation ? "warn" : "good") : "risk"}`}>
            <strong>{preview.feasible ? (preview.changed_from_recommendation ? "Override changes the consequence" : "Matches the recommendation") : "This edit cannot be produced"}</strong>
            {preview.message}
            {preview.delta_versus_recommendation ? (
              <div>
                Change in expected consequence versus the recommendation: {rm(preview.delta_versus_recommendation.expected_consequence_rm)}.
                Unserved quantity change: {m3(preview.delta_versus_recommendation.unserved_m3)}.
                Programme days change: {preview.delta_versus_recommendation.programme_days}.
              </div>
            ) : null}
          </div>
        ) : null}
        {bucket.stockable !== false && bucket.inventory_projection ? (
          <p className="note">
            Projected stock: opening {m3(bucket.inventory_projection.opening_on_hand_m3)}, drawn {m3(bucket.inventory_projection.drawn_from_inventory_m3)}, produced for this allocation {m3(bucket.inventory_projection.produced_for_allocation_m3)}, closing on-hand {m3(bucket.inventory_projection.projected_closing_on_hand_m3)}. {bucket.inventory_projection.basis}
          </p>
        ) : null}
        {preview?.changed_from_recommendation ? (
          <>
            <div className="field">
              <label>If you change the recommendation, why?</label>
              <select value={category} onChange={(event) => setCategory(event.target.value)}>
                {["Customer commitment", "Project criticality", "Contractual obligation", "Operational constraint", "Management decision", "Data issue", "Other"].map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </div>
            <div className="field grow">
              <label>Reason for the change</label>
              <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
            </div>
          </>
        ) : <p className="note">This matches the recommendation, so the recorded reason is “Accepted as recommended”.</p>}
        {termLines.length > 0 ? (
          <label className="check">
            <input type="checkbox" checked={termsConfirmed} onChange={(event) => setTermsConfirmed(event.target.checked)} />
            Commercial owner confirms the unverified contract term before sign-off.
          </label>
        ) : null}
        <button className="btn primary" disabled={preview?.feasible === false || !name.trim() || (mustChoose && !chosenPlan) || (termLines.length > 0 && !termsConfirmed) || Boolean(bucket.open_decision?.locked)} onClick={() => void record()}>
          Record decision as {name || "planner"}
        </button>
      </Panel>

      {bucket.open_decision?.status === "awaiting_signoff" ? (
        <Panel title="Sign-off" sub="The required names are the owners of orders whose allocation changed or is unserved. The plant supervisor can leave a note and does not veto.">
          <p>Deadline {bucket.open_decision.deadline ? when(bucket.open_decision.deadline) : "—"}. A soft hold is on the proposed capacity until this closes.</p>
          <ul>
            {(bucket.open_decision.required_signatories || []).map((row) => (
              <li key={`${row.role}-${row.username}`}>{row.username} · {row.role}{(row.orders || []).length ? ` · ${(row.orders || []).join(", ")}` : ""}</li>
            ))}
          </ul>
          <div className="field">
            <label>Plan you are signing</label>
            <select value={signChoice} onChange={(event) => setSignChoice(event.target.value as "recommended" | "proposed")}>
              <option value="recommended">Recommendation</option>
              <option value="proposed">Proposed allocation</option>
            </select>
          </div>
          <div className="field grow">
            <label>Why you sign</label>
            <textarea value={signReason} onChange={(event) => setSignReason(event.target.value)} />
          </div>
          <button className="btn" onClick={() => void api(`/api/decisions/${bucket.open_decision?.id}/signoff`, { method: "POST", body: JSON.stringify({ username: name, plan_choice: signChoice, reason: signReason }) }).then(() => setReveal((value) => value + 1)).catch((err: Error) => setError(err.message))}>Sign as {name || "owner"}</button>
          <div className="field">
            <label>Constraint instead of a veto</label>
            <select value={constraintKind} onChange={(event) => setConstraintKind(event.target.value as "cap" | "reserve" | "days")}>
              <option value="cap">Cap m³</option>
              <option value="reserve">Reserve m³</option>
              <option value="days">Days limit</option>
            </select>
            <input type="number" min={1} value={constraintQty} onChange={(event) => setConstraintQty(Number(event.target.value))} />
            <select value={constraintEvidence} onChange={(event) => setConstraintEvidence(event.target.value)}>
              <option>site diary</option>
              <option>crew roster</option>
              <option>access permit</option>
            </select>
            <select value={constraintDemand} onChange={(event) => setConstraintDemand(Number(event.target.value))}>
              <option value={0}>Order</option>
              {bucket.allocations.map((line) => <option key={line.demand_id} value={line.demand_id}>{line.customer_or_project}</option>)}
            </select>
          </div>
          <button className="btn" onClick={() => void api(`/api/decisions/${bucket.open_decision?.id}/constraints`, { method: "POST", body: JSON.stringify({ username: name, kind: constraintKind, quantity: constraintQty, evidence_type: constraintEvidence, demand_id: constraintDemand || null, note: signReason || "Recorded from the evidence named above." }) }).then((row: { label?: string }) => { setSaved(row.label || "Constraint applied."); setReveal((value) => value + 1); }).catch((err: Error) => setError(err.message))}>Apply constraint and re-solve</button>
          {role === "plant_supervisor" ? (
            <div className="field grow">
              <label>Plant supervisor note</label>
              <textarea value={consultNote} onChange={(event) => setConsultNote(event.target.value)} />
              <button className="btn" onClick={() => void api(`/api/decisions/${bucket.open_decision?.id}/consultation`, { method: "POST", body: JSON.stringify({ username: name, note: consultNote }) }).then(() => setSaved("Consultation noted.")).catch((err: Error) => setError(err.message))}>Record consultation</button>
            </div>
          ) : null}
        </Panel>
      ) : null}

      {bucket.open_decision && !bucket.open_decision.locked && bucket.open_decision.status !== "awaiting_signoff" ? (
        <Panel title="Record actuals" sub="Delivered quantity may sit up to 5% above requested. Saving actuals locks this decision.">
          {bucket.allocations.map((line) => (
            <div className="field" key={line.demand_id}>
              <label>{line.customer_or_project} · requested {m3(line.requested_quantity)}</label>
              <input type="number" min={0} value={actualDraft[line.demand_id]?.delivered ?? 0} onChange={(event) => setActualDraft({ ...actualDraft, [line.demand_id]: { ...actualDraft[line.demand_id], delivered: Number(event.target.value), date: actualDraft[line.demand_id]?.date || String(line.required_date).slice(0, 10), days: actualDraft[line.demand_id]?.days ?? 0, penalty: actualDraft[line.demand_id]?.penalty ?? 0 } })} />
              <input type="date" value={actualDraft[line.demand_id]?.date || String(line.required_date).slice(0, 10)} onChange={(event) => setActualDraft({ ...actualDraft, [line.demand_id]: { delivered: actualDraft[line.demand_id]?.delivered ?? 0, date: event.target.value, days: actualDraft[line.demand_id]?.days ?? 0, penalty: actualDraft[line.demand_id]?.penalty ?? 0 } })} />
              {line.demand_type === "Internal" ? <input type="number" min={0} max={365} value={actualDraft[line.demand_id]?.days ?? 0} onChange={(event) => setActualDraft({ ...actualDraft, [line.demand_id]: { delivered: actualDraft[line.demand_id]?.delivered ?? 0, date: actualDraft[line.demand_id]?.date || String(line.required_date).slice(0, 10), days: Number(event.target.value), penalty: 0 } })} /> : null}
              {line.demand_type === "External" ? <input type="number" min={0} value={actualDraft[line.demand_id]?.penalty ?? 0} onChange={(event) => setActualDraft({ ...actualDraft, [line.demand_id]: { delivered: actualDraft[line.demand_id]?.delivered ?? 0, date: actualDraft[line.demand_id]?.date || String(line.required_date).slice(0, 10), days: 0, penalty: Number(event.target.value) } })} /> : null}
            </div>
          ))}
          <button className="btn" onClick={() => void saveActuals()}>Save actuals and lock</button>
        </Panel>
      ) : null}

      {bucket.expedite_screen.length > 0 ? (
        <Panel title="Expedite screen" sub="Cost to close a firing lump. Emergency supply is a proposal, not base capacity.">
          {bucket.expedite_proposal?.recommended ? (
            <p className="tradeoff">
              Recommended expedite: {m3(bucket.expedite_proposal.extra_m3)}, cost {rm(bucket.expedite_proposal.cost_rm)}, avoids {rm(bucket.expedite_proposal.avoids_rm)}. {bucket.expedite_proposal.note}
            </p>
          ) : (
            <p className="note">{bucket.expedite_proposal?.note}</p>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Unserved order</th>
                  <th className="num">Close the step</th>
                  <th className="num">Emergency cost</th>
                  <th className="num">Penalty and delay avoided</th>
                  <th className="num">Avoided per RM</th>
                  <th>Judgement</th>
                </tr>
              </thead>
              <tbody>
                {bucket.expedite_screen.map((row) => (
                  <tr key={row.customer_or_project}>
                    <td>{row.customer_or_project}<div className="note">{row.comparison_basis}</div></td>
                    <td className="num">{m3(row.close_m3 ?? row.unserved_quantity)}</td>
                    <td className="num">{rm(row.expedite_cost_rm)}</td>
                    <td className="num">{rm(row.penalty_and_delay_avoided_rm ?? 0)}</td>
                    <td className="num">{row.avoided_per_rm ?? 0}</td>
                    <td>{row.worth_expediting ? `Worth a human decision. Net benefit ${rm(row.net_benefit_rm)}.` : "The emergency cost is larger than the lump it would turn off."}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="field"><label>Order</label>
            <select value={expediteOrder} onChange={(event) => setExpediteOrder(event.target.value)}>
              <option value="">Select an order</option>
              {bucket.expedite_screen.map((row) => <option key={row.customer_or_project}>{row.customer_or_project}</option>)}
            </select>
          </div>
          <div className="field"><label>Decision</label>
            <select value={expediteDraft.decision} onChange={(event) => setExpediteDraft({ ...expediteDraft, decision: event.target.value })}>
              <option value="approve">Approve</option>
              <option value="decline">Decline</option>
            </select>
          </div>
          <div className="field"><label>Emergency RM per m³</label><input type="number" min={0} value={expediteDraft.rate} onChange={(event) => setExpediteDraft({ ...expediteDraft, rate: Number(event.target.value) })} /></div>
          <div className="field"><label>Available volume m³</label><input type="number" min={0} value={expediteDraft.volume} onChange={(event) => setExpediteDraft({ ...expediteDraft, volume: Number(event.target.value) })} /></div>
          <div className="field"><label>Penalty that would have applied</label><input type="number" min={0} value={expediteDraft.would} onChange={(event) => setExpediteDraft({ ...expediteDraft, would: Number(event.target.value) })} /></div>
          <div className="field"><label>Penalty actually paid</label><input type="number" min={0} value={expediteDraft.paid} onChange={(event) => setExpediteDraft({ ...expediteDraft, paid: Number(event.target.value) })} /></div>
          <label className="check"><input type="checkbox" checked={expediteDraft.full} onChange={(event) => setExpediteDraft({ ...expediteDraft, full: event.target.checked })} /> Delivered in full</label>
          <label className="check"><input type="checkbox" checked={expediteDraft.onTime} onChange={(event) => setExpediteDraft({ ...expediteDraft, onTime: event.target.checked })} /> On time</label>
          <p className="note">Estimated avoided is penalty that would have applied minus penalty paid, and only when the order was approved, delivered in full, and on time.</p>
          <button className="btn" onClick={() => void saveExpedite()}>Record expedite</button>
        </Panel>
      ) : null}

      <Panel title="Audit trail" sub="Recommended allocation, the allocation that was recorded, the reason, the time, and the planner.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Planner</th>
                <th>Scope</th>
                <th>Status</th>
                <th>Reason</th>
                <th className="num">Recommended</th>
                <th className="num">Recorded</th>
              </tr>
            </thead>
            <tbody>
              {decisions.filter((row) => row.plant_name === bucket.plant_name && row.product_name === bucket.product_name).map((row) => (
                <tr key={row.id}>
                  <td className="nowrap">{when(row.created_at)}</td>
                  <td>{row.username}</td>
                  <td>{row.plant_name}<div className="note">{row.product_name}</div></td>
                  <td><span className={`badge ${row.status === "modified" ? "warn" : "ok"}`}>{row.status}</span></td>
                  <td className="reason">{row.override_reason}</td>
                  <td className="num">{rm(row.consequence_recommended)}<div className="note">{m3(row.unserved_recommended)} unserved</div></td>
                  <td className="num">{rm(row.consequence_final)}<div className="note">{m3(row.unserved_final)} unserved</div></td>
                </tr>
              ))}
            </tbody>
          </table>
          {decisions.filter((row) => row.plant_name === bucket.plant_name && row.product_name === bucket.product_name).length === 0 ? <p className="note">Nothing recorded for this plant and product yet.</p> : null}
        </div>
      </Panel>
      {result.model ? (
        <Panel title="How the recommendation is calculated" sub={result.model.solver}>
          <p>{result.model.objective}</p>
          <p className="note">Decision variables</p>
          <ul>{result.model.decision_variables.map((item) => <li key={item}>{item}</li>)}</ul>
          <p className="note">Constraints</p>
          <ul>{result.model.constraints.map((item) => <li key={item}>{item}</li>)}</ul>
          <p className="note">Left out of the objective on purpose</p>
          <ul>{result.model.not_in_the_objective.map((item) => <li key={item}>{item}</li>)}</ul>
          <p className="note">{result.model.forecast_limit}</p>
        </Panel>
      ) : null}
      <Assumptions items={result.assumptions} />
    </div>
  );
}
