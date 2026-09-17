#!/usr/bin/env python3
"""The lab hub: one page in front of every lab — VMs up/down, the portal's health, the last test run, links to the
portal, Nautobot, Gitea and GitHub. Labs are declared in ~/.config/lab-hub/labs.json (or $LAB_HUB_CONFIG):
  [{"name": "srv6-core", "dir": "/home/dcantor/srv6-core", "portal": "http://192.168.50.231:8091", "repo": "https://github.com/dcantor/srv6-core",
    "description": "...", "gitea": "http://192.168.50.231:3000/lab/srv6-core-configs"}, ...]
Read-only by design: it never starts, stops or changes a lab. Run with `lab-hub` (uvicorn, port 8088)."""
import json, os, re, subprocess, time, xml.etree.ElementTree as ET
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
import requests

CONFIG = Path(os.environ.get("LAB_HUB_CONFIG", Path.home() / ".config" / "lab-hub" / "labs.json"))
NAUTOBOT = os.environ.get("NAUTOBOT_PUBLIC_URL", "http://192.168.50.231:8080")
app = FastAPI(title="Lab hub", version="1.0", description="Every lab on this host at a glance: VM state, portal health, last tests, links.")


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
                    "tests": last_tests(lab["dir"]), "nautobot": lab.get("nautobot", NAUTOBOT)})
    try:
        free = subprocess.run(["free", "-g"], capture_output=True, text=True).stdout.splitlines()[1].split(); mem = {"total_gib": int(free[1]), "used_gib": int(free[2]), "available_gib": int(free[6])}
    except Exception: mem = None
    return {"labs": out, "host": {"memory": mem, "load": os.getloadavg()}, "generated": time.time()}


def main():
    import uvicorn; uvicorn.run(app, host=os.environ.get("LAB_HUB_HOST", "0.0.0.0"), port=int(os.environ.get("LAB_HUB_PORT", "8088")))


if __name__ == "__main__": main()
