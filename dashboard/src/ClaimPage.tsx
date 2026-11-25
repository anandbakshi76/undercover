import { useCallback } from "react";
import {
  api,
  type ClaimDetail,
  type Handler,
  type Policy,
  type Step,
} from "./api";
import { useAuth } from "./authContext";
import { Badge } from "./Badge";
import { DECISION_LABELS, dateTime, money, words } from "./format";
import { useAction, useLoad } from "./hooks";

const STEP_LABELS: Record<Step["kind"], string> = {
  tool_call: "Called",
  tool_result: "Result",
  section: "Read",
  reply: "Reply",
  error: "Error",
};

function ClaimFacts({ claim }: Readonly<{ claim: ClaimDetail }>) {
  return (
    <section className="panel">
      <h2>The claim</h2>
      <dl className="facts">
        <dt>Type</dt>
        <dd>{words(claim.claim_type)}</dd>
        <dt>Incident</dt>
        <dd>
          <span className="mono">{claim.incident_date}</span>,{" "}
          {claim.incident_location}
        </dd>
        <dt>Submitted</dt>
        <dd className="mono">{claim.submitted_on}</dd>
        <dt>Claimed</dt>
        <dd className="mono">{money(claim.amount_claimed)}</dd>
      </dl>
      <p className="story">{claim.story}</p>
    </section>
  );
}

function Items({ claim }: Readonly<{ claim: ClaimDetail }>) {
  return (
    <section className="panel">
      <h2>Items claimed</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col">Category</th>
            <th scope="col" className="number">
              Amount
            </th>
          </tr>
        </thead>
        <tbody>
          {claim.items.map((item) => (
            <tr key={item.description}>
              <td>{item.description}</td>
              <td>{words(item.category)}</td>
              <td className="number mono">{money(item.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Documents({ claim }: Readonly<{ claim: ClaimDetail }>) {
  return (
    <section className="panel">
      <h2>Documents</h2>
      <div className="documents">
        {claim.documents.map((document) => (
          <article key={document.name} className="document">
            <h3>{document.name}</h3>
            <p className="note">{words(document.type)}</p>
            <p>{document.content}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function PolicyFacts({ policy }: Readonly<{ policy: Policy }>) {
  const addOns = policy.add_on_details.map((addOn) => addOn.name).join(", ");
  return (
    <section className="panel">
      <h2>
        Policy <span className="mono">{policy.policy_id}</span>
      </h2>
      <dl className="facts">
        <dt>Plan</dt>
        <dd>{policy.plan_details.name}</dd>
        <dt>Add-ons</dt>
        <dd>{addOns || "None"}</dd>
        <dt>Trip</dt>
        <dd>
          {policy.trip.destination},{" "}
          <span className="mono">
            {policy.trip.departs_on} to {policy.trip.returns_on}
          </span>
        </dd>
        <dt>Trip cost</dt>
        <dd className="mono">{money(policy.trip.trip_cost)}</dd>
        <dt>Travelers</dt>
        <dd>{policy.travelers.join(", ")}</dd>
        <dt>Bought</dt>
        <dd className="mono">{policy.purchased_on}</dd>
      </dl>
    </section>
  );
}

function SignOffForm({
  claimId,
  onSignedOff,
}: Readonly<{ claimId: string; onSignedOff: () => void }>) {
  const { state } = useAuth();
  const action = useAction();
  if (state.status !== "signed-in") return null;
  const { handler } = state;
  if (!handler.can_approve_over_limit) {
    return (
      <p className="note sign-off-refused">
        {handler.name} can&apos;t sign off this payout. Only a handler who can
        sign off payouts over the limit may.
      </p>
    );
  }
  return (
    <form
      className="sign-off"
      onSubmit={(event) => {
        event.preventDefault();
        void action.act(async () => {
          await api.signOff(claimId);
          onSignedOff();
        });
      }}
    >
      <p className="note">
        You are signing off as <strong>{handler.name}</strong>.
      </p>
      <button type="submit" disabled={action.busy}>
        Sign off
      </button>
      {action.error && <p role="alert">{action.error}</p>}
    </form>
  );
}

function signedOff(claim: ClaimDetail, handlers: Handler[]): string | null {
  if (!claim.sign_off) return null;
  const { handler_id: id, signed_off_at: at } = claim.sign_off;
  const name = handlers.find((h) => h.handler_id === id)?.name ?? id;
  return `signed off by ${name} at ${dateTime(at)}`;
}

function Payment({
  claim,
  handlers,
}: Readonly<{ claim: ClaimDetail; handlers: Handler[] }>) {
  const sign = signedOff(claim, handlers);
  if (claim.payment) {
    return (
      <p className="payment">
        Paid <span className="mono">{money(claim.payment.amount)}</span> on{" "}
        {dateTime(claim.payment.paid_at)}
        {sign && `, ${sign}`}
      </p>
    );
  }
  if (claim.status === "awaiting_sign_off") {
    return (
      <p className="payment">
        The payment waits for a handler&apos;s sign-off.
      </p>
    );
  }
  if (claim.decision?.decision !== "approve") return null;
  return <p className="payment">Payment pending{sign && `, ${sign}`}</p>;
}

function undecided(claim: ClaimDetail): string {
  if (claim.status === "in_progress")
    return "The agent is working on this claim.";
  if (claim.status === "needs_handler") {
    return "The agent could not decide this claim after two attempts. It is left for a handler.";
  }
  return "No decision yet.";
}

function SectionsRead({ steps }: Readonly<{ steps: Step[] }>) {
  const read = [
    ...new Set(
      steps.filter((step) => step.kind === "section").map((step) => step.text),
    ),
  ];
  return (
    <section className="panel">
      <h2>What the agent read</h2>
      {read.length === 0 ? (
        <p className="note">The agent has not read any sections yet.</p>
      ) : (
        <ul className="sections-read" aria-label="Sections read">
          {read.map((section) => (
            <li key={section}>{section}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DecisionPanel({
  claim,
  handlers,
  onChange,
}: Readonly<{
  claim: ClaimDetail;
  handlers: Handler[];
  onChange: () => void;
}>) {
  const { decision } = claim;
  if (!decision) {
    return (
      <section className="panel">
        <h2>Decision</h2>
        <p className="note">{undecided(claim)}</p>
      </section>
    );
  }
  return (
    <section className="panel">
      <h2>Decision</h2>
      <p className="decision">
        <span className={`chip chip-${decision.decision}`}>
          {DECISION_LABELS[decision.decision]}
        </span>
        <span className="mono payout">{money(decision.payout)}</span>
      </p>
      <p>{decision.reason}</p>
      <p className="note">Based on: {decision.policy_section}</p>
      <Payment claim={claim} handlers={handlers} />
      {claim.status === "awaiting_sign_off" && !claim.evaluation_id && (
        <SignOffForm claimId={claim.claim_id} onSignedOff={onChange} />
      )}
    </section>
  );
}

function Steps({
  steps,
  working,
}: Readonly<{ steps: Step[]; working: boolean }>) {
  const idle = working
    ? "The agent is starting."
    : "The agent has not worked on this claim yet.";
  return (
    <section className="panel">
      <h2>The agent at work</h2>
      {steps.length === 0 ? (
        <p className="note">{idle}</p>
      ) : (
        <ol className="steps" aria-label="Agent steps">
          {steps.map((step) => (
            <li key={step.id} className={`step step-${step.kind}`}>
              <span className="step-kind">{STEP_LABELS[step.kind]}</span>
              <span className={step.kind === "reply" ? "" : "mono"}>
                {step.text}
              </span>
            </li>
          ))}
        </ol>
      )}
      {working && steps.length > 0 && <p className="note">Working...</p>}
    </section>
  );
}

// The agent owes the claim work until it is paid, so the page keeps refreshing.
function working(claim: ClaimDetail): boolean {
  return claim.status === "in_progress";
}

export function ClaimPage({ claimId }: Readonly<{ claimId: string }>) {
  const load = useCallback(async () => {
    const claim = await api.claim(claimId);
    const [policy, steps] = await Promise.all([
      api.policy(claim.policy_id),
      api.steps(claimId),
    ]);
    return { claim, policy, steps };
  }, [claimId]);
  const { data, error, refresh } = useLoad(load, (loaded) =>
    Boolean(loaded && working(loaded.claim)),
  );
  const { data: handlers } = useLoad(api.handlers);
  const run = useAction();

  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p>Loading the claim...</p>;

  const { claim, policy, steps } = data;
  const runAgent = () =>
    run.act(async () => {
      await api.runAgent(claimId);
      refresh();
    });

  return (
    <>
      <p>
        {claim.evaluation_id ? (
          <a href={`#/evaluations/${claim.evaluation_id}`}>
            Back to the evaluation
          </a>
        ) : (
          <a href="#/">Back to the queue</a>
        )}
      </p>
      <div className="page-head">
        <h1>
          <span className="mono">{claim.claim_id}</span> {claim.summary}
        </h1>
        <div className="actions">
          <Badge status={claim.status} />
          {claim.status === "waiting" && !claim.evaluation_id && (
            <button
              type="button"
              onClick={() => void runAgent()}
              disabled={run.busy}
            >
              Run agent
            </button>
          )}
        </div>
      </div>
      {run.error && <p role="alert">{run.error}</p>}
      {claim.evaluation_id && (
        <p className="note">
          This is a copy of <span className="mono">{claim.source_id}</span>{" "}
          worked for an evaluation. It is not in the queue and cannot be signed
          off.
        </p>
      )}
      <div className="claim-grid">
        <div className="column">
          <ClaimFacts claim={claim} />
          <Items claim={claim} />
          <Documents claim={claim} />
        </div>
        <div className="column">
          <DecisionPanel
            claim={claim}
            handlers={handlers ?? []}
            onChange={refresh}
          />
          <SectionsRead steps={steps} />
          <Steps steps={steps} working={working(claim)} />
          <PolicyFacts policy={policy} />
        </div>
      </div>
    </>
  );
}
