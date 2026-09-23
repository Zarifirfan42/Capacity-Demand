import { useEffect, useState } from "react";
import { api } from "../api";
import { ErrorNote, PageHeader, Panel } from "../components";
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
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

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
    load(EMPTY_FILTER);
    // initial load only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function extract(source: "Email intake" | "Simulated OCR", body = text) {
    setError("");
    setMessage("");
    const result = await api<{ ok: boolean; steps: string[]; warnings: string[]; draft: Draft }>("/api/demands/extract", {
      method: "POST",
      body: JSON.stringify({ text: body, source }),
    });
    setSteps(result.steps);
    setWarnings(result.warnings);
    setDraft(result.draft);
  }

  async function saveDraft() {
    if (!draft || draft.plant_id == null || draft.product_id == null || !draft.required_date || !draft.requested_quantity) {
      setError("Complete plant, product, date, and quantity before adding the line.");
      return;
    }
    setError("");
    await api("/api/demands", { method: "POST", body: JSON.stringify(draft) });
    setMessage(`${draft.customer_or_project} is in the demand book. Run the allocation again to see whether it changes the recommendation.`);
    setDraft(null);
    load();
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
      {error ? <ErrorNote message={error} /> : null}
      {message ? <div className="banner good">{message}</div> : null}
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
                  <td>{row.customer_or_project}</td>
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
            <p>Customer consequence: {rm(selected.contractual_penalty)} contractual penalty.</p>
            <p>Project consequence: {selected.delay_days_if_unserved} days at {rm(selected.delay_cost_per_day)} per day.</p>
            <p>Operational constraint: {selected.product_name} at {selected.plant_name}, on or before {longDate(selected.required_date)}. It cannot use another product or plant.</p>
            {selected.demand_code.includes("-IN-") ? (
              <button className="btn" onClick={() => remove(selected)}>Remove this intake line</button>
            ) : null}
          </div>
        </Panel>
      ) : null}

      <Panel title="Prepare a demand line from text" sub="Rules stand in for OCR and document extraction. Confirm the draft before it joins the book.">
        <div className="btn-row" style={{ marginBottom: 10 }}>
          <button className="btn" onClick={() => { const sample = meta?.samples.ocr ?? ""; setText(sample); void extract("Simulated OCR", sample); }}>Simulate OCR</button>
          <button className="btn" onClick={() => { const sample = meta?.samples.email ?? ""; setText(sample); void extract("Email intake", sample); }}>Load sample email</button>
          <button className="btn primary" onClick={() => void extract("Email intake")}>Extract demand</button>
        </div>
        <div className="intake">
          <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Paste a PO, an email, or an OCR transcript." />
          <div>
            {steps.map((step) => <p key={step} className="note">{step}</p>)}
            {warnings.map((warning) => <p key={warning} className="error">{warning}</p>)}
            {draft ? (
              <div className="mini">
                <div className="field"><label>Name</label><input value={draft.customer_or_project} onChange={(event) => setDraft({ ...draft, customer_or_project: event.target.value })} /></div>
                <div className="field"><label>Type</label>
                  <select value={draft.demand_type} onChange={(event) => setDraft({ ...draft, demand_type: event.target.value })}>
                    <option>Internal</option>
                    <option>External</option>
                  </select>
                </div>
                <div className="field"><label>Quantity m³</label><input type="number" value={draft.requested_quantity ?? ""} onChange={(event) => setDraft({ ...draft, requested_quantity: Number(event.target.value) })} /></div>
                <div className="field"><label>Required date</label><input type="date" value={draft.required_date ?? ""} onChange={(event) => setDraft({ ...draft, required_date: event.target.value })} /></div>
                <div className="field"><label>Margin RM</label><input type="number" value={draft.contribution_margin} onChange={(event) => setDraft({ ...draft, contribution_margin: Number(event.target.value) })} /></div>
                <div className="field"><label>Penalty RM</label><input type="number" value={draft.contractual_penalty} onChange={(event) => setDraft({ ...draft, contractual_penalty: Number(event.target.value) })} /></div>
                <button className="btn primary" onClick={() => void saveDraft()}>Add to demand book</button>
              </div>
            ) : <p className="note">Extract a document to review the draft.</p>}
          </div>
        </div>
      </Panel>
    </div>
  );
}
