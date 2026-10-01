"""CI per lab: the local Gitea keeps a pull mirror of each lab's GitHub repository and its runner (on this host) runs the
lab's workflow against the live lab. The hub shows the last runs, the test counts CI committed back, the commits CI has
not tested yet, and can start or stop a run.

A lab opts in with a "ci" entry in labs.json:
  "ci": {"gitea": "http://10.0.0.10:3000", "repo": "lab/srv6-core", "workflow": "lab-ci.yml", "link": "http://192.168.50.231:3000/lab/srv6-core"}
Gitea credentials: GITEA_USER / GITEA_PASSWORD from the environment or the lab's credential store (lab-secrets), else the
password the NMS keeps (as the labs' own tools/ci.py reads it).

Stopping: Gitea's cancel kills a job outright, so Robot's teardowns never run — a test that had withdrawn a LAN or added a
steering policy would leave it so. The hub instead stops the job on this host the careful way: the step's shells are
killed (so nothing is committed back), then Robot gets one interrupt and runs every teardown before it exits."""
import os, re, signal, subprocess, threading, time
from pathlib import Path
import requests

_cred = {"auth": None}
_cache = {}                       # repo -> (time, status)
ACTIVE = ("running", "waiting", "blocked", "queued")


def _auth(refresh=False):
    if _cred["auth"] and not refresh:
        return _cred["auth"]
    from .. import secrets as SECRETS                        # the environment, then ~/.config/lab/secrets.env
    user, pw = SECRETS.get("GITEA_USER", "lab"), SECRETS.get("GITEA_PASSWORD")
    if not pw:                                                 # not stored yet: read it from the NMS, as before
        try:
            pw = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                                 "-o", "ConnectTimeout=5", "lab@10.0.0.10", "grep ^GITEA_PASSWORD /opt/nautobot/.env | cut -d= -f2"],
                                capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:                                      # noqa: BLE001
            pw = ""
    _cred["auth"] = (user, pw) if pw else None
    return _cred["auth"]


def _get(cfg, path, **params):
    auth = _auth()
    if not auth:
        raise RuntimeError("no Gitea credentials (set GITEA_PASSWORD, or the NMS must be reachable)")
    r = requests.get(f"{cfg['gitea']}/api/v1/repos/{cfg['repo']}{path}", auth=auth, params=params, timeout=8)
    if r.status_code == 401:
        r = requests.get(f"{cfg['gitea']}/api/v1/repos/{cfg['repo']}{path}", auth=_auth(refresh=True), params=params, timeout=8)
    r.raise_for_status()
    return r.json()


def _overall(jobs):
    st = [j["status"] for j in jobs]
    if any(s in ACTIVE for s in st): return "running"
    if "failure" in st: return "failure"
    if "cancelled" in st: return "cancelled"
    return "success" if st and all(s in ("success", "skipped") for s in st) else (st[0] if st else "unknown")


RESULTS = re.compile(r"CI: test results for (\w{8}) — (\d+) passed, (\d+) failed, (\d+) skipped")


def status(cfg, max_age=15):
    key = cfg["repo"]
    if key in _cache and time.time() - _cache[key][0] < max_age:
        return _cache[key][1]
    try:
        tasks = _get(cfg, "/actions/tasks", limit=30).get("workflow_runs", [])
        runs = {}
        for t in tasks:
            r = runs.setdefault(t["run_number"], {"number": t["run_number"], "sha": t["head_sha"], "title": t.get("display_title") or "",
                                                  "event": t.get("event"), "created": t["created_at"], "jobs": [],
                                                  "url": f"{cfg.get('link', cfg['gitea'] + '/' + cfg['repo'])}/actions/runs/{t['run_number']}"})
            r["jobs"].append({"name": t["name"], "status": t["status"], "started": t.get("run_started_at"), "updated": t.get("updated_at")})
            r["created"] = min(r["created"], t["created_at"])
        runs = sorted(runs.values(), key=lambda r: -r["number"])
        for r in runs:
            r["jobs"].sort(key=lambda j: j["started"] or "")
            r["status"] = _overall(r["jobs"])
        commits = _get(cfg, "/commits", sha="main", limit=20, stat="false", verification="false", files="false")
        results = {}
        for c in commits:
            m = RESULTS.search(c["commit"]["message"])
            if m: results.setdefault(m[1], {"passed": int(m[2]), "failed": int(m[3]), "skipped": int(m[4]), "commit": c["sha"][:8]})
        for r in runs:
            r["results"] = results.get(r["sha"][:8])
        tested = {r["sha"] for r in runs}
        untested = []
        for c in commits:                                     # newest first: stop at the first commit a run has covered
            if c["sha"] in tested: break
            msg = c["commit"]["message"]
            if "[skip ci]" not in msg:
                untested.append({"sha": c["sha"][:8], "title": msg.splitlines()[0][:100], "when": c["commit"]["author"]["date"]})
        repo = _get(cfg, "")
        out = {"enabled": True, "runs": runs[:6], "latest": runs[0] if runs else None, "untested": untested,
               "mirror_updated": repo.get("mirror_updated"), "mirror_interval": repo.get("mirror_interval"),
               "link": cfg.get("link"), "running": bool(runs and runs[0]["status"] == "running"), "op": OPS.get(key)}
    except Exception as e:                                     # noqa: BLE001 — the NMS being off is a state, not an error page
        out = {"enabled": True, "error": f"{e.__class__.__name__}: {e}"[:200], "op": OPS.get(key)}
    _cache[key] = (time.time(), out)
    return out


# ---- start / stop -----------------------------------------------------------------------------------------------------
OPS = {}                          # repo -> the current or last start / stop operation
_lock = threading.Lock()


def start(cfg):
    """Sync the mirror now; if that does not start a run (nothing new to sync), dispatch the workflow on main."""
    with _lock:
        cur = OPS.get(cfg["repo"])
        if cur and cur["status"] == "running":
            raise RuntimeError("already starting a run")
        st = status(cfg, max_age=0)
        if st.get("running"):
            raise RuntimeError(f"run {st['latest']['number']} is still in progress")
        op = OPS[cfg["repo"]] = {"action": "run", "status": "running", "started": time.time(), "finished": None, "log": []}

    def go():
        try:
            auth = _auth(); base = f"{cfg['gitea']}/api/v1/repos/{cfg['repo']}"
            before = {r["number"] for r in status(cfg, max_age=0).get("runs", [])}
            requests.post(f"{base}/mirror-sync", auth=auth, timeout=30).raise_for_status(); op["log"].append("mirror sync requested")
            for _ in range(12):
                time.sleep(5)
                new = [r for r in status(cfg, max_age=0).get("runs", []) if r["number"] not in before]
                if new:
                    op["log"].append(f"run {new[0]['number']} started by the sync ({new[0]['sha'][:8]})"); op["status"] = "done"; break
            else:
                r = requests.post(f"{base}/actions/workflows/{cfg['workflow']}/dispatches", auth=auth, json={"ref": "main"}, timeout=30)
                op["log"].append(f"nothing new from GitHub: workflow dispatched on main ({r.status_code})")
                op["status"] = "done" if r.status_code < 300 else "failed"
        except Exception as e:                                 # noqa: BLE001
            op["status"] = "failed"; op["log"].append(f"{e.__class__.__name__}: {e}")
        op["finished"] = time.time(); _cache.pop(cfg["repo"], None)
    threading.Thread(target=go, name=f"ci-start-{cfg['repo']}", daemon=True).start()
    return op


def _procs():
    out = {}
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                stat = (p / "stat").read_text(); ppid = int(stat.rsplit(")", 1)[1].split()[1])
                out[int(p.name)] = (ppid, (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip())
            except Exception:                                  # noqa: BLE001 — processes come and go
                pass
    return out


def stop(cfg):
    """Stop the CI job running on this host: kill the step's shells, then interrupt Robot once so its teardowns run."""
    procs = _procs()
    daemon = next((pid for pid, (_, cmd) in procs.items() if "gitea-runner" in cmd and " daemon" in cmd), None)
    if not daemon:
        raise RuntimeError("no Gitea runner on this host")
    kids = {}
    for pid, (ppid, _) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    desc, todo = [], list(kids.get(daemon, []))
    while todo:
        pid = todo.pop(); desc.append(pid); todo += kids.get(pid, [])
    if not desc:
        raise RuntimeError("no CI job is running on this host")
    robot = [pid for pid in desc if re.search(r"bin/robot\b", procs[pid][1])]
    under_robot = set()
    todo = list(robot)
    while todo:
        pid = todo.pop(); under_robot.add(pid); todo += kids.get(pid, [])
    shells = [pid for pid in desc if pid not in under_robot]
    with _lock:
        op = OPS[cfg["repo"]] = {"action": "stop", "status": "running", "started": time.time(), "finished": None, "log": []}
    for pid in shells:
        try: os.kill(pid, signal.SIGKILL)
        except ProcessLookupError: pass
    op["log"].append(f"stopped the CI step ({len(shells)} process(es)): nothing will be committed back")
    for pid in robot:
        try: os.kill(pid, signal.SIGINT)
        except ProcessLookupError: pass
    if robot:
        op["log"].append("Robot interrupted: it finishes the current test's teardown and every suite teardown, then exits")

    def follow():
        deadline = time.time() + 600
        while time.time() < deadline and any(Path(f"/proc/{p}").exists() for p in robot):
            time.sleep(3)
        op["log"].append("Robot has exited" if robot else "the job had no Robot run (validate step)")
        op["status"] = "done"; op["finished"] = time.time(); _cache.pop(cfg["repo"], None)
    threading.Thread(target=follow, name=f"ci-stop-{cfg['repo']}", daemon=True).start()
    return op
