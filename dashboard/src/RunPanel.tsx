import type { Run, RunSummary } from "./api";
import { Badge } from "./Badge";
import {
  clock,
  cost,
  DECISION_LABELS,
  duration,
  money,
  RUN_LABELS,
} from "./format";

function Summary({ summary }: Readonly<{ summary: RunSummary }>) {
  return (
    <div className="summary">
      <h3>Summary</h3>
      <p>
        {summary.approved} approved, {summary.declined} declined and{" "}
        {summary.referred} referred.
      </p>
      <ul className="summary-list">
        {summary.awaiting_sign_off.map((held) => (
          <li key={held.claim_id}>
            <a className="mono" href={`#/claims/${held.claim_id}`}>
              {held.claim_id}
            </a>{" "}
            is waiting for a sign-off on {money(held.payout)}.
          </li>
        ))}
        {summary.needs_handler.map((id) => (
          <li key={id}>
            <a className="mono" href={`#/claims/${id}`}>
              {id}
            </a>{" "}
            is left for a handler.
          </li>
        ))}
        {summary.in_progress.map((id) => (
          <li key={id}>
            <a className="mono" href={`#/claims/${id}`}>
              {id}
            </a>{" "}
            is still in progress.
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RunPanel({ run }: Readonly<{ run: Run }>) {
  return (
    <section className="panel run" aria-label="Current run">
      <div className="run-head">
        <h2>Current dispatch</h2>
        <span className={`badge run-${run.status}`}>
          {RUN_LABELS[run.status]}
        </span>
      </div>
      <p className="run-facts note">
        <span>
          <span className="mono">{run.concurrency}</span> claim agents at once
        </span>
        <span>
          Model <span className="mono">{run.model}</span>, reasoning{" "}
          <span className="mono">{run.reasoning_effort}</span>
        </span>
        <span>
          Model cost <span className="mono">{cost(run.cost)}</span>
        </span>
        <span>
          Time <span className="mono">{duration(run.elapsed_seconds)}</span>
        </span>
      </p>
      {run.claims.length === 0 ? (
        <p className="note">
          {run.status === "running"
            ? "The dispatcher is choosing claims."
            : "The dispatch had no claims to work."}
        </p>
      ) : (
        <div className="table-panel run-table">
          <table>
            <thead>
              <tr>
                <th scope="col">Claim</th>
                <th scope="col">State</th>
                <th scope="col">Decision</th>
                <th scope="col" className="wide">
                  Started
                </th>
                <th scope="col" className="number">
                  Time
                </th>
                <th scope="col" className="number">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {run.claims.map((item) => (
                <tr key={item.claim_id}>
                  <td>
                    <a className="mono" href={`#/claims/${item.claim_id}`}>
                      {item.claim_id}
                    </a>
                  </td>
                  <td>
                    <Badge status={item.status} />
                  </td>
                  <td>
                    {item.decision && DECISION_LABELS[item.decision]}
                    {item.decision === "approve" && (
                      <span className="mono"> {money(item.payout ?? 0)}</span>
                    )}
                  </td>
                  <td className="wide mono">
                    {item.started_at ? clock(item.started_at) : ""}
                  </td>
                  <td className="number mono">{duration(item.seconds)}</td>
                  <td className="number mono">{cost(item.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {run.summary && <Summary summary={run.summary} />}
    </section>
  );
}
