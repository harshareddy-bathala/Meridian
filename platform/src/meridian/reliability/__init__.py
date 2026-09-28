"""SLI computation, SLO evaluation, irrecoverable-loss budget, failure injection.

Built by Stage 20 of docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md:

* :mod:`~meridian.reliability.classification` — what happened to a scheduled
  pass, and the one definition of a miss;
* :mod:`~meridian.reliability.satellite_silence` — D-147's verdict on whether
  the satellite was transmitting.

Both import the standard library only, because the snapshot labeller calls
them and may reach no database (D-143, D-180). This package's ``__init__``
therefore re-exports nothing: importing it must not drag in a module that does.

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
