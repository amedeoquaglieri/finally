import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  meta?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
  testId?: string;
  /** Extra attributes on the root, e.g. data-* hooks for tests. */
  attrs?: Record<string, string>;
}

export default function Panel({ title, meta, className = "", bodyClassName = "", children, testId, attrs }: PanelProps) {
  return (
    <section data-testid={testId} {...attrs} className={`flex min-h-0 flex-col border border-line bg-panel ${className}`}>
      <header className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-line px-3">
        <h2 className="text-[13px] font-medium text-muted">{title}</h2>
        {meta ? <div className="flex items-center gap-2 text-xs text-muted">{meta}</div> : null}
      </header>
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}
