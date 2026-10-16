"""Fast resume: shut a lab down by saving each VM's memory to disk (`virsh managedsave`), so the next start resumes it
where it was in a minute or so instead of booting it (an XRv9k takes ten). libvirt keeps the image (in
/var/lib/libvirt/qemu/save) until the VM is started, which reads it back, and a VM with one is listed as "saved".

Opt-in per lab ("fast_resume": true in labs.json), for labs whose nodes are all libvirt VMs. The lab's lab.sh has to
know about it: `undefine --managed-save` wherever it undefines a VM (a rebuild), and a start that falls back to a cold
boot when an image cannot be read back (`virsh start || managedsave-remove; start`). After a resume the hub runs
`lab.sh resumed` when the lab has that command: the guests' clocks stand still while they are saved, and a lab may want
to set them, or anything else that lives on time.

A full shutdown is still there (the dialog's choice, `mode: "shutdown"`): `lab.sh down`, each router saving its
configuration, and a cold boot next time. Saving also falls back to it when the disk has not got the room."""
import json, os, shutil, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import guard as GUARD, shared as SHARED

META = Path(os.environ.get("LAB_HUB_SAVED", Path.home() / ".local" / "state" / "lab-hub" / "saved.json"))
SAVE_DIR = "/var/lib/libvirt/qemu/save"
DISK_SPARE_GIB = 20                                            # what the disk keeps free after the images are written


def enabled(lab):
    return bool(lab.get("fast_resume")) and GUARD.lab_unit(lab["dir"]) == "VMs"


def _meta():
    try: return json.loads(META.read_text())
    except Exception: return {}                                # noqa: BLE001


def _write_meta(m):
    META.parent.mkdir(parents=True, exist_ok=True); tmp = META.with_suffix(".tmp"); tmp.write_text(json.dumps(m, indent=1)); tmp.replace(META)


def info(lab):
    """What of the lab is saved: nodes, when, and about how much disk the images take (the memory the VMs held)."""
    saved = GUARD.saved_domains(); m = _meta()
    rows = [(n, dom) for n, dom, _st in GUARD.lab_vms(lab["dir"]) if dom in saved]
    if not rows: return None
    at = [m[d]["at"] for _n, d in rows if d in m]
    return {"nodes": [n for n, _ in rows], "count": len(rows), "saved_at": min(at) if at else None,
            "disk_gib": round(sum(m.get(d, {}).get("mib", 0) for _n, d in rows) / 1024, 1)}


def save(lab, nodes, op):
    """Save the running VMs of the lab (or the nodes named). Returns the nodes that could not be saved: the caller shuts
    those down the ordinary way."""
    vms = [(n, dom) for n, dom, st in GUARD.lab_vms(lab["dir"]) if st == "running" and (not nodes or n in nodes)]
    if not vms: return []
    rss = GUARD.qemu_rss(); need = sum(rss.get(d, 0) for _n, d in vms) / 1024
    free = shutil.disk_usage("/var/lib/libvirt").free / 2**30
    if free - need < DISK_SPARE_GIB:
        op["log"].append(f"not saving: the images would take about {need:.0f} GiB and the disk has {free:.0f} GiB free "
                         f"({DISK_SPARE_GIB} GiB kept spare); shutting down the ordinary way")
        return [n for n, _ in vms]
    op["log"].append(f"saving {len(vms)} VM(s) to disk (about {need:.1f} GiB) …")

    def one(nd):
        n, dom = nd; t = time.time()
        r = SHARED.virsh("managedsave", dom, timeout=900)
        return n, dom, r.returncode == 0, time.time() - t, (r.stderr or r.stdout).strip()
    failed, m = [], _meta()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for n, dom, ok, secs, err in ex.map(one, vms):
            if ok:
                m[dom] = {"lab": lab["name"], "at": time.time(), "mib": rss.get(dom, 0)}
                op["log"].append(f"[{n}] saved ({secs:.0f} s)")
            else:
                failed.append(n); op["log"].append(f"[{n}] could not be saved: {err[-200:]}")
    _write_meta(m); GUARD.invalidate(lab["dir"])
    return failed


def discard(lab, nodes, op):
    """Throw the saved images away: the next start is a cold boot."""
    saved = GUARD.saved_domains(); m = _meta()
    for n, dom, _st in GUARD.lab_vms(lab["dir"]):
        if dom in saved and (not nodes or n in nodes):
            r = SHARED.virsh("managedsave-remove", dom, timeout=60)
            op["log"].append(f"[{n}] saved state discarded" if r.returncode == 0 else f"[{n}] could not discard: {r.stderr.strip()[-200:]}")
            m.pop(dom, None)
    _write_meta(m); GUARD.invalidate(lab["dir"])


def forget(doms):
    """After a start: those images are gone (libvirt deletes them once read back)."""
    m = _meta()
    if any(d in m for d in doms): _write_meta({k: v for k, v in m.items() if k not in doms})


def has_resumed_hook(lab):
    try: return "cmd_resumed" in (Path(lab["dir"]) / "lab.sh").read_text()
    except OSError: return False


def after_resume(lab, nodes, op, timeout=600):
    if not has_resumed_hook(lab): return
    op["log"].append("lab.sh resumed …")
    try:
        r = subprocess.run([str(Path(lab["dir"]) / "lab.sh"), "resumed", *nodes], capture_output=True, text=True, timeout=timeout)
        op["log"] += [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-20:]
    except subprocess.TimeoutExpired:
        op["log"].append(f"lab.sh resumed: timed out after {timeout}s")
