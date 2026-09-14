"""Check a deployed Meridian from outside it. Stdlib only.

D-041 recorded three Cloudflare remedies and D-042 found, a day later, that none
had been applied. D-088's answer is that the deliverable is the check, not the
rule — so this script exists to be run against the real hostname and pasted into
a decision entry.

Five claims, one per check:

  metrics refused    /metrics carries process internals and must answer 404
                     without a bearer token (D-087), in the same words as an
                     unrouted path.
  dashboard served   the dashboard's page is served at the site root (D-081).
  virtual station    at least one simulated station is listed and online — the
                     Phase 1 exit criterion, from wherever this is run.
  lists labelled     every item of every public list carries `simulated` where
                     provenance applies (CLAUDE.md rule 5) and no key matching
                     the do-not-expose list.
  rate limited       a burst against the public API is refused at the Cloudflare
                     edge (D-088). Only with --burst: firing one unasked would be
                     a small self-inflicted denial of service.

**A check whose subject does not exist yet reports SKIP and does not fail the
run.** Exit status is 1 only when a check that *could* run found a real problem.

Run:  python deploy/tools/verify_public_surface.py https://dash.meridian.org.in
Add ``--burst 300`` once the edge rule is applied.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TIMEOUT_S = 10

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# What an unrouted path answers (D-090), and therefore what a refused /metrics
# scrape must answer too. Written out rather than imported: this script is run
# against a *deployed* platform, possibly a different version, and importing the
# expectation from the code under test would make the two agree by construction.
NO_SUCH_ENDPOINT = {"error": "not_found", "message": "No such endpoint."}

# Fields that must never appear in a public response, whatever endpoint serves
# it. The roadmap's do-not-expose list, as substrings of a JSON key.
FORBIDDEN_KEY_PARTS = ("token", "invite", "seed", "registration_key", "health")


def fetch(url: str) -> tuple[int, str]:
    """GET ``url``, returning the status and body even when the status is 4xx.

    Args:
        url: An absolute URL.

    Returns:
        The HTTP status and the decoded body. A 404 is an answer here, not an
        error — two of the three checks are *expecting* one.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "meridian-verify"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode("utf-8", "replace")


def check_metrics_is_refused(base_url: str) -> tuple[str, str]:
    """`/metrics` must answer 404, in the same words as any unrouted path."""
    status, body = fetch(f"{base_url}/metrics")

    if status != 404:
        return FAIL, f"/metrics answered {status}, expected 404 (D-087)"

    if "process_" in body or "python_" in body:
        return FAIL, "/metrics answered 404 but the body carried a scrape"

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return FAIL, f"/metrics answered 404 with a non-JSON body: {body[:80]!r}"

    if parsed != NO_SUCH_ENDPOINT:
        return FAIL, (
            "/metrics answered 404 but not in the shape an unrouted path uses, "
            "so its existence is still detectable: "
            f"{parsed!r}"
        )

    return PASS, "refused, and indistinguishable from a path that is not there"


# Every public list, and whether its items carry provenance. The satellite
# catalogue does not: it holds real spacecraft, never simulated ones.
LIST_ENDPOINTS = (
    ("/api/v1/stations", True),
    ("/api/v1/passes", True),
    ("/api/v1/assignments", True),
    ("/api/v1/observations", True),
    ("/api/v1/simulator-runs", True),
    ("/api/v1/satellites", False),
)


def fetch_json(url: str) -> tuple[int, object]:
    """GET ``url`` and decode it, or ``None`` for a body that is not JSON."""
    status, body = fetch(url)
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, None


def judge_list(path: str, parsed: object, *, labelled: bool) -> tuple[str, str]:
    """Whether one decoded list response is safe to publish."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        return FAIL, f"{path} has no `items` list to check"
    for index, item in enumerate(parsed["items"]):
        if not isinstance(item, dict):
            return FAIL, f"{path} item {index} is not an object"
        if labelled and "simulated" not in item:
            return FAIL, f"{path} item {index} carries no `simulated` (rule 5)"
        leaked = sorted(
            key
            for key in item
            if any(part in key.lower() for part in FORBIDDEN_KEY_PARTS)
        )
        if leaked:
            return FAIL, f"{path} item {index} exposes {', '.join(leaked)}"
    return PASS, f"{len(parsed['items'])} item(s)"


def check_every_list_is_labelled(base_url: str) -> tuple[str, str]:
    """Every public list's first page is labelled and carries no secret."""
    counted = []
    for path, labelled in LIST_ENDPOINTS:
        status, parsed = fetch_json(f"{base_url}{path}")
        if status != 200:
            return FAIL, f"{path} answered {status}, expected 200"
        outcome, detail = judge_list(path, parsed, labelled=labelled)
        if outcome != PASS:
            return outcome, detail
        counted.append(f"{path.rsplit('/', 1)[-1]} {detail.split()[0]}")
    return PASS, "; ".join(counted)


def judge_virtual_station(parsed: object) -> tuple[str, str]:
    """Whether a decoded station list shows a virtual station that is online."""
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return FAIL, "/api/v1/stations has no `items` list"
    virtual = [i for i in items if isinstance(i, dict) and i.get("simulated") is True]
    online = [i for i in virtual if i.get("liveness") == "online"]
    if not virtual:
        return FAIL, "no simulated station is listed"
    if not online:
        return FAIL, f"{len(virtual)} simulated station(s) listed, none online"
    return PASS, f"{online[0].get('station_id')} is listed, simulated and online"


def check_a_virtual_station_is_visible(base_url: str) -> tuple[str, str]:
    """The Phase 1 exit criterion, as seen from wherever this runs."""
    status, parsed = fetch_json(f"{base_url}/api/v1/stations")
    if status != 200:
        return FAIL, f"/api/v1/stations answered {status}, expected 200"
    return judge_virtual_station(parsed)


def check_dashboard_is_served(base_url: str) -> tuple[str, str]:
    """The site root serves the dashboard's page, not an API error."""
    status, body = fetch(f"{base_url}/")
    if status != 200 or '<div id="root">' not in body:
        return FAIL, f"/ answered {status} without the dashboard's root element"
    return PASS, "the dashboard page is served at /"


def check_rate_limit_is_applied(base_url: str, burst: int) -> tuple[str, str]:
    """A burst against the public API is eventually refused with 429 (D-088)."""
    if burst <= 0:
        return SKIP, "not attempted; rerun with --burst N once the edge rule is on"
    for sent in range(1, burst + 1):
        status, _ = fetch(f"{base_url}/api/v1/stations?limit=1")
        if status == 429:
            return PASS, f"refused with 429 after {sent} request(s)"
    return FAIL, f"{burst} requests in a row, none refused (D-088)"


def main(argv: list[str]) -> int:
    """Run every check against the base URL given on the command line."""
    if len(argv) not in {2, 4} or (len(argv) == 4 and argv[2] != "--burst"):
        print(f"usage: {argv[0]} https://dash.example.org [--burst N]", file=sys.stderr)
        return 2

    base_url = argv[1].rstrip("/")
    burst = int(argv[3]) if len(argv) == 4 else 0
    print(f"verifying {base_url}\n")

    checks = (
        ("metrics refused", lambda: check_metrics_is_refused(base_url)),
        ("dashboard served", lambda: check_dashboard_is_served(base_url)),
        ("virtual station", lambda: check_a_virtual_station_is_visible(base_url)),
        ("lists labelled", lambda: check_every_list_is_labelled(base_url)),
        ("rate limited", lambda: check_rate_limit_is_applied(base_url, burst)),
    )

    failed = 0
    for name, check in checks:
        outcome, detail = check()
        failed += outcome == FAIL
        print(f"{name:<18} {outcome}  {detail}")

    print()
    if failed:
        print(f"{failed} check(s) failed", file=sys.stderr)
        return 1

    print("every check that can run passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
