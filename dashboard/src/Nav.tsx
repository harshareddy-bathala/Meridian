import type { Route, View } from "./route";

const LINKS: { view: View; label: string }[] = [
  { view: "live", label: "Live" },
  { view: "stations", label: "Stations" },
  { view: "schedule", label: "Schedule" },
  { view: "results", label: "Results" },
  { view: "insights", label: "Insights" },
  { view: "kiosk", label: "Kiosk" },
];

export function Nav({ route }: { route: Route }) {
  return (
    <nav aria-label="Views">
      <ul className="nav">
        {LINKS.map((one) => (
          <li key={one.view}>
            <a href={`#/${one.view}`} aria-current={route.view === one.view ? "page" : undefined}>
              {one.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
