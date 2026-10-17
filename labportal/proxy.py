"""A streaming reverse proxy behind the Lab Hub's sign-in (gate.py): the LAN reaches a service on the NMS (Grafana,
Prometheus, VictoriaMetrics, VictoriaLogs) through it instead of an open socat relay. Responses are passed on as they
come (long queries, log tails). This host's own requests pass without a session, as everywhere the gate is used.

  lab-proxy --bind 192.168.50.231 --port 3001 --upstream http://10.0.0.10:3001

WebSockets are relayed too (a noVNC console, for one), behind the same check: this host, or a valid hub session cookie.
HTTP middleware never sees a WebSocket handshake, so the check is repeated in the WebSocket route.

One user unit per port (monitoring/lab-relay-*.service.example). LAB_HUB_AUTH=off makes it an open relay again."""
import argparse
import asyncio
import http.cookiejar

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse

from . import gate
from .hub import auth as AUTH

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
       "host", "content-length"}


def make(upstream):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate.install(app)
    local = gate.host_addresses()
    ws_upstream = "ws" + upstream[4:] if upstream.startswith("http") else upstream
    u = httpx.URL(upstream)                                      # the forms an upstream's redirect may name it by
    up_bases = sorted({upstream.rstrip("/"), f"{u.scheme}://{u.host}" + (f":{u.port}" if u.port else ""),
                       *([f"{u.scheme}://{u.host}"] if u.port in (None, 80 if u.scheme == "http" else 443) else [])}, key=len, reverse=True)

    @app.websocket("/{path:path}")
    async def ws_proxy(ws: WebSocket, path: str):
        client = ws.client.host if ws.client else ""
        if AUTH.ENABLED and client not in local and not AUTH.verify(ws.cookies.get(AUTH.COOKIE, "")):
            await ws.close(code=4401); return
        url = f"{ws_upstream}/{path}" + (f"?{ws.url.query}" if ws.url.query else "")
        proto = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
        try:
            up = await websockets.connect(url, subprotocols=proto or None, max_size=None, open_timeout=10,
                                          origin=websockets.Origin(upstream) if hasattr(websockets, "Origin") else upstream)
        except Exception:                                        # noqa: BLE001
            await ws.close(code=1011); return
        await ws.accept(subprotocol=up.subprotocol)

        async def down():                                        # browser -> upstream
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect": break
                await up.send(m["bytes"] if m.get("bytes") is not None else m.get("text", ""))

        async def upward():                                      # upstream -> browser
            async for m in up:
                if isinstance(m, bytes): await ws.send_bytes(m)
                else: await ws.send_text(m)

        tasks = [asyncio.create_task(down()), asyncio.create_task(upward())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks: t.cancel()
            await up.close()
            try: await ws.close()
            except Exception: pass                               # noqa: BLE001
    # cookies: the browser's own pass through in the Cookie header; the client must never keep a jar of its own, or
    # an upstream's Set-Cookie (a login session) would be sent on behalf of every later visitor
    no_jar = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
    client = httpx.AsyncClient(base_url=upstream, timeout=httpx.Timeout(15.0, read=None), follow_redirects=False, cookies=no_jar)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
    async def proxy(path: str, request: Request):
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        # the hub's session cookie stays here; the upstream's own cookies (a Grafana login) pass
        if "cookie" in headers:
            # trimmed and re-joined: a cookie that followed the session one kept its leading space (" csrftoken=…"), an
            # illegal header value for httpx — every page 500 for a browser with the hub session first and other cookies
            # on this host (Nautobot's, sent to every port)
            kept = [c.strip() for c in headers["cookie"].split(";") if c.strip() and not c.strip().startswith("labhub_session=")]
            headers["cookie"] = "; ".join(kept)
            if not headers["cookie"].strip(): headers.pop("cookie")
        req = client.build_request(request.method, "/" + path, params=request.query_params, headers=headers, content=await request.body())
        up = await client.send(req, stream=True)
        out = [(k, v) for k, v in up.headers.multi_items() if k.lower() not in HOP]
        # a redirect to the upstream's own address (Django/Horizon builds absolute ones from its Host) is pointed back
        # at this relay, as the browser reached it: the upstream address is not reachable from the LAN
        here = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
        out = [(k, here + v[len(base):] if k.lower() == "location" and (base := next((b for b in up_bases if v == b or v.startswith(b + "/")), None)) else v)
               for k, v in out]

        async def body():
            try:
                async for chunk in up.aiter_raw(): yield chunk
            finally:
                await up.aclose()
        resp = StreamingResponse(body(), status_code=up.status_code)
        for k, v in out: resp.headers.append(k, v)
        return resp
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0"); ap.add_argument("--port", type=int, required=True); ap.add_argument("--upstream", required=True)
    a = ap.parse_args()
    uvicorn.run(make(a.upstream.rstrip("/")), host=a.bind, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
