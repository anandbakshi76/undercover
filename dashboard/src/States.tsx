// The three states every page needs besides its data, each saying what to do next.

export function Loading({ what }: Readonly<{ what: string }>) {
  return (
    <p className="state state-loading" role="status">
      Loading {what}...
    </p>
  );
}

export function Empty({
  title,
  next,
}: Readonly<{ title: string; next: string }>) {
  return (
    <div className="state state-empty">
      <p>
        <strong>{title}</strong>
      </p>
      <p className="note">{next}</p>
    </div>
  );
}

export function ErrorState({
  message,
  next,
}: Readonly<{ message: string; next: string }>) {
  return (
    <div className="state state-error" role="alert">
      <p>{message}</p>
      <p className="note">{next}</p>
    </div>
  );
}
