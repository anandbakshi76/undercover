import type { Round } from "./api";
import { percent } from "./format";
import { Empty } from "./States";

const CHART = { width: 560, height: 200, left: 40, bottom: 28, top: 12 };

export function RoundChart({ rounds }: Readonly<{ rounds: Round[] }>) {
  const tried = rounds
    .filter((r) => r.version_tried !== null && r.after)
    .reverse();
  if (tried.length === 0) {
    return (
      <Empty
        title="No versions tried yet"
        next="The chart fills in as rounds reach a verdict."
      />
    );
  }
  const inner = CHART.width - CHART.left - 16;
  const high = CHART.height - CHART.bottom;
  const x = (index: number) =>
    CHART.left +
    (tried.length === 1 ? inner / 2 : (index / (tried.length - 1)) * inner);
  const y = (share: number) => CHART.top + (1 - share) * (high - CHART.top);
  const line = (pick: (r: Round) => number | null) =>
    tried
      .map((r, index) => ({ r, index, v: pick(r) }))
      .filter((p) => p.v !== null)
      .map((p) => `${String(x(p.index))},${String(y(p.v ?? 0))}`)
      .join(" ");
  return (
    <figure>
      <svg
        viewBox={`0 0 ${String(CHART.width)} ${String(CHART.height)}`}
        role="img"
        aria-label="Held-out accuracy and grade of each version tried"
        className="chart"
      >
        {[0, 0.5, 1].map((tick) => (
          <g key={tick}>
            <line
              className="chart-grid"
              x1={CHART.left}
              x2={CHART.width - 16}
              y1={y(tick)}
              y2={y(tick)}
            />
            <text
              className="chart-label"
              x={CHART.left - 6}
              y={y(tick) + 4}
              textAnchor="end"
            >
              {percent(tick)}
            </text>
          </g>
        ))}
        <polyline
          className="chart-accuracy"
          fill="none"
          points={line((r) => r.after?.accuracy ?? null)}
        />
        <polyline
          className="chart-grade"
          fill="none"
          points={line((r) =>
            r.after?.grade == null ? null : r.after.grade / 3,
          )}
        />
        {tried.map((r, index) => (
          <text
            key={r.round_id}
            className="chart-label"
            x={x(index)}
            y={CHART.height - 8}
            textAnchor="middle"
          >
            v{r.version_tried}
          </text>
        ))}
      </svg>
      <figcaption className="note">
        <span className="swatch swatch-accuracy" /> Accuracy{" "}
        <span className="swatch swatch-grade" /> Grade (out of 3)
      </figcaption>
    </figure>
  );
}
