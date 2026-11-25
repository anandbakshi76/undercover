import { useState } from "react";
import { api } from "./api";
import { Badge } from "./Badge";
import { RunPanel } from "./RunPanel";
import {
  DECISION_LABELS,
  money,
  STATUS_LABELS,
  STATUSES,
  words,
} from "./format";
import { ConfirmDialog } from "./ConfirmDialog";
import { useAction, useLoad } from "./hooks";
import { PageHeader } from "./PageHeader";
import { ErrorState, Loading } from "./States";

export const DEFAULT_CONCURRENCY = 3;
export const MAX_CONCURRENCY = 25;
const QUEUE_PURPOSE =
  "Claims waiting for a decision, and the dispatch that works them.";

async function loadQueue() {
  const [claims, run] = await Promise.all([api.claims(), api.latestDispatch()]);
  return { claims, run };
}

export function Queue() {
  const [concurrency, setConcurrency] = useState(DEFAULT_CONCURRENCY);
  const { data, error, refresh } = useLoad(loadQueue, (loaded) =>
    Boolean(
      loaded?.run?.status === "running" ||
      loaded?.claims.some((claim) => claim.status === "in_progress"),
    ),
  );
  const action = useAction();
  const [confirming, setConfirming] = useState(false);

  if (error) {
    return (
      <>
        <PageHeader title="Claims queue" purpose={QUEUE_PURPOSE} />
        <ErrorState
          message={error}
          next="Check that the backend is running, then reload the page."
        />
      </>
    );
  }
  if (!data) {
    return (
      <>
        <PageHeader title="Claims queue" purpose={QUEUE_PURPOSE} />
        <Loading what="the queue" />
      </>
    );
  }

  const { claims, run } = data;
  const running = run?.status === "running";
  const startDispatch = () =>
    action.act(async () => {
      await api.startDispatch(concurrency);
      setConfirming(false);
      refresh();
    });

  return (
    <>
      <PageHeader title="Claims queue" purpose={QUEUE_PURPOSE}>
        <form
          className="run-form"
          onSubmit={(event) => {
            event.preventDefault();
            setConfirming(true);
          }}
        >
          <label htmlFor="concurrency">Claim agents at once</label>
          <input
            id="concurrency"
            type="number"
            min={1}
            max={MAX_CONCURRENCY}
            value={concurrency}
            onChange={(event) => {
              setConcurrency(Number(event.target.value));
            }}
          />
          <button
            type="submit"
            disabled={
              concurrency < 1 ||
              concurrency > MAX_CONCURRENCY ||
              running ||
              action.busy
            }
          >
            Start a dispatch
          </button>
        </form>
      </PageHeader>
      {action.error && <p role="alert">{action.error}</p>}
      {confirming && (
        <ConfirmDialog
          title="Start a dispatch?"
          confirmLabel="Start the dispatch"
          busy={action.busy}
          onConfirm={() => void startDispatch()}
          onCancel={() => {
            setConfirming(false);
          }}
        >
          <p>
            The dispatcher works the waiting claims with {concurrency} claim
            agents at once, on the current version of the configuration.
          </p>
        </ConfirmDialog>
      )}
      <ul className="counts" aria-label="Claims by state">
        {STATUSES.map((status) => (
          <li key={status} className="panel count">
            <span className={`dot dot-${status}`} />
            <span className="count-label">{STATUS_LABELS[status]}</span>
            <span className="count-number">
              {claims.filter((claim) => claim.status === status).length}
            </span>
          </li>
        ))}
      </ul>
      {run && <RunPanel run={run} />}
      <div className="panel table-panel">
        <table>
          <thead>
            <tr>
              <th scope="col">Claim</th>
              <th scope="col" className="wide">
                Type
              </th>
              <th scope="col" className="wide">
                Summary
              </th>
              <th scope="col" className="number">
                Claimed
              </th>
              <th scope="col" className="wide">
                Submitted
              </th>
              <th scope="col">State</th>
              <th scope="col">Decision</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.claim_id}>
                <td>
                  <a className="mono" href={`#/claims/${claim.claim_id}`}>
                    {claim.claim_id}
                  </a>
                </td>
                <td className="wide">{words(claim.claim_type)}</td>
                <td className="wide">{claim.summary}</td>
                <td className="number mono">{money(claim.amount_claimed)}</td>
                <td className="wide mono">{claim.submitted_on}</td>
                <td>
                  <Badge status={claim.status} />
                </td>
                <td>
                  {claim.decision && DECISION_LABELS[claim.decision]}
                  {claim.decision === "approve" && (
                    <span className="mono"> {money(claim.payout ?? 0)}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
