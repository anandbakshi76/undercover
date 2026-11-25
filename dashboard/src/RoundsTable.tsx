import type { Round } from "./api";
import { cost, dateTime, percent } from "./format";
import { Empty } from "./States";

const OPEN_WORDS = {
  working: "Open",
  waiting: "Open",
  stopped: "Stopped",
  finished: "Finished",
} as const;

function Verdict({ round }: Readonly<{ round: Round }>) {
  const badge = round.verdict ? (
    <span
      className={`badge ${round.verdict === "kept" ? "run-finished" : "run-stopped"}`}
    >
      {round.verdict === "kept" ? "Kept" : "Rolled back"}
    </span>
  ) : (
    <span className="badge run-running">{OPEN_WORDS[round.status]}</span>
  );
  return (
    <>
      {badge}
      {round.why && <span className="note"> {round.why}</span>}
    </>
  );
}

export function RoundsTable({ rounds }: Readonly<{ rounds: Round[] }>) {
  if (rounds.length === 0) {
    return (
      <Empty
        title="No rounds yet"
        next="Start a round and the improver will propose a change."
      />
    );
  }
  return (
    <table className="rounds">
      <thead>
        <tr>
          <th scope="col">Round</th>
          <th scope="col">Started</th>
          <th scope="col">Version tried</th>
          <th scope="col">Reason</th>
          <th scope="col" className="number">
            Accuracy before
          </th>
          <th scope="col" className="number">
            Accuracy after
          </th>
          <th scope="col" className="number">
            Grade before
          </th>
          <th scope="col" className="number">
            Grade after
          </th>
          <th scope="col">Verdict</th>
          <th scope="col" className="number">
            Cost
          </th>
        </tr>
      </thead>
      <tbody>
        {rounds.map((round) => (
          <tr key={round.round_id}>
            <td className="mono">{round.round_id}</td>
            <td>{dateTime(round.started_at)}</td>
            <td className="mono">{round.version_tried ?? "None"}</td>
            <td>
              <span className="clamp" title={round.reason ?? undefined}>
                {round.reason ?? ""}
              </span>
            </td>
            <td className="number mono">
              {round.before ? percent(round.before.accuracy) : "-"}
            </td>
            <td className="number mono">
              {round.after ? percent(round.after.accuracy) : "-"}
            </td>
            <td className="number mono">
              {round.before?.grade?.toFixed(2) ?? "-"}
            </td>
            <td className="number mono">
              {round.after?.grade?.toFixed(2) ?? "-"}
            </td>
            <td>
              <Verdict round={round} />
            </td>
            <td className="number mono">{cost(round.spent)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
