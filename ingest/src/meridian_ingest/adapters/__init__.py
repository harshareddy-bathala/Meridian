"""One adapter per source, each answering the same two questions.

An adapter knows a source: what it publishes, under what terms, and which
artefacts to ask for. A normaliser knows an artefact's format. They are
separate because the first touches a network and the second must never be able
to (D-142), and separating them by *type* rather than by convention is what
makes that checkable.

The contract is :mod:`meridian_ingest.adapters.protocol`. Implementations
arrive one per source, and the first of them is fixture-backed and ours: a
recorded fixture from a real archive would be that archive's data committed to
a public repository, which is the redistribution question D-136 leaves open,
and settling it by accident in a commit about test plumbing is not a decision.

Reference: docs/DECISIONS.md D-134, D-136, D-142.
"""
