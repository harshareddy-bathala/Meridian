"""SLI computation, SLO evaluation, irrecoverable-loss budget, failure injection.

**Phase 3. Not implemented** — this module is its interface and nothing else,
built by Stage 20 of docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md.

One rule governs this module, and it is the reason the module exists:

    **Absence is not a miss.**

A pass counts as missed only when ``meridian.registry`` confirms the station was
listening on the right frequency for the right target — which is what
``Registry.was_listening`` answers. A station that reported no data and was not
listening did not miss anything; it was not working.

The distinction is defined here and nowhere else, so every reliability figure
the project publishes traces back to a single definition of a miss rather than
to whichever module last decided one.

SC-5 requires an injected node failure to be detected within 90 s. The liveness
thresholds in ``meridian.registry`` are already set from that number, so the SLI
defined here aligns with them rather than introducing a second threshold.
"""
