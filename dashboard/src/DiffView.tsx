import type { DiffLine } from "./api";

const MARKS = { added: "+", removed: "-", same: " ", gap: "..." } as const;
const WORDS = {
  added: "Added",
  removed: "Removed",
  same: "",
  gap: "",
} as const;

export function DiffView({
  lines,
  label,
}: Readonly<{ lines: DiffLine[]; label: string }>) {
  if (lines.length === 0) {
    return <p className="note">No changes to the text.</p>;
  }
  return (
    <div className="diff" role="group" aria-label={label}>
      {lines.map((line, index) => (
        <div
          // The same line can appear twice in a diff, so the position is its key.
          key={`${String(index)}-${line.kind}-${line.text}`}
          className={`diff-line diff-${line.kind}`}
        >
          <span className="diff-mark" aria-hidden="true">
            {MARKS[line.kind]}
          </span>
          {WORDS[line.kind] && (
            <span className="sr-only">{WORDS[line.kind]}: </span>
          )}
          <span className="diff-text">
            {line.kind === "gap" ? "" : line.text}
          </span>
        </div>
      ))}
    </div>
  );
}
