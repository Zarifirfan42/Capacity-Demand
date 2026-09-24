import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EmptyNote, ErrorNote, PageHeader, Panel } from "../components";
import { when } from "../format";
import { usePlanner } from "../planner";

type Item = { kind: string; title: string; deadline: string; path: string; detail: string };
type Queue = { role: string; items: Item[]; empty: string; shared_demo_note: string };

export function QueuePage() {
  const { name, role } = usePlanner();
  const [data, setData] = useState<Queue | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function load() {
    setError("");
    api<Queue>(`/api/queue?username=${encodeURIComponent(name)}`)
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    load();
  }, [name, role]);

  async function run(path: string, ok: string) {
    setNotice("");
    setError("");
    try {
      await api(path, { method: "POST", body: JSON.stringify({ username: name || "Demo scheduler" }) });
      setNotice(ok);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The demo action failed.");
    }
  }

  function copy(item: Item) {
    const text = `${item.title}. ${item.detail} Open the decision from My queue.`;
    void navigator.clipboard.writeText(text).then(() => setNotice("Message copied. Nothing was sent.")).catch(() => setNotice(text));
  }

  return (
    <div className="page page-queue">
      <PageHeader
        kicker="Sorted by deadline"
        title="My queue"
        lede="The plant supervisor sees expedite approvals, stock counts, and threshold proposals. The scheduler sees actuals and plans awaiting sign-off. Owners see decisions that name them, and threshold proposals."
      />
      {error ? <ErrorNote message={error} /> : null}
      {notice ? <p className="note">{notice}</p> : null}
      {!data && !error ? <p className="note">Loading the queue…</p> : null}
      {data && data.items.length === 0 ? <EmptyNote message={data.empty || "Nothing is waiting for this seat."} /> : null}
      {data && data.items.length > 0 ? (
        <Panel title="Waiting" sub="Earliest deadline first.">
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>When</th><th>Item</th><th></th></tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={`${item.kind}-${item.title}-${item.deadline}`}>
                    <td>{item.deadline ? when(item.deadline) : "—"}</td>
                    <td>{item.title}<div className="note">{item.detail}</div></td>
                    <td>
                      <Link to={item.path}>Open</Link>
                      <button className="btn" type="button" onClick={() => copy(item)}>Copy message</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
      <Panel title="Simulate expiry (demo)" sub="Demo mode. This moves the deadline and calls the real default function.">
        <p className="note">{data?.shared_demo_note || "Demo rows are shared on this database. Another session sees the same rows. Reset removes only rows marked demo."}</p>
        <div className="btn-row">
          <button className="btn" type="button" onClick={() => void run("/api/demo/start", "Demo decision opened. It names Farah Lim and Nur Aina.")}>Open a demo decision</button>
          <button className="btn primary" type="button" onClick={() => void run("/api/demo/expire", "Deadline moved. The real default function ran.")}>Simulate expiry (demo)</button>
          <button className="btn" type="button" onClick={() => void run("/api/demo/reset", "Demo rows removed. The source book is unchanged.")}>Reset demo rows</button>
        </div>
      </Panel>
    </div>
  );
}
