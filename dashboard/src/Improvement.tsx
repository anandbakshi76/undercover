import { useCallback, useState } from "react";
import { api, type Round } from "./api";
import { useAuth } from "./authContext";
import { ConfirmDialog } from "./ConfirmDialog";
import { money } from "./format";
import { useAction, useLoad } from "./hooks";
import { PageHeader } from "./PageHeader";
import { ProposalCard } from "./ProposalCard";
import { Recipes } from "./Recipes";
import { RoundChart } from "./RoundChart";
import { RoundsTable } from "./RoundsTable";
import { Stages } from "./Stages";
import { ErrorState, Loading } from "./States";

const WORKING = new Set<Round["status"]>(["working", "waiting"]);

function CurrentRound({
  round,
  open,
  canAct,
  onDecided,
}: Readonly<{
  round: Round;
  open: boolean;
  canAct: boolean;
  onDecided: () => void;
}>) {
  return (
    <section className="panel" aria-label="Current round">
      <h2>
        {open ? "Open round" : "Latest round"}{" "}
        <span className="mono">{round.round_id}</span>
      </h2>
      <p className="note">
        Spent {money(round.spent)} of {money(round.limit)}. Following recipe{" "}
        {round.recipe_version}.
      </p>
      <Stages round={round} />
      {round.pending && (
        <ProposalCard
          proposal={round.pending}
          canDecide={canAct}
          onDecided={onDecided}
        />
      )}
    </section>
  );
}

function RoundButton({
  open,
  canAct,
  onAsk,
}: Readonly<{
  open: boolean;
  canAct: boolean;
  onAsk: (ask: "start" | "stop") => void;
}>) {
  return (
    <button
      type="button"
      className={open ? "secondary" : undefined}
      disabled={!canAct}
      onClick={() => {
        onAsk(open ? "stop" : "start");
      }}
    >
      {open ? "Stop the round" : "Start a round"}
    </button>
  );
}

function useCanAct(): boolean {
  const { state } = useAuth();
  return state.status === "signed-in" && state.handler.can_approve_over_limit;
}

export function Improvement() {
  const canAct = useCanAct();
  const poll = useCallback(
    (data: Round[] | null) => !data || data.some((r) => WORKING.has(r.status)),
    [],
  );
  const rounds = useLoad(api.rounds, poll);
  const recipes = useLoad(api.recipes);
  const action = useAction();
  const [confirm, setConfirm] = useState<"start" | "stop" | null>(null);

  const error = rounds.error ?? recipes.error;
  const header = (
    <PageHeader
      title="Improvement"
      purpose="Rounds in which the improver proposes better versions of the claim agent. Nothing it proposes takes effect until a handler approves it."
    />
  );
  if (error) {
    return (
      <>
        {header}
        <ErrorState
          message={error}
          next="Check that the backend is running, then reload the page."
        />
      </>
    );
  }
  if (!rounds.data || !recipes.data) {
    return (
      <>
        {header}
        <Loading what="the improvement rounds" />
      </>
    );
  }

  const open = rounds.data.find((r) => WORKING.has(r.status)) ?? null;
  const latest = open ?? rounds.data.at(0) ?? null;

  const run = async () => {
    await action.act(async () => {
      if (confirm === "start") await api.startRound();
      else if (open) await api.stopRound(open.round_id);
      setConfirm(null);
      rounds.refresh();
    });
  };

  return (
    <>
      <PageHeader
        title="Improvement"
        purpose="Rounds in which the improver proposes better versions of the claim agent. Nothing it proposes takes effect until a handler approves it."
      >
        <RoundButton open={open !== null} canAct={canAct} onAsk={setConfirm} />
      </PageHeader>
      {!canAct && (
        <p className="note">
          Only a handler who can sign off payouts over the limit can start or
          stop a round, or approve or reject a proposal.
        </p>
      )}
      {action.error && <p role="alert">{action.error}</p>}
      {latest && (
        <CurrentRound
          round={latest}
          open={open !== null}
          canAct={canAct}
          onDecided={rounds.refresh}
        />
      )}
      <section className="panel" aria-label="Held-out scores by version">
        <h2>Held-out accuracy and grade of each version tried</h2>
        <RoundChart rounds={rounds.data} />
      </section>
      <section className="panel table-panel" aria-label="Rounds">
        <h2>Rounds</h2>
        <RoundsTable rounds={rounds.data} />
      </section>
      <Recipes recipes={recipes.data} />
      {confirm && (
        <ConfirmDialog
          title={confirm === "start" ? "Start a round?" : "Stop the round?"}
          confirmLabel={
            confirm === "start" ? "Start the round" : "Stop the round"
          }
          busy={action.busy}
          onConfirm={() => void run()}
          onCancel={() => {
            setConfirm(null);
          }}
        >
          {confirm === "start" ? (
            <p>
              The improver reads the results and proposes a change. The round
              spends up to its limit on evaluations and the improver&apos;s own
              calls.
            </p>
          ) : (
            <p>
              The round stops. The current version stays as it is unless the
              round had already kept a new one.
            </p>
          )}
        </ConfirmDialog>
      )}
    </>
  );
}
