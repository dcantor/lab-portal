"""A streaming reverse proxy behind the Lab Hub's sign-in (gate.py): the LAN reaches a service on the NMS (Grafana,
Prometheus, VictoriaMetrics, VictoriaLogs) through it instead of an open socat relay. Responses are passed on as they
come (long queries, log tails). This host's own requests pass without a session, as everywhere the gate is used.

  lab-proxy --bind 192.168.50.231 --port 3001 --upstream http://10.0.0.10:3001

One user unit per port (monitoring/lab-relay-*.service.example). LAB_HUB_AUTH=off makes it an open relay again."""
import argparse

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from . import gate

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
       "host", "content-length"}


def make(upstream):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate.install(app)
    client = httpx.AsyncClient(base_url=upstream, timeout=httpx.Timeout(15.0, read=None), follow_redirects=False)

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
