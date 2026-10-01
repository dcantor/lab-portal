"""Safe power control: what a power operation would need, and what it would break, before it runs.

Memory. Every VM of a lab is a libvirt domain with a fixed memory size. Starting VMs that are shut off needs the sum of
their sizes; the host must have that available (MemAvailable) with RESERVE_GIB to spare — otherwise the host swaps or
the OOM killer picks a VM. Sizes come from `virsh dominfo` (cached; they only change when a lab is rebuilt).

Dependencies. A lab depends on other labs or on the NMS:
  - every lab on the NMS (soft: the lab runs without it, but monitoring, CI, config backups and Nautobot do not, and a
    portal run that seeds Nautobot fails) — "depends_on": [{"on": "nms", ...}] in labs.json, the default for every lab;
  - a lab on another lab whose VMs it attaches (hard): read from its lab.conf — srv6-core's EXT_LAB names the
    cat8000v-ipsec headends when they are attached as tenant-a sites, and nothing when they are detached.
Starting a lab offers to start what it depends on first; shutting one down warns about the running labs that need it."""
import os, re, subprocess, time
from pathlib import Path
from . import shared as SHARED

RESERVE_GIB = float(os.environ.get("LAB_HUB_RESERVE_GIB", "2"))
_mem = {"at": 0, "sizes": {}}


def _domain_sizes():
    """libvirt domain -> memory (MiB), for every domain on the host; refreshed every 10 minutes."""
    if time.time() - _mem["at"] < 600 and _mem["sizes"]:
        return _mem["sizes"]
    script = ("for d in $(virsh -q -c {u} list --all --name); do printf '%s ' \"$d\"; "
              "virsh -q -c {u} dominfo \"$d\" | awk '/^Max memory/ {{print $3}}'; done").format(u=SHARED.config()["libvirt"])
    try:
        out = subprocess.run(["sg", "libvirt", "-c", script], capture_output=True, text=True, timeout=120).stdout
        sizes = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                sizes[parts[0]] = int(parts[1]) // 1024
        if sizes:
            _mem.update(at=time.time(), sizes=sizes)
    except Exception:                                          # noqa: BLE001 — no sizes means "unknown", never a crash
        pass
    return _mem["sizes"]


def lab_vms(lab_dir):
    """[(node, domain, state)] from `lab.sh status`: the domain is the VM column when the lab has one (c8000v-dmvpn-lab
    prefixes its domains), else the node name."""
    try:
        out = subprocess.run([str(Path(lab_dir) / "lab.sh"), "status"], capture_output=True, text=True, timeout=60).stdout
    except Exception:                                          # noqa: BLE001
        return []
    # (a lab whose `lab.sh status` fails part-way yields fewer rows, or none: memory_plan then says it cannot tell)
    lines = out.splitlines(); vm_col = None; rows = []
    for line in lines:
        cols = line.split()
        if cols[:1] == ["NODE"]:
            vm_col = cols.index("VM") if "VM" in cols else None
            continue
        m = re.match(r"^(\S+)\s+(\S+)\s+(running|shut off|undefined|paused|crashed|in shutdown)\b(.*)$", line)
        if m:
            rest = line[m.end(3):].split()
            dom = rest[vm_col - 3] if vm_col is not None and len(rest) > vm_col - 3 else m[1]
            rows.append((m[1], dom, m[3]))
    return rows


def memory_plan(lab_dir, nodes=()):
    """What starting these nodes (or the whole lab) needs, and whether the host has it."""
    sizes = _domain_sizes()
    vms = [v for v in lab_vms(lab_dir) if not nodes or v[0] in nodes]
    to_start = [v for v in vms if v[2] != "running"]
    unknown = [v[0] for v in to_start if v[1] not in sizes]
    need = sum(sizes.get(v[1], 0) for v in to_start) / 1024
    avail = 0.0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            avail = int(line.split()[1]) / 1024 / 1024
    usable = max(0.0, avail - RESERVE_GIB)
    unreadable = not vms                                       # `lab.sh status` listed no VMs: the need is unknown, not zero
    return {"vms_to_start": len(to_start), "need_gib": round(need, 1), "available_gib": round(avail, 1),
            "reserve_gib": RESERVE_GIB, "fits": need <= usable and not unreadable, "unreadable": unreadable, "unknown_sizes": unknown,
            "largest": sorted(((v[0], round(sizes.get(v[1], 0) / 1024, 1)) for v in to_start), key=lambda x: -x[1])[:5]}


_totals = {}


def lab_total_gib(lab_dir):
    """Memory of every VM of the lab together (what it takes when fully up); cached for 10 minutes."""
    hit = _totals.get(lab_dir)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    sizes = _domain_sizes()
    total = round(sum(sizes.get(dom, 0) for _, dom, _ in lab_vms(lab_dir)) / 1024, 1)
    _totals[lab_dir] = (time.time(), total)
    return total


def _ext_labs(lab_dir):
    """Directories of other labs this lab attaches VMs from, per its lab.conf (uncommented, non-empty EXT_LAB)."""
    try:
        conf = (Path(lab_dir) / "lab.conf").read_text()
    except Exception:                                          # noqa: BLE001
        return set()
    dirs = set()
    for line in conf.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if re.search(r"\bEXT_LAB=\(", s) or re.search(r"\bEXT_LAB\[", s):
            dirs |= {d.rstrip("/") for d in re.findall(r"\]=(\S+?)\s*(?=\[|\)|$)", s)}
    return dirs


def depends_on(lab, all_labs):
    """[{"on": "nms" | <lab name>, "kind": "soft" | "hard", "why": ...}] for one lab."""
    deps = list(lab.get("depends_on", [{"on": "nms", "kind": "soft", "why": "monitoring, CI, config backups, Nautobot"}]))
    by_dir = {str(Path(l["dir"])).rstrip("/"): l["name"] for l in all_labs}
    for d in sorted(_ext_labs(lab["dir"])):
        name = by_dir.get(d)
        if name and name != lab["name"] and not any(x["on"] == name for x in deps):
            deps.append({"on": name, "kind": "hard", "why": "its VMs are attached to this lab (EXT_LAB in lab.conf)"})
    return deps


def plan(lab, action, nodes, all_labs, running):
    """Everything the page needs to ask the right questions. `running` = {lab name: running VM count}."""
    out = {"lab": lab["name"], "action": action, "nodes": list(nodes)}
    if action == "up":
        out["memory"] = memory_plan(lab["dir"], nodes)
        nms_on = SHARED.vm_state() == "running"
        out["deps_down"] = [d for d in depends_on(lab, all_labs)
                            if (d["on"] == "nms" and not nms_on) or (d["on"] != "nms" and not running.get(d["on"]))]
        extra = 0.0                                            # what starting the dependencies first would take as well
        for d in out["deps_down"]:
            if d["on"] == "nms":
                extra += _domain_sizes().get(SHARED.config()["vm"], 0) / 1024
            else:
                dep = next((l for l in all_labs if l["name"] == d["on"]), None)
                if dep: extra += memory_plan(dep["dir"])["need_gib"]
        m = out["memory"]; m["deps_need_gib"] = round(extra, 1)
        m["fits_with_deps"] = m["need_gib"] + extra <= max(0.0, m["available_gib"] - m["reserve_gib"])
    else:
        whole = not nodes
        out["dependents_up"] = [{"lab": l["name"], **d} for l in all_labs if l["name"] != lab["name"] and running.get(l["name"])
                                for d in depends_on(l, all_labs) if d["on"] == lab["name"] and (whole or d["kind"] == "hard")]
    return out


def nms_plan(all_labs, running):
    """Shutting down the NMS: every running lab loses monitoring, CI, backups and Nautobot."""
    return {"dependents_up": [{"lab": l["name"], **d} for l in all_labs if running.get(l["name"])
                              for d in depends_on(l, all_labs) if d["on"] == "nms"]}
