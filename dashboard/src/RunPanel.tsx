import type { Run } from "./api";
import { RUN_LABELS, TODO_LABELS } from "./format";

export function RunPanel({ run }: Readonly<{ run: Run }>) {
  const worked = run.claims_worked.length;
  return (
    <section className="panel run" aria-label="Current run">
      <div className="run-head">
        <h2>Current run</h2>
        <span className={`badge run-${run.status}`}>
          {RUN_LABELS[run.status]}
        </span>
      </div>
      <p className="note">
        <span className="mono">{worked}</span> of{" "}
        <span className="mono">{run.claim_limit}</span>{" "}
        {run.claim_limit === 1 ? "claim" : "claims"} worked
      </p>
      {run.todos.length === 0 ? (
        <p className="note">
          {run.status === "running"
            ? "The agent is planning its work."
            : "The agent wrote no to-do list."}
        </p>
      ) : (
        <ol className="todos" aria-label="To-do list">
          {run.todos.map((todo, index) => (
            <li key={`${String(index)}-${todo.content}`} className="todo">
              <span className={`badge todo-${todo.status}`}>
                {TODO_LABELS[todo.status]}
              </span>
              <span>{todo.content}</span>
            </li>
          ))}
        </ol>
      )}
      {run.summary && (
        <div className="summary">
          <h3>Summary</h3>
          <p>{run.summary}</p>
        </div>
      )}
    </section>
  );
}
