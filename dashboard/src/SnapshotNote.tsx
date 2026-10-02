import type { ReactNode } from "react";

import { describeSnapshot, type Snapshot } from "./snapshot";

/**
 * Says, in words, that a view is the front's kept copy and how old it is
 * (D-284), and nothing when it is live. Words rather than only a style: it has to survive a screenshot, a
 * screen reader and a greyscale print, as the simulated badge does.
 */
interface NoteProps {
  snapshot: Snapshot | null;
  /** The subject and verb: "This panel shows". */
  what: string;
  /** Anything the view needs to add about reading it as a snapshot. */
  children?: ReactNode;
}

export function SnapshotNote({ snapshot, what, children }: NoteProps) {
  if (snapshot === null) {
    return null;
  }
  return (
    <p role="alert" className="snapshot-note">
      <strong>Not live.</strong> The platform is not answering. {what}{" "}
      {describeSnapshot(snapshot, new Date())}.{children}
    </p>
  );
}
