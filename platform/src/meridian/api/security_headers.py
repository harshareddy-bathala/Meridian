"""A strict content-security policy and the security headers, on every response (D-208).

The dashboard is served from the platform's own origin and talks to nothing else
(D-081, D-091), so its policy can say so: scripts, styles, fonts and requests from
``'self'`` only, images from ``'self'``, ``data:`` and the one tile server the map
uses, and nothing framed, embedded or submitted anywhere. No ``'unsafe-inline'``:
Vite's build has no inline script or style, and what React and Leaflet style they
set through the DOM, which a policy does not govern.

The same headers go on every response, the API's JSON included. A JSON response
has nothing to execute, and one policy for the whole origin means no path can be
served without it. ``site/_headers`` carries the static site's own copy, for the
same reasons, on another host (D-036).

**No CORS.** The platform sends no ``Access-Control-Allow-*`` header, so a browser
on another origin cannot read its responses; ``tests/msp_conformance/`` pins that.
A station is not a browser and needs none.

Pure ASGI, like the other middleware here: it adds headers to the response start
message and touches nothing else.

Reference: docs/DECISIONS.md D-036, D-081, D-091, D-092, D-208.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["CONTENT_SECURITY_POLICY", "SECURITY_HEADERS", "SecurityHeadersMiddleware"]

MAP_TILE_ORIGIN = "https://tile.openstreetmap.org"
"""The only other origin the page loads from: the map's optional tiles (D-092).

``dashboard/src/StationMap.tsx`` names the same host, and a test holds the two
equal. A build with another ``VITE_MAP_TILE_URL`` must change this too, or the
tiles are refused and the map shows its own graticule, which it is built to do.
"""

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        f"img-src 'self' data: {MAP_TILE_ORIGIN}",
        "font-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)
"""What the page may load, and from where.

``data:`` images are Leaflet's own control icons, inlined in its stylesheet, and
the page's empty favicon. ``object-src`` is covered by ``default-src`` and named
anyway, because audits look for it by name.
"""

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", CONTENT_SECURITY_POLICY),
    ("x-content-type-options", "nosniff"),
    # For browsers that predate `frame-ancestors`; where both are read they agree.
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
    (
        "permissions-policy",
        "geolocation=(), camera=(), microphone=(), browsing-topics=(), payment=(),"
        " usb=(), serial=(), bluetooth=(), midi=(), display-capture=()",
    ),
    ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"),
    # Ignored by browsers over plain HTTP, so harmless on a laptop; over the
    # tunnel, where TLS ends at the edge, it keeps a visitor on HTTPS.
    ("strict-transport-security", "max-age=31536000"),
)
"""Every header added, lower-cased as ASGI carries them."""

_ENCODED = [
    (name.encode("latin-1"), value.encode("latin-1"))
    for name, value in SECURITY_HEADERS
]
_NAMES = {name for name, _ in _ENCODED}


class SecurityHeadersMiddleware:
    """Adds :data:`SECURITY_HEADERS` to every HTTP response, replacing any copy."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one request, adding the headers as the response starts."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in _NAMES
                ]
                message["headers"] = [*kept, *_ENCODED]
            await send(message)

        await self._app(scope, receive, send_with_headers)
