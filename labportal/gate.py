"""Sign-in for the labs' own web services (portals, proxies) with the Lab Hub's session: one sign-in at the hub covers
every service on the host — its cookie (labhub_session, HttpOnly, SameSite=Strict) goes to every port of the host, and
these services verify it with the hub's key (hub/auth.py, ~/.config/lab-hub/auth.json).

  install(app, open_paths={"/metrics", "/api/sd"}, open_from=["10.106.0.0/24"])

Let through without a session:
  - requests from this host itself (any of its own addresses): the hub, its MCP server, the labs' tools, CI;
  - `open_paths` from `open_from` networks (e.g. the NMS scraping /metrics and the Prometheus service discovery).
Everything else needs the session: a page is redirected to the hub's /login (which brings the browser back), an API
call gets 401. LAB_HUB_AUTH=off turns it off, as for the hub; LAB_HUB_PORT names the hub's port (8088)."""
import ipaddress
import json
import os
import socket
import subprocess
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from .hub import auth as AUTH

HUB_PORT = int(os.environ.get("LAB_HUB_PORT", "8088"))


def host_addresses():
    """Every address of this host (the source address of its own requests to its services)."""
    addrs = {"127.0.0.1", "::1"}
    try:
        for i in json.loads(subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5).stdout or "[]"):
            addrs |= {a["local"] for a in i.get("addr_info", []) if a.get("local")}
    except Exception:                                            # noqa: BLE001
        addrs.add(socket.gethostbyname(socket.gethostname()))
    return addrs


def install(app, open_paths=(), open_from=()):
    local = host_addresses()
    nets = [ipaddress.ip_network(n) for n in open_from]

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        if not AUTH.ENABLED:
            return await call_next(request)
        client = request.client.host if request.client else ""
        if client in local:
            return await call_next(request)
        if request.url.path in open_paths and client:
            try:
                if any(ipaddress.ip_address(client) in n for n in nets): return await call_next(request)
            except ValueError:
                pass
        if AUTH.verify(request.cookies.get(AUTH.COOKIE, "")):
            return await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path in ("/metrics", "/openapi.json") or request.method != "GET":
            return JSONResponse({"detail": f"sign in at the Lab Hub first (port {HUB_PORT})"}, status_code=401)
        host = request.url.hostname or "localhost"
        return RedirectResponse(f"{request.url.scheme}://{host}:{HUB_PORT}/login?next={quote(str(request.url), safe='')}", status_code=303)

    return app
