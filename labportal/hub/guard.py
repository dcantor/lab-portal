"""Safe power control: what a power operation would need, and what it would break, before it runs.

Memory. Every VM of a lab is a libvirt domain with a fixed memory size. Starting VMs that are shut off needs the sum of
their sizes; the host must have that available (MemAvailable) with RESERVE_GIB to spare — otherwise the host swaps or
the OOM killer picks a VM. Sizes come from `virsh dominfo` (cached; they only change when a lab is rebuilt).

Dependencies. A lab depends on other labs or on the NMS:
  - every lab on the NMS (soft: the lab runs without it, but monitoring, CI, config backups and Nautobot do not, and a
    portal run that seeds Nautobot fails) — "depends_on": [{"on": "nms", ...}] in labs.json, the default for every lab;
  - a lab on another lab whose VMs it attaches (hard): read from its lab.conf — srv6-core's EXT_LAB names the
    cat8000v-ipsec headends when they are attached as tenant-a sites, and nothing when they are detached.
Starting a lab offers to start what it depends on first; shutting one down warns about the running labs that need it.

Containers. A containerlab lab (evpn-clab) lists Docker containers in `lab.sh status` where a libvirt lab lists domains.
Their states come from one `docker ps -a` and are read in libvirt's words (running / shut off); their size is the
container's memory limit (containerlab `memory:`), and what they hold is their cgroup's memory.current."""
import grp, os, re, shlex, subprocess, time
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
        sizes.update(_container_sizes())
        if sizes:
            _mem.update(at=time.time(), sizes=sizes)
    except Exception:                                          # noqa: BLE001 — no sizes means "unknown", never a crash
        pass
    return _mem["sizes"]


# ---- Docker containers (containerlab labs) ------------------------------------------------------------------------------
try: _DOCKER_GID = grp.getgrnam("docker").gr_gid
except KeyError: _DOCKER_GID = None
DOCKER_STATES = {"running": "running", "exited": "shut off", "created": "shut off", "dead": "crashed", "paused": "paused",
                 "restarting": "in shutdown", "removing": "in shutdown"}


def _docker(*args, timeout=20):
    """docker, through `sg docker` when this process lacks the group (a user unit started before the user joined it)."""
    cmd = ["docker", *args]
    if _DOCKER_GID is not None and _DOCKER_GID not in os.getgroups():
        cmd = ["sg", "docker", "-c", shlex.join(cmd)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _container_sizes():
    """container name -> memory limit (MiB), for containers that have one."""
    try:
        ids = _docker("ps", "-aq").stdout.split()
        if not ids: return {}
        out = _docker("inspect", "-f", "{{.Name}} {{.HostConfig.Memory}}", *ids, timeout=60).stdout
        return {n.lstrip("/"): int(m) // 2**20 for n, m in (l.split() for l in out.splitlines() if l.strip()) if m.isdigit() and int(m) > 0}
    except Exception:                                          # noqa: BLE001
        return {}


_cs = {"at": 0.0, "states": {}}


def container_states(max_age=2.0):
    """container name -> state in libvirt's words, from one `docker ps -a` (cached for max_age seconds)."""
    if time.time() - _cs["at"] < max_age: return _cs["states"]
    try:
        r = _docker("ps", "-a", "--format", "{{.Names}}\t{{.State}}")
        if r.returncode == 0:
            _cs.update(at=time.time(), states={n: DOCKER_STATES.get(st, st) for n, st in (l.split("\t", 1) for l in r.stdout.splitlines() if "\t" in l)})
    except Exception:                                          # noqa: BLE001 — no Docker on the host is fine
        pass
    return _cs["states"]


def container_rss():
    """container name -> memory it holds (MiB): its cgroup's memory.current (systemd cgroup driver, cgroup v2)."""
    out = {}
    try:
        r = _docker("ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}")
        for line in r.stdout.splitlines():
            cid, name = line.split(None, 1)
            for p in (f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/memory.current", f"/sys/fs/cgroup/docker/{cid}/memory.current"):
                try: out[name] = int(Path(p).read_text()) // 2**20; break
                except OSError: continue
    except Exception:                                          # noqa: BLE001
        pass
    return out


# ---- what VMs each lab has (static: from lab.sh status, cached) and what state they are in (live: one virsh call) -----
NODES_TTL = 600
_nodes = {}                                     # lab dir -> (time, [(node, role, domain)], source)
_live = {"at": 0.0, "states": {}}


def _nodes_from_status(lab_dir):
    try:
        out = subprocess.run([str(Path(lab_dir) / "lab.sh"), "status"], capture_output=True, text=True, timeout=60).stdout
    except Exception:                                          # noqa: BLE001
        return []
    vm_col, rows = None, []
    for line in out.splitlines():
        cols = line.split()
        if cols[:1] == ["NODE"]:
            vm_col = cols.index("VM") if "VM" in cols else None
            continue
        m = re.match(r"^(\S+)\s+(\S+)\s+(running|shut off|undefined|paused|crashed|in shutdown)\b", line)
        if m:
            rest = line[m.end(3):].split()
            dom = rest[vm_col - 3] if vm_col is not None and len(rest) > vm_col - 3 else m[1]
            rows.append((m[1], m[2], dom))
    return rows


def _nodes_from_labconf(lab_dir):
    """Fallback for a lab whose `lab.sh status` lists nothing: its ROLE table in lab.conf (node -> role); the domain is
    taken to be the node's name, as in labs that do not prefix their domains."""
    try:
        conf = (Path(lab_dir) / "lab.conf").read_text()
    except Exception:                                          # noqa: BLE001
        return []
    m = re.search(r"^\s*declare -A ROLE=\((.*?)\)", conf, re.S | re.M)
    if not m: return []
    body = "\n".join(l.split("#", 1)[0] for l in m[1].splitlines())
    return [(n, r, n) for n, r in re.findall(r"\[([^\]]+)\]=(\S+)", body)]


def lab_nodes(lab_dir, refresh=False):
    """[(node, role, libvirt domain)] of a lab, and where it came from; cached NODES_TTL (they change only on a rebuild)."""
    hit = _nodes.get(lab_dir)
    if hit and not refresh and time.time() - hit[0] < NODES_TTL:
        return hit[1], hit[2]
    rows, source = _nodes_from_status(lab_dir), "lab.sh status"
    if not rows:
        rows, source = _nodes_from_labconf(lab_dir), "lab.conf (lab.sh status lists no VMs)"
    _nodes[lab_dir] = (time.time(), rows, source)
    return rows, source


def live_states(max_age=2.0):
    """domain -> libvirt state for every VM on the host, from one `virsh list --all` (about 40 ms), and every Docker
    container (one `docker ps -a`) in the same words."""
    if time.time() - _live["at"] < max_age:
        return _live["states"]
    try:
        r = SHARED.virsh("list", "--all", timeout=20)
        states = {}
        for line in r.stdout.splitlines():
            parts = line.split(None, 2)                       # " Id   Name   State" — the state can be two words
            if len(parts) == 3: states[parts[1]] = parts[2].strip()
        if r.returncode == 0:
            _live.update(at=time.time(), states={**container_states(), **states})
    except Exception:                                          # noqa: BLE001
        pass
    if not _live["states"]:                                    # no libvirt answer: the containers alone
        cs = container_states()
        if cs: _live.update(at=time.time(), states=cs)
    return _live["states"]


def invalidate(lab_dir=None):
    """After a power operation or a rebuild: read the states (and, for that lab, its nodes) afresh."""
    _live["at"] = 0.0; _cs["at"] = 0.0
    if lab_dir: _nodes.pop(lab_dir, None); _domains.pop(lab_dir, None); _totals.pop(lab_dir, None)


def vm_states(lab_dir):
    """node -> {role, state, domain}, for the hub's cards — the same shape lab.sh status used to give."""
    live = live_states(); rows, _ = lab_nodes(lab_dir)
    return {n: {"role": r, "state": live.get(dom, "undefined"), "domain": dom} for n, r, dom in rows}


def lab_vms(lab_dir):
    """[(node, domain, state)]."""
    live = live_states(); rows, _ = lab_nodes(lab_dir)
    return [(n, dom, live.get(dom, "undefined")) for n, _r, dom in rows]


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
    unreadable = not vms                                       # neither lab.sh status nor lab.conf listed VMs: unknown, not zero
    return {"vms_to_start": len(to_start), "need_gib": round(need, 1), "available_gib": round(avail, 1),
            "reserve_gib": RESERVE_GIB, "fits": need <= usable and not unreadable, "unreadable": unreadable, "unknown_sizes": unknown,
            "largest": sorted(((v[0], round(sizes.get(v[1], 0) / 1024, 1)) for v in to_start), key=lambda x: -x[1])[:5]}


_totals = {}
_domains = {}                                   # lab dir -> (time, [domain names])


def lab_domains(lab_dir):
    """The libvirt domains of a lab (cached with its total)."""
    hit = _domains.get(lab_dir)
    if hit and time.time() - hit[0] < 600: return hit[1]
    doms = [dom for _, _r, dom in lab_nodes(lab_dir)[0]]
    _domains[lab_dir] = (time.time(), doms)
    return doms


def qemu_rss():
    """libvirt domain -> resident memory of its QEMU process (MiB): what a running VM really holds, not its maximum; and
    each running container -> its cgroup's memory."""
    out = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit(): continue
        try:
            if not (p / "comm").read_text().startswith("qemu"): continue
            m = re.search(rb"-name\0guest=([^,\0]+)", (p / "cmdline").read_bytes())
            rss = re.search(r"VmRSS:\s+(\d+) kB", (p / "status").read_text())
            if m and rss: out[m[1].decode()] = int(rss[1]) // 1024
        except Exception:                                      # noqa: BLE001 — processes come and go
            pass
    out.update(container_rss())
    return out


def lab_total_gib(lab_dir):
    """Memory of every VM of the lab together (what it takes when fully up); cached for 10 minutes."""
    hit = _totals.get(lab_dir)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    sizes = _domain_sizes()
    doms = lab_domains(lab_dir)
    total = round(sum(sizes.get(dom, 0) for dom in doms) / 1024, 1)
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


def lab_unit(lab_dir):
    """What a lab's nodes are, for the page: "containers" when every one is a Docker container, else "VMs"."""
    rows, _ = lab_nodes(lab_dir)
    cs = container_states() if rows else {}
    return "containers" if rows and all(dom in cs for _n, _r, dom in rows) else "VMs"
