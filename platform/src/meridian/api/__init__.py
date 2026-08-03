"""Two surfaces: the public read API, and the MSP endpoints.

**Thin — validation and serialisation only, no business logic**
(docs/ARCHITECTURE.md). Anything that decides something belongs in the module that
owns the decision.

``meridian.api.msp`` binds the four endpoints of MSP §8. Every error response is
exactly ``{"error": ..., "message": ...}`` — two flat string fields, no nesting, no
arrays, no optional members, because a microcontroller client extracts both with a
substring scan and never needs a JSON tree walker. Tests assert the *absence* of
other keys, not just the presence of these two.

``meridian.api.public`` serves the dashboard and anyone else. Every response
carries ``simulated``; docs/ARCHITECTURE.md rule 4 makes that a requirement of the
protocol rather than a platform convention, because the integrity of every
reported figure depends on simulated and measured data never being aggregated.
"""
