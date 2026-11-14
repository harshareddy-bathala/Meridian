import type { ReactNode } from "react";

// A table that may be wider than the screen scrolls sideways inside this frame,
// so the page never does (D-341). A frame that scrolls must be reachable by
// keyboard, or a reader without a pointer cannot see its far columns (WCAG
// 2.1.1; axe's scrollable-region-focusable). So it takes focus, and as a named
// region it says what it holds when it does.

export function TableScroll({
  label,
  className = "table-scroll",
  children,
}: {
  /** What the table holds, said when the frame takes focus. */
  label: string;
  /** The frame's classes, for a view that marks its copy (D-284). */
  className?: string | undefined;
  children: ReactNode;
}) {
  return (
    <div className={className} tabIndex={0} role="region" aria-label={label}>
      {children}
    </div>
  );
}
