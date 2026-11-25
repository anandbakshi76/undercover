import type { ReactNode } from "react";

export function PageHeader({
  title,
  purpose,
  back,
  children,
}: Readonly<{
  title: ReactNode;
  purpose: string;
  back?: ReactNode;
  children?: ReactNode;
}>) {
  return (
    <div className="page-header">
      <div>
        {back && <p className="back">{back}</p>}
        <h1>{title}</h1>
        <p className="purpose">{purpose}</p>
      </div>
      {children && <div className="page-actions actions">{children}</div>}
    </div>
  );
}
