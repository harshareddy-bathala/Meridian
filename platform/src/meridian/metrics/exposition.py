"""One scrape, assembled from a process's counters and its scrape-time collectors.

A process's counters live in one of two places. Run as a single process, they are
in ``prometheus_client``'s default registry. Run under several workers — the
shape D-051 expects on the Pi — each worker holds its own, and a scrape answered
by whichever worker the request lands on would report a different count every
time. ``prometheus_client``'s multiprocess mode fixes that by writing values to
files in ``PROMETHEUS_MULTIPROC_DIR`` and summing them at scrape time, and it is
switched on by that variable being set in the environment the process starts
with.

Collectors that read the database at scrape time are added on top, once per
scrape. They are not registered on the default registry: an application built
twice in one test session would register the same names twice and fail.

Reference: docs/DECISIONS.md D-109.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence

from prometheus_client import REGISTRY, CollectorRegistry, generate_latest
from prometheus_client.metrics_core import Metric
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_client.registry import Collector

__all__ = [
    "MULTIPROCESS_DIRECTORY_VARIABLE",
    "ScrapeSource",
    "build_scrape_source",
    "exposition",
    "is_multiprocess",
]

MULTIPROCESS_DIRECTORY_VARIABLE = "PROMETHEUS_MULTIPROC_DIR"
"""The variable ``prometheus_client`` itself reads to enter multiprocess mode.

Named by the library, not chosen here; this module only needs to agree with it.
"""


class ScrapeSource(Collector):
    """Everything one scrape reports: a base registry, then extra collectors.

    ``generate_latest`` asks only for something with ``collect()``, so composing
    here keeps the extra collectors off every shared registry.
    """

    def __init__(self, base: Collector, extra: Sequence[Collector]) -> None:
        """Compose a scrape.

        Args:
            base: The registry holding this process's own counters.
            extra: Collectors whose values are computed when the scrape arrives.
        """
        self._base = base
        self._extra = tuple(extra)

    def collect(self) -> Iterator[Metric]:
        """Yield the base registry's metrics, then each extra collector's."""
        yield from self._base.collect()
        for collector in self._extra:
            yield from collector.collect()


def is_multiprocess(environ: Mapping[str, str]) -> bool:
    """Whether ``prometheus_client`` is running in multiprocess mode.

    Args:
        environ: The process environment, passed in so the rule is testable.

    Returns:
        True when ``PROMETHEUS_MULTIPROC_DIR`` is set to a non-empty value.
    """
    return bool(environ.get(MULTIPROCESS_DIRECTORY_VARIABLE))


def build_scrape_source(
    extra: Sequence[Collector] = (), *, environ: Mapping[str, str] | None = None
) -> ScrapeSource:
    """Build what a metrics endpoint serves.

    Args:
        extra: Collectors computed at scrape time, such as database reads.
        environ: The environment to decide the mode from; ``os.environ`` when
            omitted.

    Returns:
        A source summing every worker's files in multiprocess mode, or reading
        the default registry otherwise, followed by ``extra``.

    Note:
        Multiprocess mode reports no ``process_`` or ``python_`` collectors.
        ``prometheus_client`` cannot attribute them to one worker, so it omits
        them rather than reporting whichever worker answered.
    """
    environment = os.environ if environ is None else environ
    if not is_multiprocess(environment):
        return ScrapeSource(REGISTRY, extra)
    merged = CollectorRegistry()
    # The library annotates everything but this constructor. The ignore names
    # the one error code, so any other problem on the line is still reported.
    MultiProcessCollector(merged)  # type: ignore[no-untyped-call]
    return ScrapeSource(merged, extra)


def exposition(source: ScrapeSource) -> bytes:
    """Render ``source`` in Prometheus's text format."""
    return generate_latest(source)
