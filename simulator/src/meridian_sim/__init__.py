"""Virtual stations speaking real MSP over the real network stack.

**Not a mock.** It is a client implementation — built on ``meridian_client``, the
same transport, the same held-work record, the same upload queue and the same
loop the reference station runs — with a decision model in the one place a real
station puts a radio. A mock would prove the platform works against a mock.

Seven modules, and the split is between what can be checked without
infrastructure and what cannot. ``config`` derives a station's seed and profile
from a master seed and an index, ``outcomes`` decides what a pass produced from
that seed and the geometry, and ``executor`` places those numbers on the
assignment's timeline: all three are pure, so the determinism everything else
rests on is checkable before anything speaks MSP. ``virtual_station`` brings one
station up, ``faults`` says what will go wrong and when, ``supervisor`` runs a
fleet on one thread, and ``station`` is the command line.

Deterministic from a seed, and the claim is bounded: with the element sets and
the clock pinned, two runs at one seed produce byte-identical observation
bodies. Two runs at different moments cannot, because a pass is where the sky
puts it — so what is asserted against a live platform is that the station made
the same decisions, not that it made them about the same instants
(docs/DECISIONS.md D-077).

Simulated stations register with ``simulated: true`` plus ``simulator_run_id``
and ``seed``, and the platform propagates that flag to every derived record.
Simulated results are never aggregated with measured ones in a reported figure —
and, because outcomes here are drawn from elevation and elevation is the
prediction model's strongest feature, they are also excluded from every training
and evaluation set (D-078). A model that learned this package would score well
for a reason that means nothing.

Geometry is real: the simulator propagates real catalogue element sets, so only
its *outcomes* are synthetic. A simulator with fabricated orbits would test the
platform against passes that do not exist.
"""

__version__ = "0.1.0"
