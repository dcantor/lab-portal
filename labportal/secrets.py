"""One place for the lab's credentials on the lab host: ~/.config/lab/secrets.env (mode 0600, directory 0700;
$LAB_SECRETS_FILE), KEY=VALUE lines. Tools read a credential with `secrets.get("GITEA_PASSWORD")`: the environment
first (so a CI job or a one-off override still works), then this file — never a hard-coded default, never a value on
a command line.

    lab-secrets list                  the names stored, where each is used, whether the file is private (values never shown)
    lab-secrets set NAME              store or change one (prompted, not echoed)
    lab-secrets unset NAME
    lab-secrets import-nms            copy the NMS's service credentials (Gitea, Nautobot API token) from its .env
    lab-secrets check                 permissions, and which known names are still missing"""
import getpass, os, re, stat, subprocess, sys
from pathlib import Path

FILE = Path(os.environ.get("LAB_SECRETS_FILE", Path.home() / ".config" / "lab" / "secrets.env"))
NMS = os.environ.get("LAB_NMS_SSH", "lab@10.0.0.10")
KNOWN = {   # name -> what uses it
    "GITEA_USER": "the Lab Hub's CI view, srv6-core tools/ci.py and backup_configs.py",
    "GITEA_PASSWORD": "the Lab Hub's CI view, srv6-core tools/ci.py and backup_configs.py",
    "GITEA_TOKEN": "Gitea API access without the password",
    "NAUTOBOT_TOKEN": "the labs' Nautobot seed / render tools",
    "VYOS_USERNAME": "SSH to the VyOS routers (portals, tests)",
    "VYOS_PASSWORD": "SSH to the VyOS routers (portals, tests)",
    "HOST_USERNAME": "SSH to the Alpine hosts (SLA probes, tests)",
    "HOST_PASSWORD": "SSH to the Alpine hosts (SLA probes, tests)",
}
_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _read():
    out = {}
    if FILE.exists():
        for line in FILE.read_text().splitlines():
            m = _LINE.match(line.strip())
            if m: out[m[1]] = m[2]
    return out


def _write(values):
    FILE.parent.mkdir(parents=True, exist_ok=True); os.chmod(FILE.parent, 0o700)
    tmp = FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# lab credentials — managed with lab-secrets; never commit, never paste\n")
        for k in sorted(values): f.write(f"{k}={values[k]}\n")
    os.replace(tmp, FILE); os.chmod(FILE, 0o600)


def get(name, default=None):
    """The credential from the environment, else from the store, else `default`."""
    return os.environ.get(name) or _read().get(name) or default


def private():
    """True if the store (and its directory) can be read by its owner only."""
    if not FILE.exists(): return True
    return not (FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)) and not (FILE.parent.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def import_nms():
    """Copy the service credentials the NMS keeps in /opt/nautobot/.env; values travel over SSH, never printed."""
    want = {"GITEA_USER": "GITEA_USER", "GITEA_PASSWORD": "GITEA_PASSWORD", "GITEA_TOKEN": "GITEA_TOKEN",
            "NAUTOBOT_TOKEN": "NAUTOBOT_SUPERUSER_API_TOKEN"}
    r = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                        "-o", "ConnectTimeout=8", NMS, "cat /opt/nautobot/.env"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0: raise RuntimeError(f"could not read the NMS's .env ({NMS}): is the NMS running?")
    env = {m[1]: m[2] for line in r.stdout.splitlines() if (m := _LINE.match(line.strip()))}
    vals = _read(); got = []
    for ours, theirs in want.items():
        if env.get(theirs):
            vals[ours] = env[theirs]; got.append(ours)
    _write(vals); return got


def cli():
    args = sys.argv[1:] or ["list"]
    cmd = args[0]
    if cmd == "list":
        vals = _read()
        print(f"{FILE} ({'private' if private() else 'READABLE BY OTHERS — run: lab-secrets check'})")
        for k in sorted(set(vals) | set(KNOWN)):
            print(f"  {'✓' if k in vals else '·'} {k:16s} {KNOWN.get(k, '')}")
    elif cmd == "set" and len(args) == 2:
        if not _LINE.match(f"{args[1]}=x"): sys.exit("names are UPPER_CASE")
        v = getpass.getpass(f"{args[1]}: ")
        if not v: sys.exit("empty — nothing changed")
        vals = _read(); vals[args[1]] = v; _write(vals); print(f"{args[1]} stored")
    elif cmd == "unset" and len(args) == 2:
        vals = _read(); vals.pop(args[1], None); _write(vals); print(f"{args[1]} removed")
    elif cmd == "import-nms":
        print("imported from the NMS: " + ", ".join(import_nms()))
    elif cmd == "check":
        if FILE.exists() and not private():
            os.chmod(FILE, 0o600); os.chmod(FILE.parent, 0o700); print("permissions tightened to 0600 / 0700")
        missing = [k for k in KNOWN if k not in _read()]
        print("private: yes" if private() else "private: NO"); print("missing: " + (", ".join(missing) or "none"))
    else:
        sys.exit(__doc__)
