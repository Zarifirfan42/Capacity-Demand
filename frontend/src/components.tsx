import type { ReactNode } from "react";

export function PageHeader({ kicker, title, lede }: { kicker: string; title: string; lede: string }) {
  return (
    <header className="page-head">
      <p className="kicker">{kicker}</p>
      <h1>{title}</h1>
      <p className="lede">{lede}</p>
    </header>
  );
}

export function Kpi({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "risk" | "good";
}) {
  return (
    <article className={`kpi ${tone}`}>
      <p>{label}</p>
      <strong>{value}</strong>
      {hint ? <span>{hint}</span> : null}
    </article>
  );
}

export function Panel({ title, sub, children, action }: { title: string; sub?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>{title}</h2>
          {sub ? <p className="sub">{sub}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return <p className="error">{message}</p>;
}

export function EmptyNote({ message }: { message: string }) {
  return <p className="note empty-state">{message}</p>;
}

export function Assumptions({ items }: { items: string[] }) {
  return (
    <details className="assumptions">
      <summary>Assumptions used where company data is not connected</summary>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </details>
  );
}
