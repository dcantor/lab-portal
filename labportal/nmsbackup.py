"""Back up the NMS — the one VM every lab depends on — to the lab host, and prove each backup can be restored.

    lab-nms-backup              take a backup now (a systemd user timer runs it nightly: nms/lab-nms-backup.timer)
    lab-nms-backup --list       the backups kept, newest first, with what each verified

What is taken (one directory per backup, ~/backups/nms/nms-<time>/, mode 0700 — it holds the NMS's .env):
    nautobot.pgdump         pg_dump -Fc of Nautobot's database (devices, IPs, VRFs, peerings, jobs, users, tokens)
    nautobot-media.tar.gz   Nautobot's media volume;  nautobot-git.tar.gz  its Git-repository volume
    gitea-dump.tar.gz       `gitea dump`: every repository (the CI mirror, the config backups), Gitea's database, app.ini
    grafana.db              Grafana's SQLite database, copied online (users, annotations, preferences; the dashboards
                            themselves are provisioned from lab-portal/monitoring)
    nms-config.tar.gz       /opt/nautobot (compose file, Dockerfile, nautobot_config.py, .env) and /opt/monitoring
    manifest.json           when, sizes, SHA-256 of every file, the versions, and the restore checks below
Not taken: the metrics and log stores (Prometheus, VictoriaMetrics, VictoriaLogs) — large, and losing them loses
history, not configuration.

Every backup is checked by restoring it, without touching the live services' data:
    the database dump is restored into a scratch database (nautobot_restore_check) in the same PostgreSQL, its devices,
    IP addresses and VRFs counted, and the scratch database dropped; the Gitea dump is listed (database + repositories);
    the Grafana copy passes SQLite's integrity check and its dashboards and annotations are counted.
KEEP backups are kept (default 14). Credentials never leave the NMS except inside the backup itself."""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

NMS = os.environ.get("LAB_NMS_SSH", "lab@10.0.0.10")
DEST = Path(os.environ.get("LAB_NMS_BACKUPS", Path.home() / "backups" / "nms"))
KEEP = int(os.environ.get("LAB_NMS_KEEP", "14"))
SSH = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10", NMS]

REMOTE = r'''
set -euo pipefail
W=$(mktemp -d /tmp/nmsbackup.XXXXXX); chmod 700 "$W"; echo "WORKDIR $W"
V=/var/lib/docker/volumes
# Nautobot: the database, the media and git volumes
docker exec nautobot-postgres-1 sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$W/nautobot.pgdump"
sudo -n tar -C $V/nautobot_nautobot_media/_data -czf "$W/nautobot-media.tar.gz" .
sudo -n tar -C $V/nautobot_nautobot_git/_data -czf "$W/nautobot-git.tar.gz" .
# Gitea: its own dump (repositories + database + config)
docker exec -u git nautobot-gitea-1 sh -c 'cd /tmp && rm -f gitea-dump.tar.gz && gitea dump -c /data/gitea/conf/app.ini --type tar.gz -f /tmp/gitea-dump.tar.gz >/tmp/gitea-dump.log 2>&1'
docker cp nautobot-gitea-1:/tmp/gitea-dump.tar.gz "$W/gitea-dump.tar.gz" >/dev/null
docker exec nautobot-gitea-1 rm -f /tmp/gitea-dump.tar.gz /tmp/gitea-dump.log
# Grafana: an online copy of its SQLite database
sudo -n python3 - "$W/grafana.db" <<'PY'
import sqlite3, sys, os
src = sqlite3.connect("file:/var/lib/docker/volumes/monitoring_grafana_data/_data/grafana.db?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close(); os.chown(sys.argv[1], 1000, 1000)
PY
# the deployment itself
sudo -n tar -C /opt -czf "$W/nms-config.tar.gz" --exclude=monitoring/flows nautobot/docker-compose.yml nautobot/Dockerfile nautobot/nautobot_config.py nautobot/.env monitoring
sudo -n chown -R "$(id -u):$(id -g)" "$W"; chmod 600 "$W"/*
# ---- restore checks, on scratch copies only ----
docker exec nautobot-postgres-1 sh -c 'dropdb --if-exists -U "$POSTGRES_USER" nautobot_restore_check && createdb -U "$POSTGRES_USER" nautobot_restore_check'
docker exec -i nautobot-postgres-1 sh -c 'pg_restore --no-owner -U "$POSTGRES_USER" -d nautobot_restore_check' < "$W/nautobot.pgdump" 2> "$W/restore.err" || true
PG=$(docker exec -i nautobot-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d nautobot_restore_check -tA' 2>/dev/null <<'SQL' | tr -d '\n' || echo error
select concat_ws(',', (select count(*) from dcim_device), (select count(*) from ipam_ipaddress), (select count(*) from ipam_vrf),
                 (select count(*) from information_schema.tables where table_schema = current_schema()));
SQL
)
docker exec nautobot-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" nautobot_restore_check'
RERR=$(grep -c "error" "$W/restore.err" || true); rm -f "$W/restore.err"
GT=$(tar -tzf "$W/gitea-dump.tar.gz" | awk '/gitea-db.sql$/ {db=1} /^repos\/[^\/]+\/[^\/]+\.git\/HEAD$/ {r++} END {print (db?1:0)","r+0}')
GF=$(python3 - "$W/grafana.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); q = lambda sql: c.execute(sql).fetchone()[0]
def safe(sql):
    try: return q(sql)
    except sqlite3.Error: return 0
dash = max(safe("select count(*) from dashboard where is_folder = 0"), safe("select count(*) from resource where \"group\" like 'dashboard%' and resource = 'dashboards'"))
print(f"{q('pragma integrity_check')},{dash},{q('select count(*) from annotation')},{q('select count(*) from user')}")
PY
)
VERS=$(for c in nautobot-nautobot-1 nautobot-gitea-1 monitoring-grafana-1 nautobot-postgres-1; do docker inspect -f '{{.Config.Image}}' $c; done | paste -sd'|')
echo "CHECK pg=$PG restore_errors=$RERR gitea=$GT grafana=$GF versions=$VERS"
'''


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def backup():
    t0 = time.time(); name = time.strftime("nms-%Y%m%d-%H%M%S")
    DEST.mkdir(parents=True, exist_ok=True); os.chmod(DEST, 0o700)
    tmp = DEST / f".{name}.partial"; tmp.mkdir(mode=0o700); work = None
    try:
        r = subprocess.run(SSH + ["bash", "-s"], input=REMOTE, capture_output=True, text=True, timeout=1800)
        out = r.stdout + r.stderr
        work = next((l.split()[1] for l in out.splitlines() if l.startswith("WORKDIR ")), None)
        check = next((l for l in out.splitlines() if l.startswith("CHECK ")), None)
        if r.returncode != 0 or not work or not check or not re.match(r"CHECK pg=\d+,\d+,\d+,\d+ ", check):
            raise RuntimeError(f"the backup on the NMS failed (rc {r.returncode}): {(check or out.strip())[-600:]}")
        pull = subprocess.run(SSH + [f"tar -C {work} -cf - . && rm -rf {work}"], capture_output=True, timeout=1800)
        if pull.returncode != 0: raise RuntimeError(f"copying the backup from the NMS failed: {pull.stderr.decode()[-300:]}")
        subprocess.run(["tar", "-C", str(tmp), "-xf", "-"], input=pull.stdout, check=True)
        kv = dict(re.findall(r"(\w+)=(.*?)(?= \w+=|$)", check[len("CHECK "):]))
        pg = kv["pg"].split(","); gt = kv["gitea"].split(","); gf = kv["grafana"].split(","); vers = kv["versions"].split("|")
        checks = {"nautobot": {"ok": len(pg) == 4 and int(pg[3]) > 50 and kv["restore_errors"] == "0",
                               "devices": int(pg[0]) if pg[0].isdigit() else None, "ip_addresses": int(pg[1]) if len(pg) > 1 and pg[1].isdigit() else None,
                               "vrfs": int(pg[2]) if len(pg) > 2 and pg[2].isdigit() else None, "tables": int(pg[3]) if len(pg) > 3 and pg[3].isdigit() else None,
                               "restore_errors": int(kv["restore_errors"])},
                  "gitea": {"ok": gt[0] == "1" and int(gt[1]) > 0, "database": gt[0] == "1", "repositories": int(gt[1])},
                  "grafana": {"ok": gf[0] == "ok", "integrity": gf[0], "dashboards": int(gf[1]), "annotations": int(gf[2]), "users": int(gf[3])}}
        files = {f.name: {"bytes": f.stat().st_size, "sha256": _sha(f)} for f in sorted(tmp.iterdir())}
        manifest = {"name": name, "nms": NMS, "taken": t0, "seconds": round(time.time() - t0, 1), "files": files,
                    "bytes": sum(v["bytes"] for v in files.values()), "checks": checks, "ok": all(c["ok"] for c in checks.values()),
                    "versions": dict(zip(["nautobot", "gitea", "grafana", "postgres"], vers))}
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1)); os.chmod(tmp / "manifest.json", 0o600)
        final = DEST / name; tmp.rename(final)
        (DEST / "latest.json").write_text(json.dumps({k: manifest[k] for k in ("name", "taken", "seconds", "bytes", "checks", "ok")}, indent=1))
        prune()
        return manifest
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if work:                                               # never leave a copy of the NMS's secrets in its /tmp
            subprocess.run(SSH + [f"rm -rf {work}"], capture_output=True, timeout=60)
        (DEST / "latest.json").write_text(json.dumps({"name": name, "taken": t0, "ok": False, "error": str(e)[-600:]}, indent=1))
        raise


def prune():
    for old in sorted(p for p in DEST.glob("nms-*") if p.is_dir())[:-KEEP]:
        shutil.rmtree(old)


def listing():
    out = []
    for d in sorted((p for p in DEST.glob("nms-*") if p.is_dir()), reverse=True):
        try: m = json.loads((d / "manifest.json").read_text()); out.append(m)
        except Exception: out.append({"name": d.name, "ok": False, "error": "no manifest"})       # noqa: BLE001
    return out


def latest():
    """The last attempt (success or failure), and how many backups are kept — for the Lab Hub."""
    try: last = json.loads((DEST / "latest.json").read_text())
    except Exception: last = None                              # noqa: BLE001
    return {"last": last, "kept": len([p for p in DEST.glob("nms-*") if p.is_dir()]), "dir": str(DEST)}


def main():
    a = argparse.ArgumentParser(description="Back up the NMS to the lab host").parse_known_args()
    if "--list" in sys.argv:
        for m in listing():
            c = m.get("checks", {})
            print(f"{m['name']}  {'ok    ' if m.get('ok') else 'FAILED'}  {round(m.get('bytes', 0) / 2**20, 1):6} MiB  "
                  + (f"nautobot {c['nautobot']['devices']} devices / {c['nautobot']['ip_addresses']} IPs · gitea {c['gitea']['repositories']} repos · "
                     f"grafana {c['grafana']['dashboards']} dashboards / {c['grafana']['annotations']} annotations" if c else m.get("error", "")))
        return
    m = backup(); c = m["checks"]
    print(f"{m['name']}: {'verified' if m['ok'] else 'TAKEN, BUT A CHECK FAILED'} in {m['seconds']} s, {round(m['bytes'] / 2**20, 1)} MiB → {DEST / m['name']}")
    print(f"  nautobot: restored into a scratch database: {c['nautobot']['devices']} devices, {c['nautobot']['ip_addresses']} IP addresses, "
          f"{c['nautobot']['vrfs']} VRFs, {c['nautobot']['tables']} tables, {c['nautobot']['restore_errors']} restore errors")
    print(f"  gitea:    database {'present' if c['gitea']['database'] else 'MISSING'}, {c['gitea']['repositories']} repositories")
    print(f"  grafana:  integrity {c['grafana']['integrity']}, {c['grafana']['dashboards']} dashboards, {c['grafana']['annotations']} annotations")
    if not m["ok"]: sys.exit(1)
