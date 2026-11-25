import type { ReactNode } from "react";

export function PageHeader({
  title,
  purpose,
  children,
}: Readonly<{ title: string; purpose: string; children?: ReactNode }>) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        <p className="purpose">{purpose}</p>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}
