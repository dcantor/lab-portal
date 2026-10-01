"""The lab host's last 24 hours: CPU, memory and disk once a minute, and the power operations that happened, so the hub can
show what a power-up did to the host. Kept by the hub itself — no Prometheus needed, so it works with the NMS off — and
saved to ~/.local/state/lab-hub/host-history.json every few minutes so a restart keeps it.

On start, the part of the last 24 hours the hub has no samples for is backfilled from VictoriaMetrics on the NMS, which
scrapes the lab host's node-exporter (job "lab-host") — skipped quietly when the NMS is off."""
import json, os, shutil, threading, time
import requests
from collections import deque
from pathlib import Path

FILE = Path(os.environ.get("LAB_HUB_HISTORY", Path.home() / ".local" / "state" / "lab-hub" / "host-history.json"))
KEEP_S = 24 * 3600
_points = deque(maxlen=KEEP_S // 60 + 10)       # (t, cpu %, memory used %, disk used %)
_events = deque(maxlen=500)                      # {t, lab, action, why, status}
_lock = threading.Lock()
VM = os.environ.get("LAB_HUB_VICTORIAMETRICS", "http://10.0.0.10:8428")
QUERIES = {   # the same three figures the hub samples, from node-exporter
    "cpu": '100 * (1 - avg(rate(node_cpu_seconds_total{job="lab-host",mode="idle"}[2m])))',
    "memory": '100 * (1 - node_memory_MemAvailable_bytes{job="lab-host"} / node_memory_MemTotal_bytes{job="lab-host"})',
    "disk": '100 * (1 - node_filesystem_avail_bytes{job="lab-host",mountpoint="/"} / node_filesystem_size_bytes{job="lab-host",mountpoint="/"})'}
_source = {"backfilled_until": None}


def backfill():
    """Fill the hours before the first local sample from VictoriaMetrics, one point a minute."""
    with _lock:
        first = _points[0][0] if _points else time.time()
    start = time.time() - KEEP_S
    if first - start < 600: return
    try:
        series = {}
        for name, q in QUERIES.items():
            r = requests.get(f"{VM}/api/v1/query_range", params={"query": q, "start": int(start), "end": int(first) - 30, "step": "60s"}, timeout=10)
            res = r.json()["data"]["result"]
            series[name] = {int(float(t)): round(float(v), 1) for t, v in (res[0]["values"] if res else [])}
        ts = sorted(set().union(*(set(v) for v in series.values())))
        old = [(t, series["cpu"].get(t), series["memory"].get(t), series["disk"].get(t)) for t in ts]
        if old:
            with _lock:
                keep = list(_points); _points.clear(); _points.extend(old + keep)
            _source["backfilled_until"] = old[-1][0]
    except Exception:                                          # noqa: BLE001 — the NMS off: the graph just starts later
        pass


def _load():
    try:
        d = json.loads(FILE.read_text())
        cut = time.time() - KEEP_S
        _points.extend(tuple(p) for p in d.get("points", []) if p[0] >= cut)
        _events.extend(e for e in d.get("events", []) if e["t"] >= cut)
    except Exception:                                          # noqa: BLE001 — no history yet, or a broken file: start afresh
        pass


def _save():
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = {"points": list(_points), "events": list(_events)}
    tmp = FILE.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); tmp.replace(FILE)


def _mem_pct():
    kv = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1); kv[k] = int(v.split()[0])
    total, avail = kv["MemTotal"], kv.get("MemAvailable", kv["MemFree"])
    return round(100 * (total - avail) / total, 1)


def event(lab, action, why="", status="started"):
    """A power operation on a lab (or the NMS): drawn as a marker on the graphs."""
    with _lock:
        _events.append({"t": time.time(), "lab": lab, "action": action, "why": why, "status": status})


def loop(cpu_pct, disk_path="/"):
    """Sample once a minute. `cpu_pct()` returns the hub's current CPU busy figure (it already samples /proc/stat)."""
    _load(); backfill(); n = 0
    while True:
        try:
            u = shutil.disk_usage(disk_path)
            with _lock:
                _points.append((round(time.time()), cpu_pct(), _mem_pct(), round(100 * u.used / u.total, 1)))
            n += 1
            if n % 5 == 0: _save()
        except Exception:                                      # noqa: BLE001 — a missed sample is a gap, never a crash
            pass
        time.sleep(60 - time.time() % 60)


def series(hours=24, bucket_s=300):
    """Points averaged into buckets (5 minutes: 288 points a day — enough for a sparkline), the peak of each metric, and
    the power events in the window."""
    cut = time.time() - hours * 3600
    with _lock:
        pts = [p for p in _points if p[0] >= cut]; evs = [e for e in _events if e["t"] >= cut]
    buckets = {}
    for t, *vals in pts:
        buckets.setdefault(int(t // bucket_s), []).append(vals)
    out = []
    for b in sorted(buckets):
        vs = [v for v in buckets[b] if None not in v] or buckets[b]
        out.append([b * bucket_s] + [round(sum((v[i] or 0) for v in vs) / len(vs), 1) for i in range(3)])
    peak = lambda i: max(((p[0], p[i + 1]) for p in pts if p[i + 1] is not None), key=lambda x: x[1], default=(None, None))
    return {"from": cut, "to": time.time(), "bucket_s": bucket_s, "points": out, "samples": len(pts), "events": evs,
            "backfilled_until": _source["backfilled_until"],
            "peak": {"cpu": peak(0), "memory": peak(1), "disk": peak(2)}}
