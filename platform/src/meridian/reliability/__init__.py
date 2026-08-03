"""SLI computation, SLO evaluation, irrecoverable-loss budget, failure injection.

**Phase 3. Stub only.**

One rule governs this module, and it is the reason the module exists:

    **Absence is not a miss.**

A pass counts as missed only when ``meridian.registry`` confirms the station was
listening on the right frequency for the right target — which is what
``Registry.was_listening`` answers. A station that reported no data and was not
listening did not miss anything; it was not working.

Encode that in one place, here, and never duplicate the logic
(docs/ARCHITECTURE.md rule 3). Every reliability figure in the project depends on
this distinction holding, and it erodes the moment a second module reimplements it.

SC-5 requires an injected node failure to be detected within 90 s. The liveness
thresholds in ``meridian.registry`` are already set from that number, so the SLI
defined here aligns with them rather than introducing a second threshold.
"""
