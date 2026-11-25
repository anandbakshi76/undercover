import { useCallback, useState } from "react";
import { api, type RecipeVersion } from "./api";
import { DiffView } from "./DiffView";
import { dateTime } from "./format";
import { useLoad } from "./hooks";

export function Recipes({ recipes }: Readonly<{ recipes: RecipeVersion[] }>) {
  const [chosen, setChosen] = useState<number | null>(null);
  const load = useCallback(
    () => (chosen === null ? Promise.resolve(null) : api.recipeDiff(chosen)),
    [chosen],
  );
  const { data } = useLoad(load);
  return (
    <section className="panel table-panel" aria-label="Recipe versions">
      <h2>Recipe versions</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Version</th>
            <th scope="col">Reason</th>
            <th scope="col">Approved</th>
            <th scope="col">Changes</th>
          </tr>
        </thead>
        <tbody>
          {[...recipes].reverse().map((recipe) => (
            <tr key={recipe.version}>
              <td>
                <span className="mono">{recipe.version}</span>{" "}
                {recipe.is_current && (
                  <span className="badge run-finished">Current</span>
                )}
              </td>
              <td>{recipe.reason}</td>
              <td>
                {recipe.approved_by}, {dateTime(recipe.approved_at)}
              </td>
              <td>
                <button
                  type="button"
                  className="secondary"
                  aria-pressed={chosen === recipe.version}
                  onClick={() => {
                    setChosen(recipe.version);
                  }}
                >
                  Show changes in recipe {recipe.version}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && (
        <>
          <h3>
            {data.previous === null
              ? `Recipe ${String(data.version)}`
              : `Recipe ${String(data.version)} compared with recipe ${String(data.previous)}`}
          </h3>
          <DiffView lines={data.lines} label="Changes to the recipe" />
        </>
      )}
    </section>
  );
}
