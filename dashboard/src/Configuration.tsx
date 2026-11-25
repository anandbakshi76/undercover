import { useCallback, useState } from "react";
import { api, type ConfigVersion } from "./api";
import { DiffView } from "./DiffView";
import { cost, dateTime, duration, percent } from "./format";
import { useLoad } from "./hooks";
import { PageHeader } from "./PageHeader";
import { ErrorState, Loading } from "./States";

function scoreCells(version: ConfigVersion) {
  const score = version.scores.held_out;
  if (!score) {
    return (
      <>
        <td className="number note" colSpan={4}>
          Not evaluated yet
        </td>
      </>
    );
  }
  return (
    <>
      <td className="number mono">{percent(score.accuracy)}</td>
      <td className="number mono">
        {score.grade === null ? "Not graded" : score.grade.toFixed(2)}
      </td>
      <td className="number mono">{cost(score.cost_per_claim)}</td>
      <td className="number mono">{duration(score.seconds_per_claim)}</td>
    </>
  );
}

function Changes({ version }: Readonly<{ version: number }>) {
  const load = useCallback(() => api.configurationDiff(version), [version]);
  const { data, error } = useLoad(load);
  if (error) {
    return <ErrorState message={error} next="Choose the version again." />;
  }
  if (!data) return <Loading what="the changes" />;
  return (
    <section
      className="panel"
      aria-label={`Changes in version ${String(version)}`}
    >
      <h2>
        {data.previous === null
          ? `Version ${String(version)}`
          : `Version ${String(version)} compared with version ${String(data.previous)}`}
      </h2>
      <p className="note">
        {data.reason} Proposed by {data.proposed_by}, approved by{" "}
        {data.approved_by} on {dateTime(data.approved_at)}.
      </p>
      {data.previous === null ? (
        <p className="note">
          Version 1 is the claim agent as it was before the improver, so there
          is nothing before it to compare with.
        </p>
      ) : (
        <>
          <dl className="facts">
            <dt>Model</dt>
            <dd className="mono">
              {data.model.before === data.model.after
                ? data.model.after
                : `${String(data.model.before)} to ${data.model.after}`}
            </dd>
            <dt>Reasoning effort</dt>
            <dd className="mono">
              {data.reasoning_effort.before === data.reasoning_effort.after
                ? data.reasoning_effort.after
                : `${String(data.reasoning_effort.before)} to ${data.reasoning_effort.after}`}
            </dd>
          </dl>
          <h3>Instructions</h3>
          <DiffView
            lines={data.instructions}
            label="Changes to the instructions"
          />
        </>
      )}
    </section>
  );
}

export function Configuration() {
  const versions = useLoad(api.configurations);
  const current = useLoad(api.currentConfiguration);
  const [chosen, setChosen] = useState<number | null>(null);

  const error = versions.error ?? current.error;
  if (error) {
    return (
      <>
        <PageHeader
          title="Configuration"
          purpose="The claim agent's versions."
        />
        <ErrorState
          message={error}
          next="Check that the backend is running, then reload the page."
        />
      </>
    );
  }
  if (!versions.data || !current.data)
    return <Loading what="the configuration" />;

  const shownVersion = chosen ?? current.data.version;
  const newestFirst = [...versions.data].sort((a, b) => b.version - a.version);

  return (
    <>
      <PageHeader
        title="Configuration"
        purpose="The claim agent's instructions, model and reasoning effort, in versions. A dispatch and an evaluation use the current version."
      />
      <section className="panel" aria-label="Current version">
        <h2>Current version: {current.data.version}</h2>
        <dl className="facts">
          <dt>Model</dt>
          <dd className="mono">{current.data.model}</dd>
          <dt>Reasoning effort</dt>
          <dd className="mono">{current.data.reasoning_effort}</dd>
          <dt>Approved</dt>
          <dd>
            by {current.data.approved_by} on{" "}
            {dateTime(current.data.approved_at)}
          </dd>
        </dl>
        <h3>Instructions</h3>
        <pre className="instructions">{current.data.instructions}</pre>
      </section>
      <section className="panel table-panel" aria-label="Versions">
        <h2>Versions</h2>
        <p className="note">
          Scores are the averages of the held-out evaluations the improver ran
          on each version.
        </p>
        <table>
          <thead>
            <tr>
              <th scope="col">Version</th>
              <th scope="col">Reason</th>
              <th scope="col">Model</th>
              <th scope="col">Approved</th>
              <th scope="col" className="number">
                Accuracy
              </th>
              <th scope="col" className="number">
                Grade
              </th>
              <th scope="col" className="number">
                Cost per claim
              </th>
              <th scope="col" className="number">
                Time per claim
              </th>
              <th scope="col">Changes</th>
            </tr>
          </thead>
          <tbody>
            {newestFirst.map((version) => (
              <tr key={version.version}>
                <td>
                  <span className="mono">{version.version}</span>{" "}
                  {version.is_current && (
                    <span className="badge run-finished">Current</span>
                  )}
                </td>
                <td>{version.reason}</td>
                <td className="mono">
                  {version.model}, {version.reasoning_effort}
                </td>
                <td>
                  {version.approved_by}, {dateTime(version.approved_at)}
                </td>
                {scoreCells(version)}
                <td>
                  <button
                    type="button"
                    className="secondary"
                    aria-pressed={shownVersion === version.version}
                    onClick={() => {
                      setChosen(version.version);
                    }}
                  >
                    Show changes in {version.version}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <Changes version={shownVersion} />
    </>
  );
}
