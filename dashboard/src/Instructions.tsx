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
        Read only. Each claim agent receives these at the start of its work.
      </p>
      <section className="panel instructions-part" aria-label="Working a claim">
        <h2>Working a claim</h2>
        <pre className="instructions">{data.claim}</pre>
      </section>
    </>
  );
}
