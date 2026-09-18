"""lab-mcp: the labs as tools for an AI operator (Model Context Protocol, stdio). Everything an engineer would reach for
when triaging — lab state, inventory, show commands on the routers and hosts, the portal's live tenant view, metrics
(VictoriaMetrics), syslog and flows (VictoriaLogs), alerts (Prometheus + vmalert-logs), Nautobot, rendered vs running
configuration, the test suites — read-only by default. Configuration changes need LAB_MCP_ALLOW_WRITE=1 and go through
`vyos_configure`, which records every change it makes.

Labs come from ~/.config/lab-hub/labs.json (name, dir, portal). Run:  lab-mcp   (Claude Code: see docs/ai-ops.md)."""
import json, os, re, subprocess, time
from pathlib import Path
import requests
from mcp.server.mcpserver import MCPServer

LABS = {l["name"]: l for l in json.loads(Path(os.environ.get("LAB_HUB_LABS", os.path.expanduser("~/.config/lab-hub/labs.json"))).read_text())}
VM, VL, PROM, VMALERT_LOGS, NAUTOBOT = (os.environ.get("VICTORIAMETRICS_URL", "http://10.0.0.10:8428"), os.environ.get("VICTORIALOGS_URL", "http://10.0.0.10:9428"),
                                      os.environ.get("PROMETHEUS_URL", "http://10.0.0.10:9090"), os.environ.get("VMALERT_LOGS_URL", "http://10.0.0.10:8880"), os.environ.get("NAUTOBOT_URL", "http://10.0.0.10:8080"))
ALLOW_WRITE = os.environ.get("LAB_MCP_ALLOW_WRITE") == "1"
AUDIT = Path(os.environ.get("LAB_MCP_AUDIT", os.path.expanduser("~/.config/lab-hub/mcp-audit.jsonl")))
mcp = MCPServer("lab-mcp", instructions="Tools over the network labs on this host (default lab: srv6-core). Start with lab_status / portal_state / alerts; use vyos_show for router state, logs_query for syslog, flows_query for sFlow, metrics_query for time series. Configuration changes are refused unless the server runs with LAB_MCP_ALLOW_WRITE=1.")

_inv_cache = {}


def lab_dir(lab): return Path(LABS[lab]["dir"])
def py(lab): return str(lab_dir(lab) / "tests" / ".venv" / "bin" / "python")


def inventory(lab):
    if lab not in _inv_cache or time.time() - _inv_cache[lab][0] > 60:
        _inv_cache[lab] = (time.time(), json.loads(subprocess.run([str(lab_dir(lab) / "lab.sh"), "inventory"], capture_output=True, text=True, check=True).stdout))
    return _inv_cache[lab][1]


def node(lab, name):
    n = next((x for x in inventory(lab)["nodes"] if x["name"] == name), None)
    if n is None: raise ValueError(f"{name}: no such node in {lab} (nodes: {', '.join(x['name'] for x in inventory(lab)['nodes'])})")
    return n


def sh(cmd, timeout=120, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd); return (r.stdout + r.stderr).strip()


def audit(tool, **kw):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a") as f: f.write(json.dumps({"time": time.time(), "tool": tool, **kw}) + "\n")


# ---- lab state --------------------------------------------------------------------------------------------------------
@mcp.tool(description="Which labs exist, their directories and portals, and which VMs are running.")
def labs() -> dict:
    out = {}
    for name, l in LABS.items():
        st = sh([str(Path(l["dir"]) / "lab.sh"), "status"], timeout=60); out[name] = {"dir": l["dir"], "portal": l.get("portal"), "status": st[:4000]}
    return out


@mcp.tool(description="The lab's inventory: every node (role, dc, OOB address, loopback, locator, AS, RDs) and every link with both addresses. The map you triage against.")
def lab_inventory(lab: str = "srv6-core") -> dict: return inventory(lab)


@mcp.tool(description="lab.sh status: VM states and the wiring with addresses.")
def lab_status(lab: str = "srv6-core") -> str: return sh([str(lab_dir(lab) / "lab.sh"), "status"], timeout=60)


# ---- routers and hosts -----------------------------------------------------------------------------------------------------
SHOW_OK = re.compile(r"^(show|ping|traceroute|monitor|mtr)\b")
SHELL_OK = re.compile(r"^(ip|ss|cat /proc/net|vtysh -c ['\"]show|sudo vtysh -c ['\"]show|sudo ip|sudo ss|sudo nft list|sudo tcpdump|sudo timeout \d+ tcpdump|birdc|systemctl status|journalctl|sudo journalctl|nstat|ethtool|bridge)\b")


@mcp.tool(description="Run an operational-mode command on a VyOS node (show ..., ping, traceroute, monitor). Examples: 'show ip bgp vrf tenant-a summary', 'show isis neighbor', 'show bfd peers brief', 'show bgp ipv4 vpn', 'show ipv6 route vrf tenant-a static', 'show segment-routing srv6 locator'.")
def vyos_show(node_name: str, command: str, lab: str = "srv6-core") -> str:
    if not SHOW_OK.match(command.strip()): return f"refused: only show / ping / traceroute / monitor commands ({command!r})"
    n = node(lab, node_name); audit("vyos_show", lab=lab, node=node_name, command=command)
    return sh([py(lab), str(lab_dir(lab) / "tools" / "vyos_cmd.py"), n["mgmt_ip"], command], timeout=180)


@mcp.tool(description="Run a read-only Linux shell command on a VyOS node: ip / ss / nstat / ethtool / vtysh -c 'show ...' / sudo tcpdump (use 'sudo timeout 10 tcpdump -ni eth1 -c 5 ...'). Examples: 'ip -6 route show | grep seg6local', 'ip route show vrf tenant-a', 'sudo ip -s -6 route show', 'nstat -az Ip6OutNoRoutes'.")
def vyos_shell(node_name: str, command: str, lab: str = "srv6-core") -> str:
    if not SHELL_OK.match(command.strip()): return f"refused: read-only shell commands only ({command!r})"
    n = node(lab, node_name); audit("vyos_shell", lab=lab, node=node_name, command=command)
    import paramiko
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(n["mgmt_ip"], username="vyos", password="vyos", timeout=20, look_for_keys=False, allow_agent=False)
    try:
        _, o, e = c.exec_command(command, timeout=120); return (o.read().decode() + e.read().decode()).strip()[:20000]
    finally: c.close()


@mcp.tool(description="Run a command on a tenant host (Alpine: ping, traceroute, ip, mtr, iperf3 -c). Example: host_command('dc1-h1', 'ping -c 3 172.20.3.2').")
def host_command(host_name: str, command: str, lab: str = "srv6-core") -> str:
    n = node(lab, host_name)
    if n["role"] != "host": return f"{host_name} is a {n['role']}, not a host"
    audit("host_command", lab=lab, host=host_name, command=command)
    return sh([py(lab), str(lab_dir(lab) / "tools" / "host_cmd.py"), "run", host_name, command], timeout=180)


@mcp.tool(description="Ping every tenant host from every other host (the reachability matrix): the fastest 'what is broken' check.")
def host_matrix(lab: str = "srv6-core") -> str: return sh([py(lab), str(lab_dir(lab) / "tools" / "host_cmd.py"), "matrix"], timeout=600)


# ---- portal, metrics, logs, flows, alerts ------------------------------------------------------------------------------------
@mcp.tool(description="The portal's live view: per tenant health (up / degraded / down), per site the PE-CE eBGP state, VRF and SRv6 route counts, host reachability, steering policies.")
def portal_state(lab: str = "srv6-core") -> dict:
    r = requests.get(LABS[lab]["portal"] + "/api/state", timeout=180); r.raise_for_status(); d = r.json()
    return {"tenants": [{"name": t["name"], "health": t.get("health"), "sites": [{k: s.get(k) for k in ("dc", "pe", "ce", "host", "lan", "attachment_circuit", "live")} for s in t["sites"]]} for t in d["tenants"]],
            "core_live": d.get("core_live"), "steering": d.get("steering"), "generated": d.get("generated")}


@mcp.tool(description="Instant PromQL query against VictoriaMetrics (everything: node/frr exporters, portal metrics lab_*, Telegraf, ALERTS). Examples: 'frr_bgp_peer_state{lab=\"srv6-core\"} != 1', 'lab_isis_adjacencies_up < lab_isis_adjacencies_expected', 'rate(nstat_Ip6OutNoRoutes[5m]) > 0'.")
def metrics_query(expr: str) -> list:
    r = requests.get(f"{VM}/api/v1/query", params={"query": expr}, timeout=60); r.raise_for_status(); d = r.json()
    if d["status"] != "success": return [{"error": d}]
    return [{"labels": x["metric"], "value": x["value"][1]} for x in d["data"]["result"]][:200]


@mcp.tool(description="Range PromQL query (start/end as 'now-30m' style offsets or epoch seconds; step like '30s'). For 'when did it change'.")
def metrics_range(expr: str, start: str = "now-30m", end: str = "now", step: str = "30s") -> list:
    conv = lambda t: time.time() - int(t[4:-1]) * {"s": 1, "m": 60, "h": 3600}[t[-1]] if t.startswith("now-") else (time.time() if t == "now" else float(t))
    r = requests.get(f"{VM}/api/v1/query_range", params={"query": expr, "start": conv(start), "end": conv(end), "step": step}, timeout=60); r.raise_for_status(); d = r.json()
    return [{"labels": x["metric"], "values": x["values"][-120:]} for x in d["data"]["result"]][:50]


@mcp.tool(description="LogsQL query against VictoriaLogs: the routers' syslog (fields hostname, app_name, severity, _msg) and the sFlow records (fields sampler_address, src_addr, dst_addr, proto, bytes, in_if, out_if). Examples: '_time:15m app_name:bgpd \"ADJCHANGE\"', '_time:1h hostname:pe3 severity:<=4', '_time:30m app_name:commit'. Returns up to `limit` records, newest last.")
def logs_query(query: str, limit: int = 50) -> list:
    if "_time:" not in query: query = "_time:1h " + query
    r = requests.get(f"{VL}/select/logsql/query", params={"query": f"{query} | sort by (_time) | limit {limit}"}, timeout=60); r.raise_for_status()
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


@mcp.tool(description="Aggregate the sFlow records: which source PE sends to which destination SID, seen by which router, over the last `minutes`. Shows the SRv6 paths actually carrying traffic (proto IPv6-Route = SRv6-encapsulated).")
def flows_query(minutes: int = 15, sampler: str = "", srv6_only: bool = True) -> list:
    q = f"_time:{minutes}m sampler_address:{sampler or '*'} " + ('proto:"IPv6-Route" ' if srv6_only else "") + "| stats by (sampler_address, src_addr, dst_addr, proto) sum(bytes) as sampled_bytes, count() as samples | sort by (sampled_bytes desc) | limit 40"
    r = requests.get(f"{VL}/select/logsql/query", params={"query": q}, timeout=60); r.raise_for_status()
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


@mcp.tool(description="Every alert currently firing or pending: Prometheus (metric rules) and vmalert-logs (syslog-derived rules).")
def alerts() -> dict:
    out = {}
    for name, url in (("prometheus", f"{PROM}/api/v1/alerts"), ("vmalert-logs", f"{VMALERT_LOGS}/api/v1/alerts")):
        try:
            d = requests.get(url, timeout=30).json()["data"]["alerts"]
            out[name] = [{"name": a.get("name") or a["labels"].get("alertname"), "state": a["state"], "labels": {k: v for k, v in a["labels"].items() if k != "alertname"}, "summary": a.get("annotations", {}).get("summary")} for a in d]
        except Exception as e:  # noqa: BLE001
            out[name] = f"unavailable: {e}"
    return out


@mcp.tool(description="Grafana annotations in the last `minutes`: what the portal, the tests and the steering tool did (runs, failover cuts, steering changes).")
def events(minutes: int = 60) -> list:
    from .. import grafana
    r = requests.get(f"{grafana.URL}/api/annotations", params={"from": int((time.time() - minutes * 60) * 1000), "to": int(time.time() * 1000), "limit": 100}, auth=grafana.AUTH, timeout=30); r.raise_for_status()
    return [{"time": a["time"] / 1000, "end": (a.get("timeEnd") or 0) / 1000 or None, "tags": a["tags"], "text": a["text"]} for a in r.json()]


# ---- source of truth and configuration ------------------------------------------------------------------------------------------
@mcp.tool(description="Read-only GraphQL against Nautobot (the source of truth). Example: '{ devices(name: \"pe1\") { name interfaces { name ip_addresses { address } connected_interface { name device { name } } } } }'.")
def nautobot_graphql(query: str) -> dict:
    tok = os.environ.get("NAUTOBOT_TOKEN") or sh(["bash", "-c", f"cd {lab_dir('srv6-core')} && ./lab.sh nautobot token"], timeout=60).splitlines()[-1].strip()
    if re.search(r"\bmutation\b", query, re.I): return {"error": "read-only"}
    r = requests.post(f"{NAUTOBOT}/api/graphql/", json={"query": query}, headers={"Authorization": f"Token {tok}"}, timeout=60); return r.json()


@mcp.tool(description="The configuration a node SHOULD have (rendered from lab.conf, identical to Nautobot's rendering when in sync): the set commands.")
def intended_config(node_name: str, lab: str = "srv6-core") -> str:
    node(lab, node_name); return (lab_dir(lab) / "nodes" / node_name / "vyos_config.txt").read_text()


@mcp.tool(description="Drift check: which intended set commands are missing from the node's running configuration, and which running lines are not intended (ignores hw-id, passwords, and lines the lab does not render). The first thing to run when a node misbehaves.")
def config_diff(node_name: str, lab: str = "srv6-core") -> dict:
    n = node(lab, node_name)
    if n["role"] not in ("pe", "p", "ce"): return {"error": "VyOS nodes only"}
    running = sh([py(lab), str(lab_dir(lab) / "tools" / "vyos_cmd.py"), n["mgmt_ip"], "show configuration commands"], timeout=180)
    norm = lambda l: re.sub(r"'", "", l.strip())
    run_lines = {norm(l) for l in running.splitlines() if l.startswith("set ")}
    want = [norm(l) for l in (lab_dir(lab) / "nodes" / node_name / "vyos_config.txt").read_text().splitlines() if l.startswith("set ") and "plaintext-password" not in l]
    missing = [l for l in want if l not in run_lines]
    prefixes = ("set interfaces ethernet", "set protocols", "set vrf", "set system sysctl", "set service", "set firewall", "set system syslog", "set system sflow")
    extra = sorted(l for l in run_lines if l.startswith(prefixes) and l not in set(want) and "hw-id" not in l and "encrypted-password" not in l and "public-keys" not in l and not l.startswith("set system syslog local"))
    return {"missing_from_running": missing, "not_intended": extra, "note": "'not_intended' lists lines inside the lab's own subtrees only"}


@mcp.tool(description="Run one or more Robot suites of the lab (e.g. ['05_end_to_end'], ['04_vpn','05_end_to_end']); returns pass/fail per test with messages. Slow (minutes); prefer targeted show commands first.")
def run_tests(suites: list[str], lab: str = "srv6-core") -> dict:
    audit("run_tests", lab=lab, suites=suites)
    paths = [f"suites/{s if s.endswith('.robot') else s + '.robot'}" for s in suites]
    out = sh([str(lab_dir(lab) / "lab.sh"), "test", *paths], timeout=3600)
    m = re.search(r"==> results: (\S+)", out); res = {}
    if m:
        import xml.etree.ElementTree as ET
        for tc in ET.parse(Path(m[1]) / "output.xml").iter("test"):
            st = tc.find("status"); res[tc.get("name")] = {"status": st.get("status"), "message": (st.text or "").strip()[:500]}
    return {"results": res, "summary": re.findall(r"^\d+ tests?, .*$", out, re.M)[-1:] , "results_dir": m[1] if m else None}


@mcp.tool(description="Apply VyOS configuration lines (set/delete) on a node and commit + save. REFUSED unless the server runs with LAB_MCP_ALLOW_WRITE=1. Every change is written to the audit log. Prefer proposing the fix to the operator.")
def vyos_configure(node_name: str, lines: list[str], lab: str = "srv6-core") -> str:
    if not ALLOW_WRITE: return "refused: configuration changes are disabled (LAB_MCP_ALLOW_WRITE=1 enables them). Proposed lines:\n" + "\n".join(lines)
    n = node(lab, node_name); audit("vyos_configure", lab=lab, node=node_name, lines=lines)
    import sys; sys.path.insert(0, str(lab_dir(lab) / "tests" / "resources"))
    from netmiko import ConnectHandler
    c = ConnectHandler(device_type="vyos", host=n["mgmt_ip"], username="vyos", password="vyos"); c.config_mode()
    out = c.send_config_set(lines, exit_config_mode=False); out += c.commit(); out += c.send_command_timing("save"); c.exit_config_mode(); c.disconnect()
    return out[-3000:]


def main(): mcp.run(transport="stdio")


if __name__ == "__main__": main()
