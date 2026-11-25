import { useState } from "react";
import { api, type Proposal } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { DiffView } from "./DiffView";
import { useAction } from "./hooks";

export function ProposalCard({
  proposal,
  canDecide,
  onDecided,
}: Readonly<{
  proposal: Proposal;
  canDecide: boolean;
  onDecided: () => void;
}>) {
  const action = useAction();
  const [asking, setAsking] = useState<"approve" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const noun = proposal.kind === "change" ? "change" : "recipe";

  const decide = async () => {
    await action.act(async () => {
      if (asking === "approve") await api.approveProposal(proposal.id);
      else await api.rejectProposal(proposal.id, reason.trim());
      setAsking(null);
      setReason("");
      onDecided();
    });
  };

  return (
    <section
      className="panel"
      aria-label={`Proposed ${noun} waiting for a handler`}
    >
      <h3>Proposed {noun}, waiting for a handler</h3>
      <p>{proposal.reason}</p>
      {proposal.model && proposal.reasoning_effort && (
        <dl className="facts">
          <dt>Model</dt>
          <dd className="mono">
            {proposal.model.before ?? "none"} to {proposal.model.after}
          </dd>
          <dt>Reasoning effort</dt>
          <dd className="mono">
            {proposal.reasoning_effort.before ?? "none"} to{" "}
            {proposal.reasoning_effort.after}
          </dd>
        </dl>
      )}
      <DiffView
        lines={proposal.diff}
        label={`Changes proposed to the ${noun}`}
      />
      {canDecide ? (
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setAsking("reject");
            }}
          >
            Reject
          </button>
          <button
            type="button"
            onClick={() => {
              setAsking("approve");
            }}
          >
            Approve
          </button>
        </div>
      ) : (
        <p className="note">
          Only a handler who can sign off payouts over the limit can approve or
          reject a proposal.
        </p>
      )}
      {action.error && <p role="alert">{action.error}</p>}
      {asking && (
        <ConfirmDialog
          title={
            asking === "approve"
              ? `Approve this ${noun}?`
              : `Reject this ${noun}?`
          }
          confirmLabel={asking === "approve" ? "Approve" : "Reject"}
          busy={action.busy || (asking === "reject" && reason.trim() === "")}
          onConfirm={() => void decide()}
          onCancel={() => {
            setAsking(null);
          }}
        >
          {asking === "approve" ? (
            <p>
              The improver carries on and evaluates the new{" "}
              {proposal.kind === "change" ? "version" : "recipe"}.
            </p>
          ) : (
            <label>
              Your reason, which goes back to the improver
              <textarea
                value={reason}
                rows={3}
                onChange={(event) => {
                  setReason(event.target.value);
                }}
              />
            </label>
          )}
        </ConfirmDialog>
      )}
    </section>
  );
}
