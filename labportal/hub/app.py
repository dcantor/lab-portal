#!/usr/bin/env python3
"""The lab hub: one page in front of every lab — VMs up/down, the portal's health, the last test run, links to the
portal, Nautobot, Gitea and GitHub. Labs are declared in ~/.config/lab-hub/labs.json (or $LAB_HUB_CONFIG):
  [{"name": "srv6-core", "dir": "/home/dcantor/srv6-core", "portal": "http://192.168.50.231:8091", "repo": "https://github.com/dcantor/srv6-core",
    "description": "...", "gitea": "http://192.168.50.231:3000/lab/srv6-core-configs"}, ...]
It reads the labs, and it can **power them**: a whole lab up or down, or any single VM, by running that lab's own
`lab.sh up|down [node...]` — the same command an operator would type, so a shutdown still saves each router's
configuration first. Nothing else about a lab is changed here; provisioning stays in the lab's own portal.
Set LAB_HUB_POWER=off to make the hub read-only again. Run with `lab-hub` (uvicorn, port 8088)."""
import json, os, re, shutil, subprocess, threading, time, uuid, xml.etree.ElementTree as ET
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests

CONFIG = Path(os.environ.get("LAB_HUB_CONFIG", Path.home() / ".config" / "lab-hub" / "labs.json"))
MONITORING = json.loads(os.environ["LAB_HUB_MONITORING"]) if os.environ.get("LAB_HUB_MONITORING") else \
    {"grafana": "http://192.168.50.231:3001", "prometheus": "http://192.168.50.231:9091", "victoriametrics": "http://192.168.50.231:8428", "victorialogs": "http://192.168.50.231:9428"}   # the stack in ../monitoring on the NMS
NAUTOBOT = os.environ.get("NAUTOBOT_PUBLIC_URL", "http://192.168.50.231:8080")
POWER = os.environ.get("LAB_HUB_POWER", "on") != "off"      # the hub may be made read-only again
POWER_TIMEOUT = int(os.environ.get("LAB_HUB_POWER_TIMEOUT", "2400"))
app = FastAPI(title="Lab hub", version="1.0", description="Every lab on this host at a glance: VM state, portal health, last tests, links.")


# ---- the host: CPU, memory, disk -------------------------------------------------------------------------------------
_cpu = {"pct": None, "cores": os.cpu_count() or 1, "prev": None}


def _cpu_sample():
    """Busy percentage from /proc/stat deltas, kept fresh by a thread so a page load never has to wait for a sample."""
    while True:
        try:
            parts = [int(x) for x in Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
            idle, total = parts[3] + parts[4], sum(parts)
            prev = _cpu["prev"]; _cpu["prev"] = (idle, total)
            if prev and total > prev[1]:
                _cpu["pct"] = round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)
        except Exception:                                     # noqa: BLE001 — statistics must never break the page
            pass
        time.sleep(5)


def host_stats():
    """What the host has left: CPU, memory (and swap), and the filesystems the labs live on."""
    out = {"cpu": {"pct": _cpu["pct"], "cores": _cpu["cores"], "load": [round(x, 2) for x in os.getloadavg()]}}
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1); mem[k] = int(v.strip().split()[0]) // 1024        # MiB
        total, avail = mem["MemTotal"], mem.get("MemAvailable", mem["MemFree"])
        out["memory"] = {"total_gib": round(total / 1024, 1), "used_gib": round((total - avail) / 1024, 1),
                         "available_gib": round(avail / 1024, 1), "pct": round(100 * (total - avail) / total, 1),
                         "swap_total_gib": round(mem.get("SwapTotal", 0) / 1024, 1),
                         "swap_used_gib": round((mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) / 1024, 1)}
    except Exception:                                         # noqa: BLE001
        out["memory"] = None
    disks, seen = [], set()
    for path in ["/"] + [l["dir"] for l in labs()]:
        try:
            st = os.stat(path)
            if st.st_dev in seen: continue                    # the labs usually sit on the root filesystem
            seen.add(st.st_dev); u = shutil.disk_usage(path)
            disks.append({"path": path, "total_gib": round(u.total / 2**30, 1), "used_gib": round(u.used / 2**30, 1),
                          "free_gib": round(u.free / 2**30, 1), "pct": round(100 * u.used / u.total, 1)})
        except Exception:                                     # noqa: BLE001
            pass
    out["disks"] = disks
    return out


def labs():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else []


def vm_states(lab_dir):
    """Node -> libvirt state, from `lab.sh status` (first table) so only this lab's VMs are counted."""
    try: out = subprocess.run([str(Path(lab_dir) / "lab.sh"), "status"], capture_output=True, text=True, timeout=60).stdout
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    states = {}
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s+(\S+)\s+(running|shut off|undefined|paused|crashed|in shutdown)\b", line)
        if m and m[1] != "NODE": states[m[1]] = {"role": m[2], "state": m[3]}
    return states


def last_tests(lab_dir):
    latest = Path(lab_dir) / "results" / "latest" / "output.xml"
    if not latest.exists(): return None
    try:
        root = ET.parse(latest).getroot(); tot = root.find("statistics/total/stat"); suites = []
        for su in root.iter("suite"):
            if su.get("source", "").endswith(".robot"):
                st = su.find("status"); n = sum(1 for _ in su.iter("test")); suites.append({"name": su.get("name"), "tests": n, "status": st.get("status")})
        return {"passed": int(tot.get("pass")), "failed": int(tot.get("fail")), "when": latest.resolve().parent.name, "suites": suites}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def portal_health(url):
    try:
        r = requests.get(url + "/api/runs", timeout=3); runs = r.json() if r.ok else []
        active = next((x for x in runs if x.get("status") in ("running", "queued")), None)
        return {"up": r.ok, "runs": len(runs), "active": active and {"id": active["id"], "mode": active["mode"]}, "last": runs[0] and {"mode": runs[0]["mode"], "status": runs[0]["status"], "started": runs[0]["started"]} if runs else None}
    except Exception as e:  # noqa: BLE001
        return {"up": False, "error": e.__class__.__name__}


@app.get("/", include_in_schema=False)
def index(): return FileResponse(str(Path(__file__).resolve().parent / "static" / "index.html"))


@app.get("/api/labs", summary="Every lab with VM states, portal health and the last test run")
def api_labs():
    out = []
    for lab in labs():
        vms = vm_states(lab["dir"]); running = sum(1 for v in vms.values() if isinstance(v, dict) and v.get("state") == "running")
        out.append({**lab, "vms": vms, "running": running, "total": len([v for v in vms.values() if isinstance(v, dict)]), "portal_health": portal_health(lab["portal"]),
                    "tests": last_tests(lab["dir"]), "nautobot": lab.get("nautobot", NAUTOBOT), "power": OPS.get(lab["name"])})
    return {"labs": out, "host": host_stats(), "monitoring": MONITORING, "power_enabled": POWER, "generated": time.time()}


# ---- powering a lab: one operation at a time per lab, run in the background ------------------------------------------
OPS = {}            # lab name -> the current or last operation
_ops_lock = threading.Lock()


class PowerRequest(BaseModel):
    action: str                                  # up | down
    nodes: list[str] = []                        # empty = the whole lab
    confirm: bool = False                        # required for the whole lab: it is every VM of it
    force: bool = False                          # shut down even though the lab's portal has a run in progress


def _run_power(lab, op):
    """`lab.sh up|down [node...]` with its output kept, so the page can show what happened."""
    cmd = [str(Path(lab["dir"]) / "lab.sh"), op["action"], *op["nodes"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=POWER_TIMEOUT)
        op["log"] = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-200:]
        op["status"] = "done" if r.returncode == 0 else "failed"
        op["returncode"] = r.returncode
    except subprocess.TimeoutExpired:
        op["status"] = "failed"; op["log"] = [f"timed out after {POWER_TIMEOUT}s"]
    except Exception as e:                       # noqa: BLE001
        op["status"] = "failed"; op["log"] = [f"{e.__class__.__name__}: {e}"]
    op["finished"] = time.time()


@app.post("/api/labs/{name}/power", summary="Bring a lab up or shut it down — the whole lab, or the nodes you name")
def power(name: str, req: PowerRequest):
    """`{"action": "up"}` starts every VM of the lab, `{"action": "down"}` stops them (each router saves its configuration
    first — it is that lab's own `lab.sh`). `nodes` limits it to those VMs; without `nodes` the whole lab is meant and
    `confirm` must be true. One operation at a time per lab."""
    if not POWER: raise HTTPException(403, "power control is disabled on this hub (LAB_HUB_POWER=off)")
    lab = next((l for l in labs() if l["name"] == name), None)
    if lab is None: raise HTTPException(404, f"no such lab: {name}")
    if req.action not in ("up", "down"): raise HTTPException(422, "action must be up or down")
    known = {n for n, v in vm_states(lab["dir"]).items() if isinstance(v, dict)}
    unknown = [n for n in req.nodes if n not in known]
    if unknown: raise HTTPException(422, f"no such node in {name}: {', '.join(unknown)}")
    if not req.nodes and not req.confirm: raise HTTPException(422, f"this would {req.action} every VM of {name} — send confirm: true")
    if req.action == "down" and not req.force:   # never pull the rug from under a Terraform apply
        active = (portal_health(lab["portal"]) or {}).get("active")
        if active: raise HTTPException(409, f"{name}: a {active['mode']} run is in progress ({active['id']}) — let it finish, or send force: true")
    with _ops_lock:
        cur = OPS.get(name)
        if cur and cur["status"] == "running": raise HTTPException(409, f"{name} is already {cur['action']} ({', '.join(cur['nodes']) or 'the whole lab'})")
        op = OPS[name] = {"id": uuid.uuid4().hex[:8], "lab": name, "action": req.action, "nodes": list(req.nodes),
                          "started": time.time(), "finished": None, "status": "running", "log": [], "returncode": None}
    threading.Thread(target=_run_power, args=(lab, op), name=f"power-{name}", daemon=True).start()
    return op


@app.get("/api/labs/{name}/power", summary="The current or last power operation of a lab")
def power_status(name: str):
    op = OPS.get(name)
    if op is None: raise HTTPException(404, "no power operation for this lab yet")
    return op


@app.on_event("startup")
def _start_sampler():
    threading.Thread(target=_cpu_sample, name="cpu", daemon=True).start()


def main():
    import uvicorn; uvicorn.run(app, host=os.environ.get("LAB_HUB_HOST", "0.0.0.0"), port=int(os.environ.get("LAB_HUB_PORT", "8088")))


if __name__ == "__main__": main()
