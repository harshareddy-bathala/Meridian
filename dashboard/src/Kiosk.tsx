import { useEffect, useState } from "react";

import { InsightsView } from "./Insights";
import { LossesView } from "./Losses";
import { Network } from "./Network";
import type { Route } from "./route";
import { ScheduleView } from "./ScheduleTimeline";
import { useStations } from "./useStations";

// Every view in turn, for a screen nobody touches. Each is the view itself,
// with its own snapshot note and its own simulated badges, so nothing on the
// screen says more than the view would on a desk.

/** How long each view stays up. */
export const KIOSK_STEP_MS = 20_000;

/** How long a touch, a click or a key holds the cycle still. */
export const KIOSK_HOLD_MS = 60_000;

const STEPS = ["network", "schedule", "losses", "insights"] as const;

function useCycle(): [number, () => void] {
  const [step, setStep] = useState(0);
  const [heldUntil, setHeldUntil] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (Date.now() >= heldUntil) {
        setStep((previous) => (previous + 1) % STEPS.length);
      }
    }, KIOSK_STEP_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [heldUntil]);
  return [
    step,
    () => {
      setHeldUntil(Date.now() + KIOSK_HOLD_MS);
    },
  ];
}

function KioskStep({ name, shown, go }: { name: (typeof STEPS)[number]; shown: string | null; go: (route: Route) => void }) {
  switch (name) {
    case "network":
      return <Network go={go} />;
    case "schedule":
      return <ScheduleView stationId={shown} go={go} />;
    case "losses":
      return <LossesView go={go} />;
    case "insights":
      return <InsightsView />;
  }
}

export function Kiosk({ go }: { go: (route: Route) => void }) {
  const [step, hold] = useCycle();
  const directory = useStations();
  // The schedule shows a station that is being heard, so the screen is not empty.
  const shown = directory.stations?.find((one) => one.liveness === "online")?.stationId ?? null;
  const name = STEPS[step % STEPS.length] ?? "network";
  return (
    <div className="kiosk" onPointerDown={hold} onKeyDown={hold} role="presentation">
      <p className="dim kiosk-step" aria-live="polite">
        {name.charAt(0).toUpperCase() + name.slice(1)}, view {step + 1} of {STEPS.length}. Touch to hold it here.
      </p>
      <KioskStep name={name} shown={shown} go={go} />
    </div>
  );
}
