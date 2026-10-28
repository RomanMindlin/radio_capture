"""Serving the app under a path behind a reverse proxy.

nginx on pic.ygto.com serves the app at /radio: it drops the prefix from the
path and sends it in the X-Forwarded-Prefix header. ForwardedPrefixMiddleware
makes it the request's root_path, the way `uvicorn --root-path` would, so the
URLs built from root_path (templates, redirects, url_for) carry it. Requests
without the header, straight to port 8000, stay at /.
"""

from fastapi import Request
from fastapi.responses import RedirectResponse


class ForwardedPrefixMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            prefix = dict(scope["headers"]).get(b"x-forwarded-prefix", b"").decode("latin-1").rstrip("/")
            if prefix.startswith("/"):
                # Like uvicorn: path includes root_path; Starlette strips it for routing
                scope = {
                    **scope,
                    "root_path": prefix,
                    "path": prefix + scope["path"],
                    "raw_path": prefix.encode("latin-1") + scope.get("raw_path", scope["path"].encode()),
                }
        await self.app(scope, receive, send)


def root(request: Request) -> str:
    """The path the app is served at: "/radio" behind nginx, "" directly."""
    return request.scope.get("root_path", "")


def redirect(request: Request, path: str) -> RedirectResponse:
    """A redirect to one of the app's own pages, e.g. redirect(request, "/login")."""
    return RedirectResponse(root(request) + path)
