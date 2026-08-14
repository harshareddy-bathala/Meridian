"""Check a deployed Meridian from outside it. Stdlib only.

D-041 recorded three Cloudflare remedies and D-042 found, a day later, that none
had been applied. D-088's answer is that the deliverable is the check, not the
rule — so this script exists to be run against the real hostname and pasted into
a decision entry.

Three claims, one per check:

  metrics refused   /metrics carries process internals and must answer 404
                    without a bearer token (D-087). The 404 is byte-identical to
                    an unrouted path, so this also confirms the two agree.
  simulated present every item of every list response carries `simulated`, so a
                    reader can never mistake a virtual station for a measured
                    one (CLAUDE.md rule 5)
  rate limited      a burst against the public API is refused at the Cloudflare
                    edge (D-088)

**A check whose subject does not exist yet reports SKIP and does not fail the
run.** Two of the three are in that state during Stage 11: no public endpoint
serves items, and the edge rule is applied in a console rather than in this
repository. Reporting them as skipped keeps the script honest and keeps it
runnable from its first commit — a script that could only be written once
everything was finished is the script that never gets written.

Exit status is 1 only when a check that *could* run found a real problem.

Run:  python deploy/tools/verify_public_surface.py https://dash.meridian.org.in
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


def check_no_endpoint_leaks_a_secret(base_url: str) -> tuple[str, str]:
    """Every served item carries `simulated` and no forbidden key.

    Skipped while no public endpoint exists. The station list is checked first
    when there is one, because it is the response the dashboard's map is drawn
    from and the one that would carry a coordinate.
    """
    status, body = fetch(f"{base_url}/api/v1/stations")

    if status == 404:
        return SKIP, "no public endpoint serves items yet (Stage 11 parts 3-9)"

    if status != 200:
        return FAIL, f"/api/v1/stations answered {status}, expected 200"

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return FAIL, "/api/v1/stations did not answer JSON"

    return _judge_station_list(parsed)


def _judge_station_list(parsed: object) -> tuple[str, str]:
    """Whether a decoded station list is safe to publish."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        return FAIL, "/api/v1/stations has no `items` list to check"

    items = parsed["items"]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return FAIL, f"item {index} is not an object"
        if "simulated" not in item:
            return FAIL, f"item {index} carries no `simulated` (CLAUDE.md rule 5)"
        leaked = [
            key
            for key in item
            if any(part in key.lower() for part in FORBIDDEN_KEY_PARTS)
        ]
        if leaked:
            return FAIL, f"item {index} exposes {', '.join(sorted(leaked))}"

    return PASS, f"{len(items)} item(s), each labelled and carrying no secret"


def check_rate_limit_is_applied(base_url: str) -> tuple[str, str]:
    """A burst against the public API is refused at the edge.

    Skipped unconditionally for now. The rule is applied in the Cloudflare
    dashboard, so there is nothing in this repository to check it against — and
    firing a burst at a hostname that has no rule yet would only be a small
    self-inflicted denial of service, which is a strange thing for a
    verification script to do.
    """
    return SKIP, f"edge rate limit not applied yet for {base_url} (D-088)"


def main() -> int:
    """Run every check against the base URL given on the command line."""
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} https://dash.example.org", file=sys.stderr)
        return 2

    base_url = sys.argv[1].rstrip("/")
    print(f"verifying {base_url}\n")

    checks = (
        ("metrics refused", check_metrics_is_refused),
        ("simulated on every item", check_no_endpoint_leaks_a_secret),
        ("rate limit applied", check_rate_limit_is_applied),
    )

    failed = 0
    for name, check in checks:
        outcome, detail = check(base_url)
        failed += outcome == FAIL
        print(f"{name:<26} {outcome}  {detail}")

    print()
    if failed:
        print(f"{failed} check(s) failed", file=sys.stderr)
        return 1

    print("every check that can run passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
