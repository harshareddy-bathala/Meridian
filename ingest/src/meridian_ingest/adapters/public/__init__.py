"""Stage 31's sources: public environmental and space-weather products.

One module per source class, each a real provider's documented format read by
an adapter and a normaliser of our own. The fixtures beside them are synthetic
— written by us in that format, not recorded from the provider — so nothing in
this repository redistributes anyone's data, and D-136 stays open (D-142).

Every source is registered **disabled by default**: with no settings file,
``meridian-ingest fetch`` still reaches only the reference archive. A real
source is fetched from when an operator names it or enables it, after its
``ATTRIBUTION.md`` entry — which lands before any retrieval (D-134).

Reference: docs/DECISIONS.md D-131 to D-134, D-142, D-220.
"""
