import { useState } from "react";
import { api, type EvaluationSet } from "./api";
import {
  cost,
  dateTime,
  duration,
  money,
  percent,
  RUN_LABELS,
  SET_LABELS,
} from "./format";
import { useAction, useLoad } from "./hooks";
import { DEFAULT_CONCURRENCY, MAX_CONCURRENCY } from "./Queue";

export function Scoreboard() {
  const [concurrency, setConcurrency] = useState(DEFAULT_CONCURRENCY);
  const [evaluationSet, setEvaluationSet] =
    useState<EvaluationSet>("evaluation");
  const [tiny, setTiny] = useState(false);
  const { data, error, refresh } = useLoad(api.evaluations, (loaded) =>
    Boolean(loaded?.some((item) => item.status === "running")),
  );
  const action = useAction();

  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p>Loading the scoreboard...</p>;

  const running = data.some((item) => item.status === "running");
  const start = () =>
    action.act(async () => {
      await api.startEvaluation(concurrency, evaluationSet, tiny);
      refresh();
    });

  return (
    <>
      <div className="page-head">
        <h1>Scoreboard</h1>
      </div>
      <form
        className="panel eval-form"
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <h2>Start an evaluation</h2>
        <div className="run-form">
          <label htmlFor="set">Set</label>
          <select
            id="set"
            value={evaluationSet}
            onChange={(event) => {
              setEvaluationSet(event.target.value as EvaluationSet);
            }}
          >
            <option value="evaluation">{SET_LABELS.evaluation}</option>
            <option value="held_out">{SET_LABELS.held_out}</option>
          </select>
          <label htmlFor="size">Claims</label>
          <select
            id="size"
            value={tiny ? "tiny" : "whole"}
            onChange={(event) => {
              setTiny(event.target.value === "tiny");
            }}
          >
            <option value="whole">Whole set</option>
            <option value="tiny">Tiny (5 claims)</option>
          </select>
          <label htmlFor="agents">Claim agents at once</label>
          <input
            id="agents"
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
            Start an evaluation
          </button>
        </div>
      </form>
      {action.error && <p role="alert">{action.error}</p>}
      {data.length === 0 ? (
        <p className="note">There have been no evaluations yet.</p>
      ) : (
        <div className="panel table-panel">
          <table aria-label="Evaluations">
            <thead>
              <tr>
                <th scope="col">Started</th>
                <th scope="col">Set</th>
                <th scope="col" className="number">
                  Claims
                </th>
                <th scope="col" className="number">
                  At once
                </th>
                <th scope="col">Model</th>
                <th scope="col">Status</th>
                <th scope="col" className="number">
                  Decision accuracy
                </th>
                <th scope="col" className="number">
                  Accuracy
                </th>
                <th scope="col" className="number">
                  Cost of mistakes
                </th>
                <th scope="col" className="number">
                  Straight through
                </th>
                <th scope="col" className="number">
                  Cost per claim
                </th>
                <th scope="col" className="number">
                  Time per claim
                </th>
                <th scope="col" className="number">
                  Run time
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((item) => (
                <tr key={item.run_id}>
                  <td>
                    <a className="mono" href={`#/evaluations/${item.run_id}`}>
                      {dateTime(item.started_at)}
                    </a>
                  </td>
                  <td>{SET_LABELS[item.evaluation_set]}</td>
                  <td className="number mono">{item.claim_count}</td>
                  <td className="number mono">{item.concurrency}</td>
                  <td>
                    <span className="mono">{item.model}</span>
                    <span className="note">
                      {" "}
                      reasoning {item.reasoning_effort}
                    </span>
                  </td>
                  <td>
                    <span className={`badge run-${item.status}`}>
                      {RUN_LABELS[item.status]}
                    </span>
                  </td>
                  <td className="number mono">
                    {percent(item.decision_accuracy)}
                  </td>
                  <td className="number mono">{percent(item.accuracy)}</td>
                  <td className="number mono">
                    {money(item.cost_of_mistakes)}
                  </td>
                  <td className="number mono">
                    {percent(item.straight_through)}
                  </td>
                  <td className="number mono">{cost(item.cost_per_claim)}</td>
                  <td className="number mono">
                    {duration(item.seconds_per_claim)}
                  </td>
                  <td className="number mono">{duration(item.run_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
