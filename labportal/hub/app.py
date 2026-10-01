#!/usr/bin/env python3
"""The lab hub: one page in front of every lab — VMs up/down, the portal's health, the last test run, links to the
portal, Nautobot, Gitea and GitHub. Labs are declared in ~/.config/lab-hub/labs.json (or $LAB_HUB_CONFIG):
  [{"name": "srv6-core", "dir": "/home/dcantor/srv6-core", "portal": "http://192.168.50.231:8091", "repo": "https://github.com/dcantor/srv6-core",
    "description": "...", "gitea": "http://192.168.50.231:3000/lab/srv6-core-configs"}, ...]
It reads the labs, and it can **power them**: a whole lab up or down, or any single VM, by running that lab's own
`lab.sh up|down [node...]` — the same command an operator would type, so a shutdown still saves each router's
configuration first. Nothing else about a lab is changed here; provisioning stays in the lab's own portal.
Set LAB_HUB_POWER=off to make the hub read-only again. Run with `lab-hub` (uvicorn, port 8088).

It also shows the **shared services** every lab depends on — the NMS VM and Prometheus, VictoriaMetrics, VictoriaLogs,
Grafana, Gitea and Nautobot on it — and can start or shut down that VM (shared.py); and each lab's **CI** — the last runs
on the local Gitea, the tests they committed back, the commits not tested yet — with a run / stop button (ci.py).

Power is guarded (guard.py): a start is checked against the host's available memory and offers to start what the lab
depends on first (the NMS, or a lab whose VMs it attaches); a shutdown warns about running labs that need it. Labs can
be started and stopped on a **schedule**, and shut down when idle (schedule.py)."""
import json, os, re, shutil, subprocess, threading, time, uuid, xml.etree.ElementTree as ET
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
from . import ci as CI, guard as GUARD, schedule as SCHED, shared as SHARED

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
        return {"up": r.ok, "runs": len(runs), "active": active and {"id": active["id"], "mode": active["mode"]},
                "last": runs[0] and {"mode": runs[0]["mode"], "status": runs[0]["status"], "started": runs[0]["started"], "finished": runs[0].get("finished")} if runs else None}
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
                    "tests": last_tests(lab["dir"]), "nautobot": lab.get("nautobot", NAUTOBOT), "power": OPS.get(lab["name"]),
                    "ci_status": CI.status(lab["ci"]) if lab.get("ci") else None,
                    "depends_on": GUARD.depends_on(lab, labs()), "memory_gib": GUARD.lab_total_gib(lab["dir"])})
        out[-1]["schedule"] = SCHED.view(lab["name"], running, out[-1]["portal_health"], out[-1]["ci_status"])
    return {"labs": out, "host": host_stats(), "monitoring": MONITORING, "power_enabled": POWER,
            "services": {**SHARED.status(), "power": SERVICE_OP.get("op")}, "generated": time.time()}


# ---- powering a lab: one operation at a time per lab, run in the background ------------------------------------------
OPS = {}            # lab name -> the current or last operation
_ops_lock = threading.Lock()


class PowerRequest(BaseModel):
    action: str                                  # up | down
    nodes: list[str] = []                        # empty = the whole lab
    confirm: bool = False                        # required for the whole lab: it is every VM of it
    force: bool = False                          # shut down even though the lab's portal has a run in progress
    start_deps: bool = False                     # up: start what the lab depends on first (the NMS, attached labs)
    override_memory: bool = False                # up: start even though the host has not got the memory for it
    override_dependents: bool = False            # down: shut down even though running labs depend on this one


def _running():
    return {l["name"]: sum(1 for v in vm_states(l["dir"]).values() if isinstance(v, dict) and v.get("state") == "running") for l in labs()}


def _run_power(lab, op, deps=()):
    """Start what the lab depends on first (in order), then `lab.sh up|down [node...]`, with the output kept."""
    for d in deps:
        if d["on"] == "nms":
            sub = {"action": "up", "log": []}; op["log"].append("starting the NMS first …"); SHARED.power(sub)
            op["log"] += sub["log"][-2:]
        else:
            dep = next((l for l in labs() if l["name"] == d["on"]), None)
            if dep:
                op["log"].append(f"starting {dep['name']} first ({d['why']}) …")
                r = subprocess.run([str(Path(dep["dir"]) / "lab.sh"), "up"], capture_output=True, text=True, timeout=POWER_TIMEOUT)
                op["log"] += [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-3:]
    cmd = [str(Path(lab["dir"]) / "lab.sh"), op["action"], *op["nodes"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=POWER_TIMEOUT)
        op["log"] = (op["log"] + [l for l in (r.stdout + r.stderr).splitlines() if l.strip()])[-200:]
        op["status"] = "done" if r.returncode == 0 else "failed"
        op["returncode"] = r.returncode
    except subprocess.TimeoutExpired:
        op["status"] = "failed"; op["log"] = [f"timed out after {POWER_TIMEOUT}s"]
    except Exception as e:                       # noqa: BLE001
        op["status"] = "failed"; op["log"] = [f"{e.__class__.__name__}: {e}"]
    op["finished"] = time.time()
    if op["action"] == "up" and op["status"] == "done": SCHED.touch(lab["name"], "started")


class PowerRefused(Exception):
    def __init__(self, code, detail): super().__init__(str(detail)); self.code, self.detail = code, detail


def start_power(lab, req, why="from the hub"):
    """Every power operation, from a button or the scheduler, goes through here and through the same checks."""
    name = lab["name"]
    known = {n for n, v in vm_states(lab["dir"]).items() if isinstance(v, dict)}
    unknown = [n for n in req.nodes if n not in known]
    if unknown: raise PowerRefused(422, f"no such node in {name}: {', '.join(unknown)}")
    if not req.nodes and not req.confirm: raise PowerRefused(422, f"this would {req.action} every VM of {name} — send confirm: true")
    if req.action == "down" and not req.force:   # never pull the rug from under a Terraform apply
        active = (portal_health(lab["portal"]) or {}).get("active")
        if active: raise PowerRefused(409, f"{name}: a {active['mode']} run is in progress ({active['id']}) — let it finish, or send force: true")
        if lab.get("ci") and CI.status(lab["ci"]).get("running"):
            raise PowerRefused(409, f"{name}: a CI run is testing this lab — stop it first, or send force: true")
    pl = GUARD.plan(lab, req.action, req.nodes, labs(), _running())
    m = pl.get("memory") or {}
    fits = m.get("fits_with_deps") if req.start_deps and pl.get("deps_down") else m.get("fits")
    if req.action == "up" and not fits and not req.override_memory:
        need = m["need_gib"] + (m.get("deps_need_gib", 0) if req.start_deps else 0)
        if m.get("unreadable"):
            raise PowerRefused(409, {"reason": "memory", "message": f"{name}: its VMs could not be read (`lab.sh status` lists none), so "
                                     f"the memory it needs is unknown — the host has {m['available_gib']} GiB available", "plan": pl})
        raise PowerRefused(409, {"reason": "memory", "message": f"{name} needs {round(need, 1)} GiB to start {m['vms_to_start']} VM(s)"
                                 f"{' and what it depends on' if req.start_deps and pl.get('deps_down') else ''}; the host has "
                                 f"{m['available_gib']} GiB available and keeps {m['reserve_gib']} GiB in reserve", "plan": pl})
    if req.action == "down" and pl["dependents_up"] and not req.override_dependents:
        names = ", ".join(sorted({d["lab"] for d in pl["dependents_up"]}))
        raise PowerRefused(409, {"reason": "dependents", "message": f"running labs depend on {name}: {names}", "plan": pl})
    deps = pl.get("deps_down", []) if req.action == "up" and req.start_deps else []
    with _ops_lock:
        cur = OPS.get(name)
        if cur and cur["status"] == "running": raise PowerRefused(409, f"{name} is already {cur['action']} ({', '.join(cur['nodes']) or 'the whole lab'})")
        op = OPS[name] = {"id": uuid.uuid4().hex[:8], "lab": name, "action": req.action, "nodes": list(req.nodes), "why": why,
                          "started": time.time(), "finished": None, "status": "running", "log": [], "returncode": None}
    threading.Thread(target=_run_power, args=(lab, op, deps), name=f"power-{name}", daemon=True).start()
    return op


@app.get("/api/labs/{name}/plan", summary="What a power operation would need and break: memory, dependencies (nothing is changed)")
def power_plan(name: str, action: str = "up", nodes: str = ""):
    lab = next((l for l in labs() if l["name"] == name), None)
    if lab is None: raise HTTPException(404, f"no such lab: {name}")
    return GUARD.plan(lab, action, [n for n in nodes.split(",") if n], labs(), _running())


@app.post("/api/labs/{name}/power", summary="Bring a lab up or shut it down — the whole lab, or the nodes you name")
def power(name: str, req: PowerRequest):
    """`{"action": "up"}` starts every VM of the lab, `{"action": "down"}` stops them (each router saves its configuration
    first — it is that lab's own `lab.sh`). `nodes` limits it to those VMs; without `nodes` the whole lab is meant and
    `confirm` must be true. One operation at a time per lab."""
    if not POWER: raise HTTPException(403, "power control is disabled on this hub (LAB_HUB_POWER=off)")
    lab = next((l for l in labs() if l["name"] == name), None)
    if lab is None: raise HTTPException(404, f"no such lab: {name}")
    if req.action not in ("up", "down"): raise HTTPException(422, "action must be up or down")
    try: return start_power(lab, req)
    except PowerRefused as e: raise HTTPException(e.code, e.detail)


@app.get("/api/labs/{name}/power", summary="The current or last power operation of a lab")
def power_status(name: str):
    op = OPS.get(name)
    if op is None: raise HTTPException(404, "no power operation for this lab yet")
    return op


# ---- shared services: the NMS VM -------------------------------------------------------------------------------------
SERVICE_OP = {}


class ConfirmedPower(BaseModel):
    action: str                                  # up | down
    confirm: bool = False
    force: bool = False                          # down: even while a portal run is in progress


@app.get("/api/services/plan", summary="What shutting down the NMS would affect: the running labs that use it")
def services_plan(): return GUARD.nms_plan(labs(), _running())


@app.get("/api/services", summary="The shared services every lab depends on: the NMS VM and each service on it")
def services(): return {**SHARED.status(), "power": SERVICE_OP.get("op")}


@app.post("/api/services/power", summary="Start the NMS VM, or shut it down cleanly (monitoring, CI, Gitea and Nautobot go with it)")
def services_power(req: ConfirmedPower):
    if not POWER: raise HTTPException(403, "power control is disabled on this hub (LAB_HUB_POWER=off)")
    if req.action not in ("up", "down"): raise HTTPException(422, "action must be up or down")
    if not req.confirm: raise HTTPException(422, "send confirm: true")
    if req.action == "down" and not req.force:
        busy = [(l["name"], a) for l in labs() for a in [(portal_health(l["portal"]) or {}).get("active")] if a]
        if busy: raise HTTPException(409, f"portal runs in progress need Nautobot and Gitea: {', '.join(f'{n} ({a['mode']})' for n, a in busy)} — let them finish, or send force: true")
    with _ops_lock:
        cur = SERVICE_OP.get("op")
        if cur and cur["status"] == "running": raise HTTPException(409, f"already {'starting' if cur['action'] == 'up' else 'shutting down'} the NMS")
        op = SERVICE_OP["op"] = {"id": uuid.uuid4().hex[:8], "action": req.action, "nodes": [SHARED.config()["vm"]], "started": time.time(),
                                 "finished": None, "status": "running", "log": []}
    threading.Thread(target=SHARED.power, args=(op,), name="power-nms", daemon=True).start()
    return op


# ---- CI ---------------------------------------------------------------------------------------------------------------
def _ci_lab(name):
    lab = next((l for l in labs() if l["name"] == name), None)
    if lab is None: raise HTTPException(404, f"no such lab: {name}")
    if not lab.get("ci"): raise HTTPException(404, f"{name} has no CI configured in labs.json")
    return lab


@app.get("/api/labs/{name}/ci", summary="A lab's CI: the last runs, test counts, commits not tested yet")
def ci_status(name: str): return CI.status(_ci_lab(name)["ci"], max_age=0)


@app.post("/api/labs/{name}/ci/run", summary="Start CI now: sync the mirror from GitHub, or dispatch the workflow on main")
def ci_run(name: str):
    if not POWER: raise HTTPException(403, "control is disabled on this hub (LAB_HUB_POWER=off)")
    try: return CI.start(_ci_lab(name)["ci"])
    except RuntimeError as e: raise HTTPException(409, str(e))


class Confirm(BaseModel):
    confirm: bool = False


@app.post("/api/labs/{name}/ci/stop", summary="Stop the running CI job carefully: nothing is committed, Robot runs its teardowns")
def ci_stop(name: str, req: Confirm):
    if not POWER: raise HTTPException(403, "control is disabled on this hub (LAB_HUB_POWER=off)")
    if not req.confirm: raise HTTPException(422, "send confirm: true")
    try: return CI.stop(_ci_lab(name)["ci"])
    except RuntimeError as e: raise HTTPException(409, str(e))


# ---- schedules, idle shutdown -------------------------------------------------------------------------------------
@app.get("/api/labs/{name}/schedule", summary="A lab's schedule, what happens next, the idle clock and the scheduler's last actions")
def get_schedule(name: str):
    lab = next((l for l in labs() if l["name"] == name), None)
    if lab is None: raise HTTPException(404, f"no such lab: {name}")
    st = _lab_status(lab); return SCHED.view(name, st["running"], st["portal"], st["ci"])


@app.put("/api/labs/{name}/schedule", summary="Set a lab's schedule: {enabled, start: 'HH:MM', stop: 'HH:MM', days: [...], idle_hours}")
def put_schedule(name: str, body: dict):
    if not any(l["name"] == name for l in labs()): raise HTTPException(404, f"no such lab: {name}")
    try: return SCHED.set_schedule(name, body)
    except ValueError as e: raise HTTPException(422, str(e))


@app.delete("/api/labs/{name}/schedule", summary="Remove a lab's schedule")
def delete_schedule(name: str): SCHED.set_schedule(name, None); return {"removed": name}


@app.post("/api/labs/{name}/keepawake", summary="Someone is using the lab: restart its idle clock")
def keepawake(name: str):
    if not any(l["name"] == name for l in labs()): raise HTTPException(404, f"no such lab: {name}")
    SCHED.touch(name, "kept awake from the hub"); return {"kept_awake": name, "at": time.time()}


def _lab_status(lab):
    vms = vm_states(lab["dir"])
    return {"running": sum(1 for v in vms.values() if isinstance(v, dict) and v.get("state") == "running"),
            "total": len([v for v in vms.values() if isinstance(v, dict)]), "portal": portal_health(lab["portal"]),
            "ci": CI.status(lab["ci"]) if lab.get("ci") else None}


def _scheduled_power(lab, action, why):
    if not POWER: return False, "power control is disabled on this hub"
    try:
        start_power(lab, PowerRequest(action=action, confirm=True, start_deps=True), why=why)
        return True, "started" if action == "up" else "shutting down"
    except PowerRefused as e:
        d = e.detail; return False, "skipped: " + (d["message"] if isinstance(d, dict) else str(d))


@app.on_event("startup")
def _start_sampler():
    threading.Thread(target=_cpu_sample, name="cpu", daemon=True).start()
    threading.Thread(target=SCHED.loop, args=(labs, _lab_status, _scheduled_power), name="scheduler", daemon=True).start()


def main():
    import uvicorn; uvicorn.run(app, host=os.environ.get("LAB_HUB_HOST", "0.0.0.0"), port=int(os.environ.get("LAB_HUB_PORT", "8088")))


if __name__ == "__main__": main()
