"""Firing alerts, for the hub's banner and its lab cards. There is no Alertmanager: the rules fire in Prometheus (metrics) and
vmalert-logs (syslog) and nothing tells anyone — evpn-clab's wan exporters were down for a day with ExporterDown firing
the whole time. The hub is where they are seen now.

Read from both evaluators' /api/v1/alerts (the lab host reaches the NMS directly), cached for 15 s. `info` alerts (a
configuration commit and the like) are left out; `critical` ones lead the banner, `warning` ones follow it.
A failed read is reported, not hidden: the NMS being off means nothing is watching."""
import os, threading, time
from concurrent.futures import ThreadPoolExecutor
import requests

SOURCES = {"prometheus": os.environ.get("LAB_HUB_PROMETHEUS", "http://10.0.0.10:9090"),
           "syslog": os.environ.get("LAB_HUB_VMALERT", "http://10.0.0.10:8880")}
RANK = {"critical": 0, "warning": 1}
_cache = {"at": 0.0, "value": None}; _lock = threading.Lock()


def _read(source, base):
    r = requests.get(f"{base}/api/v1/alerts", timeout=5); r.raise_for_status()
    out = []
    for a in r.json().get("data", {}).get("alerts", []):
        lb = a.get("labels", {}); sev = lb.get("severity", "warning")
        if a.get("state") != "firing" or sev not in RANK: continue
        detail = {k: v for k, v in lb.items() if k not in ("alertname", "severity", "lab", "job", "instance", "evaluator", "source")}
        out.append({"name": lb.get("alertname") or a.get("name"), "severity": sev, "lab": lb.get("lab"), "source": source,
                    "summary": (a.get("annotations") or {}).get("summary", ""), "since": a.get("activeAt"), "labels": detail})
    return out


def firing(max_age=15):
    """{"alerts": [...] critical first, "counts": {critical, warning}, "errors": {source: why}, "at": time}."""
    with _lock:
        if _cache["value"] is not None and time.time() - _cache["at"] < max_age: return _cache["value"]
    alerts, errors = [], {}
    with ThreadPoolExecutor(len(SOURCES)) as ex:
        futs = {s: ex.submit(_read, s, b) for s, b in SOURCES.items()}
        for s, f in futs.items():
            try: alerts += f.result()
            except Exception as e:                            # noqa: BLE001 — the banner says which source could not be read
                errors[s] = f"{e.__class__.__name__}: {str(e)[:160]}"
    alerts.sort(key=lambda a: (RANK[a["severity"]], a["lab"] or "", a["name"] or "", a.get("since") or ""))
    value = {"alerts": alerts, "counts": {s: sum(1 for a in alerts if a["severity"] == s) for s in RANK}, "errors": errors, "at": time.time()}
    with _lock: _cache.update(at=time.time(), value=value)
    return value
