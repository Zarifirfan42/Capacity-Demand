import { useEffect, useState } from "react";
import { api } from "../api";
import { EmptyNote, ErrorNote, PageHeader, Panel } from "../components";
import { longDate, m3, rm } from "../format";
import type { DemandRow, Meta } from "../types";

type Draft = {
  demand_type: string;
  customer_or_project: string;
  customer_type: string;
  plant_id: number | null;
  product_id: number | null;
  required_date: string | null;
  requested_quantity: number | null;
  confirmed_quantity: number;
  demand_status: string;
  confidence_level: string;
  contribution_margin: number;
  contractual_penalty: number;
  project_criticality: string;
  delay_days_if_unserved: number;
  delay_cost_per_day: number;
  source: string;
  notes: string;
};

type FieldView = { value: string | number | null; span: string; confidence: number; grounded: boolean; warning: string };
type IntakeView = {
  extractor: string;
  badge: string | null;
  intent: string;
  input_hash: string;
  mode: string;
  model: string;
  latency_ms: number;
  fields: Record<string, FieldView>;
  highlights: { field: string; start: number; end: number }[];
  duplicate: { demand_code: string; reason: string } | null;
  match: { demand_id: number; demand_code: string; summary: string; changes: { field: string; before: string | number; after: string | number }[] } | null;
  contacts_kept_local: string[];
  blocked_reasons: string[];
  display_text: string;
};

function localStamp() {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

function SourceText({ text, marks }: { text: string; marks: { start: number; end: number }[] }) {
  const parts: (string | { text: string; key: number })[] = [];
  let cursor = 0;
  marks.forEach((mark) => {
    if (mark.start < cursor || mark.end > text.length) return;
    if (mark.start > cursor) parts.push(text.slice(cursor, mark.start));
    parts.push({ text: text.slice(mark.start, mark.end), key: mark.start });
    cursor = mark.end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return (
    <p className="source-text">
      {parts.map((part, index) => (typeof part === "string" ? <span key={`t-${index}`}>{part}</span> : <mark key={part.key}>{part.text}</mark>))}
    </p>
  );
}

const EMPTY_FILTER = {
  plant_id: "",
  product_id: "",
  demand_type: "",
  demand_status: "",
  confidence_level: "",
  customer: "",
  date_from: "",
  date_to: "",
};

export function DemandPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [filters, setFilters] = useState(EMPTY_FILTER);
  const [rows, setRows] = useState<DemandRow[]>([]);
  const [totals, setTotals] = useState({ internal_m3: 0, external_m3: 0, requested_m3: 0, contribution_margin_rm: 0 });
  const [statuses, setStatuses] = useState<string[]>([]);
  const [selected, setSelected] = useState<DemandRow | null>(null);
  const [text, setText] = useState("");
  const [steps, setSteps] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [intake, setIntake] = useState<IntakeView | null>(null);
  const [proposed, setProposed] = useState<Record<string, string | number | null>>({});
  const [receivedAt, setReceivedAt] = useState(localStamp);
  const [started, setStarted] = useState<number | null>(null);
  const [acceptCeiling, setAcceptCeiling] = useState(false);
  const [acceptDefault, setAcceptDefault] = useState(false);
  const [ackPenalty, setAckPenalty] = useState(false);
  const [ackDelay, setAckDelay] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [ownerName, setOwnerName] = useState("");
  const [ownerRole, setOwnerRole] = useState("");
  const [manual, setManual] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [quality, setQuality] = useState<{ quality_percent: number; quality_meaning: string; failed: number; issues: { code: string; message: string }[]; synthetic_note: string } | null>(null);
  const [planning, setPlanning] = useState<{ by_confidence_m3: Record<string, number>; forecast_method: string; certainty_note?: string } | null>(null);
  const [forecast, setForecast] = useState<{
    label: string;
    method: string;
    not_confirmed: string;
    october_planning_forecast_m3: number;
    evaluation: { points: number; mae_m3: number; rmse_m3: number; bias_m3: number; mape: number | null; mape_note: string } | null;
    lines: { plant_name: string; product_name: string; statistical_forecast_m3: number; planner_adjustment_m3: number; planning_forecast_m3: number }[];
    october_weeks?: { week: string; confirmed_m3: number; forecast_class_in_book_m3: number; planning_demand_m3: number }[];
  } | null>(null);
  const [tab, setTab] = useState<"book" | "planning" | "quality" | "intake">("book");

  function load(next = filters) {
    const params = new URLSearchParams();
    Object.entries(next).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    api<{ rows: DemandRow[]; totals: typeof totals; statuses: string[] }>(`/api/demands?${params.toString()}`)
      .then((payload) => {
        setRows(payload.rows);
        setTotals(payload.totals);
        setStatuses(payload.statuses);
        setSelected(payload.rows[0] ?? null);
      })
      .catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    api<Meta>("/api/meta").then(setMeta).catch((err: Error) => setError(err.message));
    api<NonNullable<typeof quality>>("/api/quality").then(setQuality).catch(() => undefined);
    api<NonNullable<typeof planning>>("/api/planning-view").then(setPlanning).catch(() => undefined);
    api<NonNullable<typeof forecast>>("/api/forecast").then(setForecast).catch(() => undefined);
    load(EMPTY_FILTER);
    // initial load only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function extract(source: "Email intake" | "Simulated OCR" | "WhatsApp", body = text) {
    setError("");
    setMessage("");
    setManual(false);
    try {
      const result = await api<{ ok: boolean; steps: string[]; warnings: string[]; draft: Draft } & IntakeView>("/api/demands/extract", {
        method: "POST",
        body: JSON.stringify({ text: body, source }),
      });
      setSteps(result.steps);
      setWarnings(result.warnings);
      setDraft(result.draft);
      setDrafts((result as { drafts?: Draft[] }).drafts?.length ? (result as { drafts?: Draft[] }).drafts || [] : result.draft ? [result.draft] : []);
      setIntake(result);
      setProposed({
        customer_or_project: result.draft?.customer_or_project ?? null,
        demand_type: result.fields?.demand_type?.value ?? null,
        plant_id: result.draft?.plant_id ?? null,
        product_id: result.draft?.product_id ?? null,
        requested_quantity: result.draft?.requested_quantity ?? null,
        required_date: result.draft?.required_date ?? null,
      });
      setStarted(Date.now());
      setAcceptCeiling(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read the message.");
    }
  }

  function beginManual() {
    setError("");
    setMessage("");
    setManual(true);
    setIntake(null);
    setSteps([]);
    setWarnings(["This timer is a manual entry. Margin, penalty, and delay cost stay blank until you type them."]);
    setDraft({
      demand_type: "External",
      customer_or_project: "",
      customer_type: "Main contractor",
      plant_id: null,
      product_id: null,
      required_date: "",
      requested_quantity: null,
      confirmed_quantity: 0,
      demand_status: "Open",
      confidence_level: "Probable",
      contribution_margin: 0,
      contractual_penalty: 0,
      project_criticality: "n/a",
      delay_days_if_unserved: 0,
      delay_cost_per_day: 0,
      source: "Manual",
      notes: "",
    });
    setStarted(Date.now());
  }

  async function saveDraft(action: "save_new" | "apply_change" | "cancel" | "manual" = manual ? "manual" : "save_new") {
    if (!draft) return;
    if (action !== "cancel" && (draft.plant_id == null || draft.product_id == null || !draft.required_date || !draft.requested_quantity)) {
      setError("Complete plant, product, date, and quantity before adding the line.");
      return;
    }
    setError("");
    const proposal = manual ? {} : proposed;
    try {
      const saved = await api<{ demand_code: string }>("/api/intake/confirm", {
        method: "POST",
        body: JSON.stringify({
          action,
          source: draft.source,
          input_hash: intake?.input_hash || "",
          mode: manual ? "manual" : intake?.mode || "manual",
          extractor: manual ? "manual" : intake?.extractor || "manual",
          model: intake?.model || "",
          latency_ms: intake?.latency_ms || 0,
          intent: intake?.intent || "new",
          proposed: proposal,
          draft,
          matched_demand_id: intake?.match?.demand_id ?? null,
          confirm_seconds: started ? Math.round((Date.now() - started) / 1000) : 0,
          received_at: receivedAt ? new Date(receivedAt).toISOString() : null,
          contacts_kept_local: intake?.contacts_kept_local || [],
          accept_ceiling: acceptCeiling,
          accept_default_margin: acceptDefault,
          acknowledge_zero_penalty: ackPenalty,
          acknowledge_zero_delay: ackDelay,
          owner_name: ownerName,
          owner_role: ownerRole,
        }),
      });
      setMessage(`${saved.demand_code} is updated in the demand book. Run the allocation again to see whether it changes the recommendation.`);
      setDraft(null);
      setIntake(null);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not confirm the line.");
    }
  }

  async function remove(row: DemandRow) {
    setError("");
    try {
      await api(`/api/demands/${row.id}`, { method: "DELETE" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the line.");
    }
  }

  return (
    <div className="page">
      <PageHeader
        kicker="One book"
        title="Demand Hub"
        lede="Internal projects and external customers sit in the same table. Extraction prepares a line. It does not decide who gets capacity."
      />
      <div className="btn-row">
        {(["book", "planning", "quality", "intake"] as const).map((name) => (
          <button key={name} className={tab === name ? "btn primary" : "btn"} onClick={() => setTab(name)}>
            {name === "book" ? "Demand book" : name === "planning" ? "Demand planning" : name === "quality" ? "Data quality" : "Intake"}
          </button>
        ))}
      </div>
      {tab === "quality" && quality ? (
        <Panel title="Data quality before allocation" sub={`${quality.quality_percent}% of checks passed. ${quality.failed} failed. ${quality.quality_meaning}`}>
          <p className="note">{quality.synthetic_note}</p>
          {quality.issues.length === 0 ? <p>No failed checks on the current book.</p> : (
            <ul>{quality.issues.slice(0, 8).map((issue) => <li key={issue.message}>{issue.message}</li>)}</ul>
          )}
        </Panel>
      ) : null}
      {tab === "planning" ? (
        <Panel title="Demand planning" sub="Synthetic history. The forecast is not a confirmed order.">
          {planning ? <p className="note">October book: confirmed {m3(planning.by_confidence_m3.Confirmed ?? 0)}, probable {m3(planning.by_confidence_m3.Probable ?? 0)}, forecast-class lines already in the book {m3(planning.by_confidence_m3.Forecast ?? 0)}. {planning.certainty_note} {planning.forecast_method}</p> : null}
          {forecast ? (
            <>
              <p>{forecast.label}</p>
              <p className="note">{forecast.method}</p>
              <p className="note">{forecast.not_confirmed}</p>
              {forecast.evaluation ? (
                <p>Backtest on {forecast.evaluation.points} plant-product months. MAE {m3(forecast.evaluation.mae_m3)}. RMSE {m3(forecast.evaluation.rmse_m3)}. Bias {m3(forecast.evaluation.bias_m3)}. {forecast.evaluation.mape == null ? "MAPE withheld." : `MAPE ${(forecast.evaluation.mape * 100).toFixed(1)}%.`} {forecast.evaluation.mape_note}</p>
              ) : null}
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Plant</th><th>Product</th><th className="num">Statistical forecast</th><th className="num">Planner adjustment</th><th className="num">Planning forecast</th></tr>
                  </thead>
                  <tbody>
                    {forecast.lines.map((line) => (
                      <tr key={`${line.plant_name}-${line.product_name}`}>
                        <td>{line.plant_name}</td>
                        <td>{line.product_name}</td>
                        <td className="num">{m3(line.statistical_forecast_m3)}</td>
                        <td className="num">{m3(line.planner_adjustment_m3)}</td>
                        <td className="num">{m3(line.planning_forecast_m3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="note">October planning forecast across plants and products: {m3(forecast.october_planning_forecast_m3)}. A person can add a forecast-class line to the book. The allocator does not import this table by itself.</p>
              {forecast.october_weeks ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Week</th><th className="num">Confirmed</th><th className="num">Forecast-class in the book</th><th className="num">Planning demand</th></tr>
                    </thead>
                    <tbody>
                      {forecast.october_weeks.map((week) => (
                        <tr key={week.week}>
                          <td>{week.week}</td>
                          <td className="num">{m3(week.confirmed_m3)}</td>
                          <td className="num">{m3(week.forecast_class_in_book_m3)}</td>
                          <td className="num">{m3(week.planning_demand_m3)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              <p className="note">Planning demand here is the October order book. The statistical forecast above is not added into it.</p>
            </>
          ) : <p>Forecast is loading.</p>}
        </Panel>
      ) : null}
      {error ? <ErrorNote message={error} /> : null}
      {tab === "book" && rows.length === 0 && !error ? <EmptyNote message="No orders are in the book. Add a line from intake, or load the synthetic book." /> : null}
      {message ? <div className="banner good">{message}</div> : null}
      {tab === "book" ? <>
      <div className="kpi-grid">
        <article className="kpi"><p>Showing</p><strong>{m3(totals.requested_m3)}</strong><span>{rows.length} lines</span></article>
        <article className="kpi"><p>Internal</p><strong>{m3(totals.internal_m3)}</strong></article>
        <article className="kpi"><p>External</p><strong>{m3(totals.external_m3)}</strong><span>Margin in view {rm(totals.contribution_margin_rm)}</span></article>
      </div>

      <Panel title="Filters">
        <div className="filters">
          <div className="field">
            <label>Plant</label>
            <select value={filters.plant_id} onChange={(event) => setFilters({ ...filters, plant_id: event.target.value })}>
              <option value="">All</option>
              {meta?.plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Product</label>
            <select value={filters.product_id} onChange={(event) => setFilters({ ...filters, product_id: event.target.value })}>
              <option value="">All</option>
              {meta?.products.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Type</label>
            <select value={filters.demand_type} onChange={(event) => setFilters({ ...filters, demand_type: event.target.value })}>
              <option value="">All</option>
              <option>Internal</option>
              <option>External</option>
            </select>
          </div>
          <div className="field">
            <label>Status</label>
            <select value={filters.demand_status} onChange={(event) => setFilters({ ...filters, demand_status: event.target.value })}>
              <option value="">All</option>
              {statuses.map((status) => <option key={status}>{status}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Confidence</label>
            <select value={filters.confidence_level} onChange={(event) => setFilters({ ...filters, confidence_level: event.target.value })}>
              <option value="">All</option>
              <option>Confirmed</option>
              <option>Probable</option>
              <option>Forecast</option>
            </select>
          </div>
          <div className="field">
            <label>From</label>
            <input type="date" value={filters.date_from} onChange={(event) => setFilters({ ...filters, date_from: event.target.value })} />
          </div>
          <div className="field">
            <label>To</label>
            <input type="date" value={filters.date_to} onChange={(event) => setFilters({ ...filters, date_to: event.target.value })} />
          </div>
          <div className="field grow">
            <label>Customer or project</label>
            <input value={filters.customer} onChange={(event) => setFilters({ ...filters, customer: event.target.value })} placeholder="Search name" />
          </div>
          <div className="field">
            <label>&nbsp;</label>
            <button className="btn primary" onClick={() => load()}>Apply</button>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Type</th>
                <th>Customer / project</th>
                <th>Customer type</th>
                <th>Plant</th>
                <th>Product</th>
                <th>Required</th>
                <th className="num">Requested</th>
                <th className="num">Confirmed</th>
                <th>Status</th>
                <th>Confidence</th>
                <th className="num">Margin</th>
                <th className="num">Penalty</th>
                <th>Criticality</th>
                <th className="num">Delay days</th>
                <th className="num">RM / day</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={`click ${selected?.id === row.id ? "selected" : ""}`} onClick={() => setSelected(row)}>
                  <td className="nowrap">{row.demand_code}</td>
                  <td><span className={`badge ${row.demand_type === "Internal" ? "internal" : "external"}`}>{row.demand_type}</span></td>
                  <td>{row.customer_or_project}{row.economics_basis === "default_assumption" ? <div className="note">Economics incomplete</div> : null}</td>
                  <td>{row.customer_type}</td>
                  <td className="nowrap">{row.plant_name}</td>
                  <td className="nowrap">{row.product_name}</td>
                  <td className="nowrap">{longDate(row.required_date)}</td>
                  <td className="num">{m3(row.requested_quantity)}</td>
                  <td className="num">{m3(row.confirmed_quantity)}</td>
                  <td>{row.demand_status}</td>
                  <td>{row.confidence_level}</td>
                  <td className="num">{rm(row.contribution_margin)}</td>
                  <td className="num">{rm(row.contractual_penalty)}</td>
                  <td>{row.project_criticality}</td>
                  <td className="num">{row.delay_days_if_unserved}</td>
                  <td className="num">{rm(row.delay_cost_per_day)}</td>
                  <td>{row.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {selected ? (
        <Panel title={selected.customer_or_project} sub={`${selected.demand_code} · ${selected.notes}`}>
          <div className="detail">
            <p>Commercial consequence if fully missed: {rm(selected.contribution_margin)} contribution margin.</p>
            <p>Customer consequence: {rm(selected.contractual_penalty)} contractual penalty, type {selected.penalty_type || "per_m3"}{selected.types_unverified ? " (unverified)" : ""}, trigger {selected.lump_sum_trigger || "any"}.</p>
            <p>Project consequence: {selected.delay_days_if_unserved} days at {rm(selected.delay_cost_per_day)} per day, delay type {selected.delay_type || "proportional"}{selected.delay_type_unverified || selected.types_unverified ? " (unverified)" : ""}. The penalty sits on the confirmed tranche. Programme delay uses the same tranche weights as margin: confirmed cubic metres at 100%, remainder at its confidence. Minimum useful delivery {m3(selected.minimum_useful_delivery_m3 || 0)}.</p>
            <p>Operational constraint: {selected.product_name} at {selected.plant_name}, on or before {longDate(selected.required_date)}. It cannot use another product or plant.</p>
            <p>The solver treats {m3(selected.confirmed_quantity)} as confirmed, at a 100% planning-certainty weight. The remaining {m3(Math.max(0, selected.requested_quantity - selected.confirmed_quantity))} is a separate tranche at 75%, or 45% if this line is a forecast.</p>
            {selected.demand_code.includes("-IN-") ? (
              <button className="btn" onClick={() => remove(selected)}>Remove this intake line</button>
            ) : null}
          </div>
        </Panel>
      ) : null}
      </> : null}

      {tab === "intake" ? <Panel title="Prepare a demand line from text" sub="A person confirms every line. Margin, penalty, and delay cost are typed, not read from the message. Relative dates use 24 Sep 2026.">
        <div className="btn-row" style={{ marginBottom: 10 }}>
          <button className="btn" onClick={() => { const sample = meta?.samples.ocr ?? ""; setText(sample); void extract("Simulated OCR", sample); }}>Simulate OCR</button>
          <button className="btn" onClick={() => { const sample = meta?.samples.email ?? ""; setText(sample); void extract("Email intake", sample); }}>Load sample email</button>
          <button className="btn" onClick={() => { const sample = "Hi this is Farah from Gamuda 012-3456789 farah@gamuda.example please book 30 m3 G50 at Pasir Gudang on 11 Oct 2026 confirmed"; setText(sample); void extract("WhatsApp", sample); }}>Load WhatsApp</button>
          <button className="btn primary" onClick={() => void extract("Email intake")}>Extract demand</button>
          <button className="btn" onClick={beginManual}>Time a manual entry</button>
        </div>
        <div className="intake">
          <div>
            <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Paste a PO, an email, a WhatsApp note, or an OCR transcript." />
            {intake ? <SourceText text={intake.display_text || text} marks={intake.highlights || []} /> : null}
          </div>
          <div>
            {steps.map((step) => <p key={step} className="note">{step}</p>)}
            {warnings.map((warning) => <p key={warning} className="error">{warning}</p>)}
            {intake?.duplicate ? <p className="error">{intake.duplicate.reason}</p> : null}
            {intake?.contacts_kept_local?.length ? <p className="note">Kept on this machine, not sent for reading: {intake.contacts_kept_local.join(", ")}</p> : null}
            {intake?.match ? (
              <div className="note">
                <p>{intake.match.summary}. Nothing is saved until you confirm.</p>
                <ul>
                  {intake.match.changes.map((change) => <li key={change.field}>{change.field}: {String(change.before)} → {String(change.after)}</li>)}
                </ul>
                <button className="btn primary" onClick={() => void saveDraft(intake.intent === "cancel" ? "cancel" : "apply_change")}>Confirm {intake.match.summary}</button>
              </div>
            ) : null}
            {draft ? (
              <div className="mini">
                <div className="field"><label>Name</label><input value={draft.customer_or_project} onChange={(event) => setDraft({ ...draft, customer_or_project: event.target.value })} />{intake?.fields.customer_or_project?.span ? <span className="note">Source: {intake.fields.customer_or_project.span}</span> : null}</div>
                <div className="field"><label>Type</label>
                  <select value={draft.demand_type} onChange={(event) => setDraft({ ...draft, demand_type: event.target.value })}>
                    <option>Internal</option>
                    <option>External</option>
                  </select>
                </div>
                <div className="field"><label>Plant</label>
                  <select value={draft.plant_id ?? ""} onChange={(event) => setDraft({ ...draft, plant_id: event.target.value ? Number(event.target.value) : null })}>
                    <option value="">Choose</option>
                    {(meta?.plants || []).map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
                  </select>
                  {intake?.fields.plant_name?.span ? <span className="note">Source: {intake.fields.plant_name.span}</span> : null}
                </div>
                <div className="field"><label>Product</label>
                  <select value={draft.product_id ?? ""} onChange={(event) => setDraft({ ...draft, product_id: event.target.value ? Number(event.target.value) : null })}>
                    <option value="">Choose</option>
                    {(meta?.products || []).map((product) => <option key={product.id} value={product.id}>{product.code}</option>)}
                  </select>
                  {intake?.fields.product_code?.span ? <span className="note">Source: {intake.fields.product_code.span}</span> : null}
                </div>
                <div className="field"><label>Quantity m³</label><input type="number" value={draft.requested_quantity ?? ""} onChange={(event) => setDraft({ ...draft, requested_quantity: Number(event.target.value) })} />{intake?.fields.quantity_m3?.span ? <span className="note">Source: {intake.fields.quantity_m3.span}</span> : null}</div>
                <div className="field"><label>Required date</label><input type="date" value={draft.required_date ?? ""} onChange={(event) => setDraft({ ...draft, required_date: event.target.value })} />{intake?.fields.required_date?.span ? <span className="note">Source: {intake.fields.required_date.span}</span> : null}</div>
                <div className="field"><label>Received</label><input type="datetime-local" value={receivedAt} onChange={(event) => setReceivedAt(event.target.value)} /></div>
                <div className="field"><label>Owner</label><input value={ownerName} onChange={(event) => setOwnerName(event.target.value)} placeholder="Typed, not extracted" /></div>
                <div className="field"><label>Owner role</label><input value={ownerRole} onChange={(event) => setOwnerRole(event.target.value)} /></div>
                <div className="field"><label>Margin RM, typed</label><input type="number" value={draft.contribution_margin} onChange={(event) => setDraft({ ...draft, contribution_margin: Number(event.target.value) })} /></div>
                <div className="field"><label>Penalty RM, typed</label><input type="number" value={draft.contractual_penalty} onChange={(event) => setDraft({ ...draft, contractual_penalty: Number(event.target.value) })} /></div>
                <div className="field"><label>Delay days, typed</label><input type="number" value={draft.delay_days_if_unserved} onChange={(event) => setDraft({ ...draft, delay_days_if_unserved: Number(event.target.value) })} /></div>
                <div className="field"><label>Delay RM per day, typed</label><input type="number" value={draft.delay_cost_per_day} onChange={(event) => setDraft({ ...draft, delay_cost_per_day: Number(event.target.value) })} /></div>
                {drafts.length > 1 ? (
                  <div className="btn-row">
                    {drafts.map((item, index) => (
                      <button key={index} className="btn" type="button" onClick={() => setDraft(item)}>Order {index + 1}: {item.requested_quantity ?? "—"} m³ {item.customer_or_project}</button>
                    ))}
                  </div>
                ) : null}
                {warnings.some((warning) => warning.includes("2,000") || warning.includes("2000")) ? (
                  <label className="note"><input type="checkbox" checked={acceptCeiling} onChange={(event) => setAcceptCeiling(event.target.checked)} /> Accept a quantity above 2,000 m³</label>
                ) : null}
                <label className="note"><input type="checkbox" checked={acceptDefault} onChange={(event) => setAcceptDefault(event.target.checked)} /> Use the default margin for this product ({(meta?.default_margins || []).find((row) => row.product_id === draft.product_id)?.per_m3 ?? "—"} RM per m³, median of seeded lines). The line stays in the recommendation and is badged Economics incomplete.</label>
                <label className="note"><input type="checkbox" checked={ackPenalty} onChange={(event) => setAckPenalty(event.target.checked)} /> There is no contractual penalty.</label>
                <label className="note"><input type="checkbox" checked={ackDelay} onChange={(event) => setAckDelay(event.target.checked)} /> There is no delay cost.</label>
                {intake?.intent === "cancel" ? null : <button className="btn primary" onClick={() => void saveDraft(manual ? "manual" : "save_new")}>{manual ? "Save manual line" : "Add to demand book"}</button>}
              </div>
            ) : <p className="note">Extract a document, or time a manual entry. The scheduler seat confirms it.</p>}
          </div>
        </div>
        <div className="table-wrap" style={{ marginTop: 16 }}>
          <table>
            <thead><tr><th>Use</th><th>Where</th></tr></thead>
            <tbody>
              <tr><td>Read a message into a demand line, then a person confirms it</td><td>Use now, with a person confirming. The model has not been compared with the rules.</td></tr>
              <tr><td>Forecasting with a learned model</td><td>Premature</td></tr>
              <tr><td>Letting a model choose the allocation</td><td>Never</td></tr>
              <tr><td>Anomaly flags on the book</td><td>Later</td></tr>
              <tr><td>Reading a contract clause for penalty type</td><td>Pilot, after a legal review. Not in this build.</td></tr>
              <tr><td>Reading a photo of a paper order</td><td>Behind a flag. Vision was not run. Scores on the 14-message file are a regression check, not accuracy evidence.</td></tr>
            </tbody>
          </table>
        </div>
      </Panel> : null}
    </div>
  );
}
