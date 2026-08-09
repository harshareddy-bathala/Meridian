"""Pydantic request and response models for the MSP endpoints.

One module per message: ``registration.py`` for MSP §4.1's ``register``,
``heartbeat.py`` for §4.2's station-to-platform report, and ``assignment.py``
for the §4.3 work the heartbeat response carries back.

Each owns the reshaping between the wire names the specification fixes and the
domain shapes behind it — which is why the Python identifiers here can spell
their words out and carry their units while the wire keeps ``lat`` and
``client.impl``.
"""
