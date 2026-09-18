"""The run engine shared by the portals.

A portal subclasses RunBase: `plan()` returns the ordered step names for the run's mode, `STEP_TITLES` names them, and a
`do_<step>(self, step)` method implements each one (raise to fail the run). RunBase does the rest: ids, step records,
streamed log, persistence to runs/<id>.json, execution with failure handling, and carrying successful steps over when a
failed or interrupted run is resumed. RunRegistry keeps the live runs, serialises them (one at a time), and
install_runs_api() mounts the common endpoints (list, get with incremental log, resume) plus the startup hook that marks
runs interrupted by a restart. POST /api/runs stays in the portal (its validation is lab-specific) and calls registry.start()."""
import json, os, subprocess, threading, time, uuid, xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


class RunBase:
    STEP_TITLES = {}
    #: extra fields a portal wants in to_dict() (name -> attribute); e.g. {"tenant": "tenant_name"}
    EXTRA = {}

    def __init__(self, mode, options=None, resume_of=None, runs_dir=None, cwd=None):
        self.id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "-" + uuid.uuid4().hex[:4]
        self.mode, self.options, self.resume_of = mode, options or {}, resume_of
        self.runs_dir, self.cwd = Path(runs_dir), Path(cwd) if cwd else None
        self.started, self.finished, self.status, self.error = time.time(), None, "queued", None
        self.log, self.tests, self.results_dir = [], None, None
        self.steps = [{"name": s, "title": self.STEP_TITLES.get(s, s), "status": "pending", "started": None, "finished": None, "summary": ""} for s in self.plan()]
        if resume_of:   # steps that succeeded in the interrupted / failed run are carried over, everything from the failure on is redone
            done = {st["name"]: st for st in resume_of["steps"] if st["status"] == "success"}
            for st in self.steps:
                if st["name"] not in done: break
                st.update({"status": "success", "summary": f"(from run {resume_of['id']}) {done[st['name']]['summary']}", "started": done[st["name"]]["started"], "finished": done[st["name"]]["finished"]})

    def plan(self):
        raise NotImplementedError

    def to_dict(self, with_log=True):
        d = {"id": self.id, "mode": self.mode, "status": self.status, "started": self.started, "finished": self.finished, "steps": self.steps, "tests": self.tests,
             "results_dir": self.results_dir, "error": self.error, "options": self.options, "resume_of": self.resume_of and self.resume_of["id"]}
        for key, attr in self.EXTRA.items(): d[key] = getattr(self, attr, None)
        if with_log: d["log"] = self.log
        return d

    def say(self, line): self.log.append({"t": time.time(), "line": line.rstrip("\n")})
    def persist(self): (self.runs_dir / f"{self.id}.json").write_text(json.dumps(self.to_dict(), indent=1))
    def step(self, name): return next(s for s in self.steps if s["name"] == name)

    def sh(self, cmd, cwd=None, env=None, timeout=3600, check=True):
        """Run a command, streaming stdout+stderr into the run log; returns the exit code (raises on failure when check)."""
        cmd = [str(c) for c in cmd]; self.say(f"$ {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, cwd=cwd or self.cwd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout: self.say(line)
        proc.wait(timeout=timeout)
        if check and proc.returncode != 0: raise RuntimeError(f"{' '.join(cmd[:2])} failed (rc={proc.returncode})")
        return proc.returncode

    def after(self):
        """Hook after a run finishes (any status) — e.g. invalidate caches."""

    LAB = "lab"   # Grafana annotation tag (a portal sets its lab name)

    def execute(self):
        self.status = "running"; self.persist()
        from . import grafana
        ann = grafana.annotate(f"{self.LAB}: run {self.mode} started ({self.id})", tags=[self.LAB, "run", self.mode])
        try:
            for s in self.steps:
                if s["status"] == "success": continue
                s["status"], s["started"] = "running", time.time(); self.persist()
                getattr(self, "do_" + s["name"])(s)
                s["status"], s["finished"] = "success", time.time(); self.persist()
            self.status = "success"
        except Exception as e:  # noqa: BLE001 - any failure ends the run
            cur = next((s for s in self.steps if s["status"] == "running"), None)
            if cur: cur["status"], cur["finished"] = "failed", time.time(); cur["summary"] = cur["summary"] or str(e)
            for s in self.steps:
                if s["status"] == "pending": s["status"] = "skipped"
            self.error = str(e); self.status = "failed"; self.say(f"!! {e}")
        self.finished = time.time(); self.persist()
        grafana.update(ann, text=f"{self.LAB}: run {self.mode} {self.status} ({self.id})" + (f" — {self.error}" if self.error else ""), end=self.finished, tags=[self.LAB, "run", self.mode, self.status])
        try: self.after()
        except Exception: pass

    def record_tests(self, results_root, step, rc):
        """Common tail of a Robot step: parse results/latest, set summary, raise when tests failed."""
        latest = Path(results_root) / "latest"
        if latest.exists():
            self.results_dir = latest.resolve().name; self.tests = parse_robot(latest / "output.xml"); step["summary"] = f"{self.tests['passed']}/{self.tests['total']} passed"
        if rc != 0: raise RuntimeError(f"tests failed ({step['summary'] or 'no report'})")


def parse_robot(path):
    root = ET.parse(path).getroot(); suites = []
    for suite in root.iter("suite"):
        tests = suite.findall("test")
        if not tests: continue
        rows = []
        for t in tests:
            st = t.find("status"); rows.append({"name": t.get("name"), "status": st.get("status"), "message": (st.text or "").strip()[:800], "elapsed": st.get("elapsed"), "start": st.get("start")})
        suites.append({"name": suite.get("name"), "tests": rows, "passed": sum(r["status"] == "PASS" for r in rows), "failed": sum(r["status"] == "FAIL" for r in rows)})
    total = sum(len(s["tests"]) for s in suites); failed = sum(s["failed"] for s in suites)
    return {"suites": suites, "total": total, "passed": total - failed, "failed": failed}


class RunRegistry:
    def __init__(self, runs_dir):
        self.runs_dir = Path(runs_dir); self.runs_dir.mkdir(exist_ok=True)
        self.runs, self.lock, self.worker = {}, threading.Lock(), threading.Lock()

    def busy(self): return any(r.status in ("queued", "running") for r in self.runs.values())

    def start(self, run):
        with self.lock:
            if self.busy(): raise RuntimeError("a run is already in progress")
            self.runs[run.id] = run
        def work():
            with self.worker: run.execute()
        threading.Thread(target=work, daemon=True).start(); return run.to_dict(with_log=False)

    def load(self, run_id):
        run = self.runs.get(run_id)
        if run: return run.to_dict()
        f = self.runs_dir / f"{run_id}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def list(self, limit=30):
        items = [r.to_dict(with_log=False) for r in self.runs.values()]; seen = {r["id"] for r in items}
        for f in sorted(self.runs_dir.glob("*.json"), reverse=True):
            if f.name.endswith(".intent.json") or f.stem in seen: continue
            try: d = json.loads(f.read_text()); d.pop("log", None); items.append(d)
            except ValueError: pass
        return sorted(items, key=lambda r: r["started"], reverse=True)[:limit]

    def mark_interrupted(self):
        for f in self.runs_dir.glob("*.json"):
            if f.name.endswith(".intent.json"): continue
            try: d = json.loads(f.read_text())
            except ValueError: continue
            if d.get("status") in ("running", "queued"):
                for st in d["steps"]:
                    if st["status"] == "running": st["status"] = "failed"; st["summary"] = st["summary"] or "interrupted (portal restarted)"
                    elif st["status"] == "pending": st["status"] = "skipped"
                d["status"], d["error"], d["finished"] = "interrupted", "interrupted: the portal was restarted", time.time(); f.write_text(json.dumps(d, indent=1))


def install_runs_api(app, registry, resume_factory, tag="runs"):
    """GET /api/runs, GET /api/runs/{id}?since=, POST /api/runs/{id}/resume, and the startup interrupted-marking.
    resume_factory(old_dict) -> a new run object built from the old run's record (mode, options, spec...)."""
    from fastapi import HTTPException, Query, Path as PathParam

    @app.on_event("startup")
    def _mark(): registry.mark_interrupted()

    @app.get("/api/runs", tags=[tag], summary="Recent runs (newest first, without logs)")
    def list_runs(): return registry.list()

    @app.get("/api/runs/{run_id}", tags=[tag], summary="Run status, steps, log and test report", responses={404: {"description": "no such run"}})
    def get_run(run_id: str = PathParam(..., description="run id"), since: int = Query(0, description="return log lines from this offset (incremental polling)")):
        d = registry.load(run_id)
        if d is None: raise HTTPException(404, "no such run")
        d["log"] = d.get("log", [])[since:]; d["log_offset"] = since; return d

    @app.post("/api/runs/{run_id}/resume", tags=[tag], summary="Resume a failed or interrupted run", responses={404: {"description": "no such run"}, 409: {"description": "not resumable / a run is in progress"}})
    def resume_run(run_id: str = PathParam(..., description="id of the failed / interrupted run")):
        """Starts a new run that keeps the successful steps of the given run and redoes everything from the failed step on."""
        d = registry.load(run_id)
        if d is None: raise HTTPException(404, "no such run")
        if d["status"] not in ("failed", "interrupted"): raise HTTPException(409, f"run is {d['status']}")
        try: return registry.start(resume_factory(d))
        except RuntimeError as e: raise HTTPException(409, str(e))
