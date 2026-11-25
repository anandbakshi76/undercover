import { type ReactNode, useEffect, useId, useRef } from "react";

// The dashboard's own dialog, shown before any action that changes the agent or a setting.
export function ConfirmDialog({
  title,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
  children,
}: Readonly<{
  title: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children: ReactNode;
}>) {
  const titleId = useId();
  const cancel = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancel.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("keydown", close);
    };
  }, [onCancel]);

  return (
    <div className="overlay">
      <div
        className="dialog panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <h2 id={titleId}>{title}</h2>
        <div className="dialog-body">{children}</div>
        <div className="dialog-actions">
          <button
            type="button"
            className="secondary"
            ref={cancel}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
