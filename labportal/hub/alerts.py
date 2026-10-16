"""Firing alerts, for the hub's banner and its lab cards. There is no Alertmanager: the rules fire in Prometheus (metrics) and
vmalert-logs (syslog) and nothing tells anyone — evpn-clab's wan exporters were down for a day with ExporterDown firing
the whole time. The hub is where they are seen now.

Read from both evaluators' /api/v1/alerts (the lab host reaches the NMS directly), cached for 15 s. `info` alerts (a
configuration commit and the like) are left out; `critical` ones lead the banner, `warning` ones follow it.
A failed read is reported, not hidden: the NMS being off means nothing is watching.

Clearing (the banner's Clear button) hides the alerts firing now, for everyone (kept in ~/.local/state/lab-hub/
cleared.json, not in the browser). An alert is the same alert while it keeps firing (its source, name, labels and the
time it started), so one that stops and fires again, or a new one, shows again; a cleared one that is no longer firing is
forgotten. Cleared alerts stay in the list marked `cleared` and out of the counts."""
import json, os, threading, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests

SOURCES = {"prometheus": os.environ.get("LAB_HUB_PROMETHEUS", "http://10.0.0.10:9090"),
           "syslog": os.environ.get("LAB_HUB_VMALERT", "http://10.0.0.10:8880")}
RANK = {"critical": 0, "warning": 1}
CLEARED = Path(os.environ.get("LAB_HUB_CLEARED", Path.home() / ".local" / "state" / "lab-hub" / "cleared.json"))
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


def key(a):
    return "|".join([a["source"], a["name"] or "", a["lab"] or "", json.dumps(a["labels"], sort_keys=True), a.get("since") or ""])


def _cleared():
    try: return json.loads(CLEARED.read_text())
    except Exception: return {}                               # noqa: BLE001


def _write_cleared(c):
    CLEARED.parent.mkdir(parents=True, exist_ok=True); tmp = CLEARED.with_suffix(".tmp"); tmp.write_text(json.dumps(c, indent=1)); tmp.replace(CLEARED)


def _marked(value):
    """The read with each alert marked cleared or not, and counts of what is not cleared."""
    c = _cleared(); alerts = [{**a, "key": key(a), "cleared": key(a) in c} for a in value["alerts"]]
    live = [a for a in alerts if not a["cleared"]]
    return {**value, "alerts": alerts, "counts": {s: sum(1 for a in live if a["severity"] == s) for s in RANK},
            "cleared": len(alerts) - len(live)}


def clear(keys=None, by=None):
    """Clear the alerts named (their `key`), or every one firing now. Returns how many are cleared."""
    now = firing(); c = _cleared()
    for a in now["alerts"]:
        if keys is None or a["key"] in keys: c.setdefault(a["key"], {"at": time.time(), "by": by})
    _write_cleared(c)
    return sum(1 for a in now["alerts"] if a["key"] in c)


def restore():
    """Show every cleared alert again."""
    _write_cleared({})


def firing(max_age=15):
    """{"alerts": [...] critical first, each with `key` and `cleared`, "counts": {critical, warning} of those not cleared,
    "cleared": how many are, "errors": {source: why}, "at": time}."""
    with _lock:
        if _cache["value"] is not None and time.time() - _cache["at"] < max_age: return _marked(_cache["value"])
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
    if not errors:                                            # forget the cleared alerts that stopped firing (not on a failed read)
        c = _cleared(); keep = {k: v for k, v in c.items() if k in {key(a) for a in alerts}}
        if keep != c: _write_cleared(keep)
    return _marked(value)
