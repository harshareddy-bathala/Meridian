"""External archive ingest — the one stage whose input we do not control.

Retrieves artefacts from published archives, holds them unchanged in a raw
store, and normalises them into the archive tables. Meridian schedules,
receives, decodes, monitors and reports without any of it: this distribution is
enrichment, and the independence test is what decides its shape.

**The dependency points one way.** This package imports ``meridian``; the
platform never imports this one, and nothing here is installed into the runtime
image (docs/DECISIONS.md D-138). Its command is ``meridian-ingest`` rather than
a ``meridian`` subcommand, because ``meridian.cli`` imports every subcommand
module eagerly and a subcommand would make the platform import the archive
layer at start-up.

**Archive receptions are never pooled with our own.** They land in their own
tables, with their own outcome vocabulary, because ``no_signal`` asserts that a
station was verifiably listening and we hold no heartbeat for someone else's
station (D-139). Rule 7 is only meaningful where that evidence exists.

**Retrieval and normalisation are separate events**, and only the first touches
a network. Normalisation takes bytes and their manifest, read from the raw
store — no client, no adapter, no URL, no clock — which is what makes the
stage's completion gate provable rather than asserted: a snapshot is downloaded
once, then normalised repeatedly with nothing reachable (D-141, D-142).

**What the data may be used for is narrow**: training and evaluation input under
Stage 16's weighting, fitting the simulator's outcome distributions, and
cross-checking a satellite our own station heard nothing from. Never a runtime
input to scheduling or reception, and never a published comparison against
another network — D-053 rejects that, because it imports the other network's
selection bias along with its totals.

The modules arrive over Stage 14; this package holds only its own description
until they do.
"""

__version__ = "0.1.0"
