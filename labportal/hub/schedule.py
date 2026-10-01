"""Scheduled power and idle shutdown, per lab.

A schedule (in ~/.config/lab-hub/schedules.json, edited from the page):
  {"srv6-core": {"enabled": true, "start": "08:00", "stop": "20:00", "days": ["mon", "tue", "wed", "thu", "fri"],
                 "idle_hours": 4}}
  start / stop   start or shut down the whole lab at that time (host local time) on those days; null = never
  idle_hours     shut a running lab down once it has been idle that long; null = never

Idle means none of these happened for idle_hours: the lab was started, a portal run started or finished, a CI run on
it started or finished, or someone pressed "I'm using it" (keep awake). A lab with a portal run or a CI run in
progress is never idle. Logins on the VMs themselves are not seen — hence the keep-awake button.

Every scheduled action goes through the same checks as a button press: a start is skipped when the host has not got
the memory for it, and starts what the lab depends on (the NMS) first; a shutdown is skipped while a portal or CI run is
in progress or another running lab needs this one. What the scheduler did, or why it did not, is kept per lab
(state in ~/.local/state/lab-hub/state.json). A time the hub was not running for is not caught up later."""
import json, os, threading, time
from datetime import datetime
from pathlib import Path

CONFIG = Path(os.environ.get("LAB_HUB_SCHEDULES", Path.home() / ".config" / "lab-hub" / "schedules.json"))
STATE = Path(os.environ.get("LAB_HUB_STATE", Path.home() / ".local" / "state" / "lab-hub" / "state.json"))
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_lock = threading.Lock()


def _read(p, default):
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except Exception:                                          # noqa: BLE001
        return default


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=1) + "\n"); tmp.replace(p)


def schedules():
    return _read(CONFIG, {})


def validate(s):
    out = {"enabled": bool(s.get("enabled", True)), "start": s.get("start") or None, "stop": s.get("stop") or None,
           "days": [d for d in DAYS if d in (s.get("days") or DAYS)], "idle_hours": s.get("idle_hours") or None}
    for k in ("start", "stop"):
        if out[k]:
            try: datetime.strptime(out[k], "%H:%M")
            except ValueError: raise ValueError(f"{k} must be HH:MM (24 h), not {out[k]!r}")
    if out["idle_hours"] is not None:
        try: out["idle_hours"] = float(out["idle_hours"])
        except (TypeError, ValueError): raise ValueError("idle_hours must be a number of hours")
        if not 0.25 <= out["idle_hours"] <= 168: raise ValueError("idle_hours must be between 0.25 and 168")
    if out["start"] and out["stop"] and out["start"] == out["stop"]:
        raise ValueError("start and stop cannot be the same time")
    return out


def set_schedule(name, s):
    with _lock:
        all_ = schedules()
        if s is None: all_.pop(name, None)
        else: all_[name] = validate(s)
        _write(CONFIG, all_)
        return all_.get(name)


# ---- activity and events ------------------------------------------------------------------------------------------
def state():
    return _read(STATE, {"active": {}, "fired": {}, "events": {}, "seen_running": {}})


def _save(st):
    _write(STATE, st)


def touch(name, why):
    """Something happened on the lab: the idle clock restarts."""
    with _lock:
        st = state(); st["active"][name] = {"at": time.time(), "why": why}; _save(st)


def event(name, text, ok=True):
    with _lock:
        st = state(); ev = st["events"].setdefault(name, []); ev.insert(0, {"at": time.time(), "text": text, "ok": ok})
        del ev[10:]; _save(st)


def last_activity(name, running, portal, ci):
    """(time, why) of the latest activity we can see, for a running lab."""
    st = state(); cands = []
    if st["active"].get(name): cands.append((st["active"][name]["at"], st["active"][name]["why"]))
    if running and name in st["seen_running"]: cands.append((st["seen_running"][name], "seen running by the hub"))
    last = (portal or {}).get("last") or {}
    for k in ("finished", "started"):
        if last.get(k): cands.append((last[k], f"portal run {last.get('mode', '')} {k}")); break
    if ci and ci.get("latest"):
        r = ci["latest"]; upd = max((j.get("updated") or "" for j in r["jobs"]), default="")
        if upd:
            try: cands.append((datetime.fromisoformat(upd.replace("Z", "+00:00")).timestamp(), f"CI run {r['number']}"))
            except ValueError: pass
    return max(cands) if cands else (None, None)


def view(name, running, portal, ci):
    """The schedule, what happens next, and the idle clock — for the page."""
    s = schedules().get(name)
    st = state(); out = {"schedule": s, "events": st["events"].get(name, [])}
    if not s or not s.get("enabled"):
        return out
    now = datetime.now(); nxt = []
    for k in ("start", "stop"):
        if not s.get(k): continue
        hh, mm = map(int, s[k].split(":"))
        for add in range(0, 8):
            day = datetime.fromtimestamp(now.timestamp() + add * 86400).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if day > now and DAYS[day.weekday()] in s["days"]:
                nxt.append((day.timestamp(), k)); break
    if nxt:
        at, k = min(nxt); out["next"] = {"action": k, "at": at}
    if s.get("idle_hours") and running:
        at, why = last_activity(name, running, portal, ci)
        busy = bool((portal or {}).get("active")) or bool(ci and ci.get("running"))
        out["idle"] = {"since": at, "why": why, "limit_h": s["idle_hours"], "busy": busy,
                       "stops_at": None if busy or at is None else at + s["idle_hours"] * 3600}
    return out


# ---- the scheduler ------------------------------------------------------------------------------------------------
def loop(get_labs, lab_status, start_power, interval=60):
    """Every minute: fire the start / stop times that match, and shut down labs idle past their limit.
    `lab_status(lab)` -> {running, total, portal, ci}; `start_power(lab, action, why)` -> (ok, message)."""
    while True:
        try:
            tick(get_labs, lab_status, start_power)
        except Exception as e:                                 # noqa: BLE001 — the scheduler must never die
            print(f"lab-hub scheduler: {e.__class__.__name__}: {e}", flush=True)
        time.sleep(interval - time.time() % interval + 1)


def tick(get_labs, lab_status, start_power):
    now = datetime.now(); key_day = now.strftime("%Y-%m-%d"); hm = now.strftime("%H:%M"); today = DAYS[now.weekday()]
    sched = schedules()
    for lab in get_labs():
        name = lab["name"]; s = sched.get(name); stt = lab_status(lab)
        with _lock:                                            # remember when a lab was first seen running (started outside the hub)
            st = state()
            if stt["running"] and name not in st["seen_running"]: st["seen_running"][name] = time.time(); _save(st)
            if not stt["running"] and name in st["seen_running"]: st["seen_running"].pop(name); _save(st)
        if not s or not s.get("enabled"):
            continue
        for k, action in (("start", "up"), ("stop", "down")):
            if s.get(k) == hm and today in s["days"]:
                fired = f"{name}|{k}|{key_day}|{hm}"
                st = state()
                if st["fired"].get(f"{name}|{k}") == fired:
                    continue
                with _lock:
                    st = state(); st["fired"][f"{name}|{k}"] = fired; _save(st)
                if action == "up" and stt["running"] == stt["total"]:
                    event(name, f"scheduled start at {hm}: already running"); continue
                if action == "down" and not stt["running"]:
                    event(name, f"scheduled stop at {hm}: already shut down"); continue
                ok, msg = start_power(lab, action, f"scheduled {k} at {hm}")
                event(name, f"scheduled {k} at {hm}: {msg}", ok)
        if s.get("idle_hours") and stt["running"]:
            v = view(name, stt["running"], stt["portal"], stt["ci"]).get("idle") or {}
            if v.get("stops_at") and time.time() >= v["stops_at"]:
                ok, msg = start_power(lab, "down", f"idle for {s['idle_hours']} h (last activity: {v.get('why')})")
                event(name, f"idle shutdown after {s['idle_hours']} h: {msg}", ok)
                touch(name, "idle shutdown")            # do not retry every minute if it was refused
