import { api } from "./api";
import { useLoad } from "./hooks";

export function Instructions() {
  const { data, error } = useLoad(api.instructions);

  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p>Loading the instructions...</p>;

  return (
    <>
      <div className="page-head">
        <h1>Agent instructions</h1>
      </div>
      <p className="note">
        Read only. The queue agent receives the first set at the start of every
        run, and each claim agent receives the second.
      </p>
      <section
        className="panel instructions-part"
        aria-label="Running the queue"
      >
        <h2>Running the queue</h2>
        <pre className="instructions">{data.queue}</pre>
      </section>
      <section className="panel instructions-part" aria-label="Working a claim">
        <h2>Working a claim</h2>
        <pre className="instructions">{data.claim}</pre>
      </section>
    </>
  );
}
