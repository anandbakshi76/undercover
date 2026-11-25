import { useCallback } from "react";
import { api, type ClaimGrade } from "./api";
import { Badge } from "./Badge";
import {
  clock,
  cost,
  DECISION_LABELS,
  duration,
  gradeText,
  money,
  percent,
  RUN_LABELS,
  SET_LABELS,
  yesNo,
} from "./format";
import { useLoad } from "./hooks";

function Answer({
  answer,
}: Readonly<{
  answer: { decision: "approve" | "decline" | "refer"; payout: number };
}>) {
  return (
    <>
      {DECISION_LABELS[answer.decision]}
      {answer.decision === "approve" && (
        <span className="mono"> {money(answer.payout)}</span>
      )}
    </>
  );
}

function GradeCells({ grade }: Readonly<{ grade: ClaimGrade | null }>) {
  if (grade === null) {
    return (
      <td colSpan={3} className="note">
        Not graded
      </td>
    );
  }
  return (
    <>
      <td className="number mono">
        <span className="badge grade">{grade.letter}</span>{" "}
        {gradeText(grade.score)}
      </td>
      <td>{yesNo(grade.same_grounds)}</td>
      <td>{yesNo(grade.same_section)}</td>
    </>
  );
}

export function EvaluationPage({ runId }: Readonly<{ runId: string }>) {
  const load = useCallback(() => api.evaluation(runId), [runId]);
  const { data, error } = useLoad(
    load,
    (loaded) => loaded?.status === "running",
  );

  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p>Loading the evaluation...</p>;

  const measures: [string, string][] = [
    ["Decision accuracy", percent(data.decision_accuracy)],
    ["Accuracy", percent(data.accuracy)],
    ["Grade", gradeText(data.grade)],
    ["Cost of mistakes", money(data.cost_of_mistakes)],
    ["Straight through", percent(data.straight_through)],
    ["Cost per claim", cost(data.cost_per_claim)],
    ["Time per claim", duration(data.seconds_per_claim)],
    ["Run time", duration(data.run_seconds)],
    ["Judge cost", cost(data.judge_cost)],
  ];

  return (
    <>
      <p>
        <a href="#/scoreboard">Back to the scoreboard</a>
      </p>
      <div className="page-head">
        <h1>
          {SET_LABELS[data.evaluation_set]}
          {data.tiny ? ", tiny" : ""}
        </h1>
        <span className={`badge run-${data.status}`}>
          {RUN_LABELS[data.status]}
        </span>
      </div>
      <p className="note">
        <span className="mono">{data.run_id}</span>, {data.claim_count} claims,{" "}
        {data.concurrency} claim agents at once, model{" "}
        <span className="mono">{data.model}</span>, reasoning{" "}
        {data.reasoning_effort}.
      </p>
      <dl className="panel measures" aria-label="Measures">
        {measures.map(([name, value]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="panel table-panel">
        <table aria-label="Evaluation claims">
          <thead>
            <tr>
              <th scope="col">Claim</th>
              <th scope="col">State</th>
              <th scope="col">Handler</th>
              <th scope="col">Agent</th>
              <th scope="col">Result</th>
              <th scope="col" className="number">
                Grade
              </th>
              <th scope="col">Same grounds</th>
              <th scope="col">Same section</th>
              <th scope="col" className="number">
                Cost of mistake
              </th>
              <th scope="col" className="number">
                Cost
              </th>
              <th scope="col" className="number">
                Time
              </th>
              <th scope="col" className="wide">
                Started
              </th>
            </tr>
          </thead>
          <tbody>
            {data.claims.map((item) => (
              <tr key={item.claim_id} className={item.right ? "" : "miss"}>
                <td>
                  <a className="mono" href={`#/claims/${item.claim_id}`}>
                    {item.source_id}
                  </a>
                </td>
                <td>
                  <Badge status={item.status} />
                </td>
                <td>
                  <Answer answer={item.handler} />
                </td>
                <td>
                  <Answer answer={item.agent} />
                </td>
                <td>
                  <span className={`badge ${item.right ? "hit" : "missed"}`}>
                    {item.right ? "Match" : "Miss"}
                  </span>
                </td>
                <GradeCells grade={item.grade} />
                <td className="number mono">{money(item.mistake_cost)}</td>
                <td className="number mono">{cost(item.cost)}</td>
                <td className="number mono">{duration(item.seconds)}</td>
                <td className="wide mono">
                  {item.started_at ? clock(item.started_at) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
