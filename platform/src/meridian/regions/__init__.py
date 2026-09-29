"""Regional monitoring: what public products say about a place, and what we received.

Module 19 of the architecture, Stage 32 of the roadmap. An **area of
interest** is a place and a label, registered by an operator (D-137, D-227).
For each one a **regional report** states:

* **series** — what the ingested products say about the area over time,
  computed from published values in a snapshot and never from a rendered tile
  or a live source (D-133, D-229);
* **change** — each series against a baseline period, with a seeded bootstrap
  interval, and an **alert** where the whole interval lies beyond a threshold
  (D-231), recorded behind a delivery interface that Stage 29 will give
  channels (D-232);
* **coverage** — which of our own decoded receptions imaged the area, from the
  ground beneath each pass frozen at export (D-230);
* **cross-checks** — our receptions against the public layers, as a check on
  the ingest and on the receiving chain (D-233);
* **imagery** — tiles that may be shown behind the area, always labelled as
  imagery, never read for a value.

Everything but registration and recorded alerts is a pure function of a raw
snapshot, a configuration and a seed, published as a content-addressed
directory (rule 8). Nothing here is on the scheduling or reception path.

**Not the platform's own monitoring.** Prometheus and Grafana watch Meridian;
this watches places. The two share no name: nothing here imports
``meridian.metrics``, and no metric or alert rule is named for a region.

Reference: docs/DECISIONS.md D-137, D-227 to D-233.
"""
