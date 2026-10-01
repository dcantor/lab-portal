"""Shared services: the NMS VM and what runs on it (Prometheus, VictoriaMetrics, VictoriaLogs, Grafana, Gitea, Nautobot).
Every lab depends on them — monitoring, CI, configuration backups, the source of truth — and when the NMS is off nothing
says so. The hub shows each one's state and can start or shut down the NMS VM.

Declared in ~/.config/lab-hub/services.json (or $LAB_HUB_SERVICES), else the defaults below:
  {"vm": "nms", "libvirt": "qemu:///system", "services": [{"name": "Prometheus", "check": "http://10.0.0.10:9090/-/ready",
   "link": "http://192.168.50.231:9091", "used_for": "metrics and alerts"}, ...]}
`check` is probed from the lab host (on the OOB network); `link` is what a browser opens (the LAN relay)."""
import json, os, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

CONFIG = Path(os.environ.get("LAB_HUB_SERVICES", Path.home() / ".config" / "lab-hub" / "services.json"))
DEFAULT = {"vm": "nms", "libvirt": "qemu:///system", "services": [
    {"name": "Prometheus", "check": "http://10.0.0.10:9090/-/ready", "link": "http://192.168.50.231:9091", "used_for": "metrics, alert rules"},
    {"name": "VictoriaMetrics", "check": "http://10.0.0.10:8428/health", "link": "http://192.168.50.231:8428/vmui", "used_for": "long-term metrics"},
    {"name": "VictoriaLogs", "check": "http://10.0.0.10:9428/health", "link": "http://192.168.50.231:9428/select/vmui", "used_for": "every router's syslog"},
    {"name": "Grafana", "check": "http://10.0.0.10:3001/api/health", "link": "http://192.168.50.231:3001", "used_for": "dashboards, annotations"},
    {"name": "Gitea", "check": "http://10.0.0.10:3000/api/v1/version", "link": "http://192.168.50.231:3000", "used_for": "CI mirror and runner, config backups"},
    {"name": "Nautobot", "check": "http://10.0.0.10:8080/health/", "link": "http://192.168.50.231:8080", "used_for": "source of truth"}]}


def config():
    try:
        return {**DEFAULT, **json.loads(CONFIG.read_text())} if CONFIG.exists() else DEFAULT
    except Exception:                                          # noqa: BLE001 — a broken file falls back to the defaults
        return DEFAULT


def virsh(*args, timeout=60):
    """virsh through `sg libvirt`, as the labs' lab.sh does, so it works from a unit without the group in its session."""
    cmd = "virsh -q -c {} {}".format(config()["libvirt"], " ".join(args))
    return subprocess.run(["sg", "libvirt", "-c", cmd], capture_output=True, text=True, timeout=timeout)


def vm_state():
    try:
        r = virsh("domstate", config()["vm"], timeout=20)
        return r.stdout.strip() or (r.stderr.strip() and "unknown")
    except Exception:                                          # noqa: BLE001
        return "unknown"


def _probe(svc):
    t = time.time()
    try:
        r = requests.get(svc["check"], timeout=4)
        return {**svc, "up": r.status_code < 400, "code": r.status_code, "ms": round(1000 * (time.time() - t))}
    except Exception as e:                                     # noqa: BLE001
        return {**svc, "up": False, "error": e.__class__.__name__}


def status():
    cfg = config()
    with ThreadPoolExecutor(max_workers=8) as ex:
        services = list(ex.map(_probe, cfg["services"]))
    state = vm_state()
    up = sum(s["up"] for s in services)
    overall = "ok" if state == "running" and up == len(services) else ("down" if state != "running" or up == 0 else "degraded")
    return {"vm": cfg["vm"], "vm_state": state, "services": services, "up": up, "total": len(services), "state": overall}


def power(op):
    """Start the NMS VM (`virsh start`) or shut it down cleanly (`virsh shutdown`, ACPI), then follow it to the end."""
    vm = config()["vm"]
    try:
        r = virsh("start" if op["action"] == "up" else "shutdown", vm)
        op["log"] = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        if r.returncode != 0:
            op["status"] = "failed"; op["finished"] = time.time(); return
        want, deadline = ("running" if op["action"] == "up" else "shut off"), time.time() + 300
        while time.time() < deadline and vm_state() != want:
            time.sleep(3)
        if op["action"] == "up":                               # running is not ready: wait for the services to answer
            while time.time() < deadline and status()["up"] < len(config()["services"]):
                time.sleep(5)
        s = status()
        op["log"].append(f"{vm}: {s['vm_state']}, {s['up']}/{s['total']} services answering")
        op["status"] = "done" if s["vm_state"] == want else "failed"
    except Exception as e:                                     # noqa: BLE001
        op["status"] = "failed"; op["log"] = op.get("log", []) + [f"{e.__class__.__name__}: {e}"]
    op["finished"] = time.time()
