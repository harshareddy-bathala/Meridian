// Whether what is on screen is live, or the front's kept copy of it (D-282,
// D-284), and how old that copy is.
//
// The front answers every request the platform answers with the platform's own
// answer. When the platform does not answer, it serves the last copy it kept,
// marked `Meridian-Cache: STALE` and carrying the `Date` the platform gave it
// (EVALUATION.md §12.2). A stale number presented as current is the failure
// that drill names as the serious one, so every view that drew on such a copy
// says so, with its age.

import type { Fetcher } from "./api";
import { formatAge } from "./format";

/** Long enough for a slow Pi, short enough that a page never waits on a dead upstream. */
export const REQUEST_TIMEOUT_MS = 20_000;

/** A view answered from the front's copy. */
export interface Snapshot {
  /** When the platform made the oldest copy the view used; null if unknown. */
  takenAt: Date | null;
}

/** The copy's age if this response is one, or null if the platform answered it. */
export function snapshotOf(response: Response): Snapshot | null {
  if (response.headers.get("Meridian-Cache") !== "STALE") {
    return null;
  }
  const date = response.headers.get("Date");
  const parsed = date === null ? Number.NaN : Date.parse(date);
  return { takenAt: Number.isNaN(parsed) ? null : new Date(parsed) };
}

/** The older of two: a view is as old as the oldest thing it shows. */
export function oldest(a: Snapshot | null, b: Snapshot | null): Snapshot | null {
  if (a === null || b === null) {
    return a ?? b;
  }
  if (a.takenAt === null || b.takenAt === null) {
    return { takenAt: null };
  }
  return a.takenAt <= b.takenAt ? a : b;
}

export interface SnapshotWatch {
  /** Every request through this one times out, and is checked for a copy. */
  fetcher: Fetcher;
  /** The oldest copy any request so far was answered with, or null if none was. */
  snapshot: () => Snapshot | null;
}

/**
 * One refresh's requests. A new watch per refresh, so a copy served during an
 * outage stops being reported by the first refresh after the platform answers.
 */
export function watchSnapshots(
  base: Fetcher = (url, init) => fetch(url, init),
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): SnapshotWatch {
  let seen: Snapshot | null = null;
  const fetcher: Fetcher = async (url, init) => {
    const signal = AbortSignal.any([init.signal, AbortSignal.timeout(timeoutMs)]);
    const response = await base(url, { signal });
    seen = oldest(seen, snapshotOf(response));
    return response;
  };
  return { fetcher, snapshot: () => seen };
}

/** "a snapshot taken 14:49 UTC, 7 min ago", or one of unknown age. */
export function describeSnapshot(snapshot: Snapshot, now: Date): string {
  const { takenAt } = snapshot;
  if (takenAt === null) {
    return "a snapshot of unknown age";
  }
  const clock = takenAt.toISOString().slice(11, 16);
  return `a snapshot taken ${clock} UTC, ${formatAge(takenAt.toISOString(), now)}`;
}

/** A view's class, with `snapshot` added when it shows a kept copy. */
export function snapshotClass(base: string, snapshot: Snapshot | null): string | undefined {
  const names = [base, snapshot === null ? "" : "snapshot"].filter((name) => name !== "");
  return names.length === 0 ? undefined : names.join(" ");
}
