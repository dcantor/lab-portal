#!/usr/bin/env python3
"""Write srv6-xr.yml — gnmic's configuration for streaming telemetry from srv6-xr's IOS-XR PE (xr-pe1) over gNMI — from the
lab's inventory (`lab.sh inventory`). XR's gRPC server is on the lab's isolated OOB network, so gnmic dials the host's
relay (10.0.0.1:<gnmi_relay_port>, the srv6-xr-gnmi-relay unit) instead. The subscriptions sample every 10 s:

  iface   OpenConfig interface state and counters        xr_iface_oper_up, xr_iface_in_octets, ... {interface, peer, tenant}
  isis    IS-IS adjacencies (Cisco native)                xr_isis_adjacency_up {interface, peer}
  bgp     VPNv4 / VPNv6 sessions to the reflector         xr_bgp_established, xr_bgp_prefixes_accepted / _advertised {peer, family}
  bfd     single-hop BFD sessions on the core links       xr_bfd_session_up {interface, peer}
  te      SR policies                                     xr_te_admin_up, xr_te_oper_up {policy, color, endpoint}
  system  CPU and memory of each card                     xr_system_cpu_1m / _5m, xr_system_memory_free_bytes / _total_bytes {location}

Interfaces with no link in the lab (Null0, SINT0, Loopback0, spare ports) are left out.

XR does not report a down IS-IS adjacency or BFD session as down: it drops it from the list. So a series that stops
coming is gone after 30 s (the exporter's expiration), and the dashboard and alerts compare them with the core ports
(xr_iface_oper_up with a P router as peer): a core port with no adjacency is one that is down. XR's native models name a neighbour by its system id, its link-local address or its interface: a Starlark processor
(below) puts the lab's own names on every series (peer=p1, policy=VIA-P3), turns states into numbers (up = 1) and keeps
only the leaves above. Run after a change to srv6-xr's lab.conf, then ../deploy.sh. SRV6_XR_DIR overrides ~/srv6-xr."""
import ipaddress, json, os, subprocess
from pathlib import Path
import yaml

LAB = Path(os.environ.get("SRV6_XR_DIR", Path.home() / "srv6-xr"))
SAMPLE = "10s"
RELAY = "10.0.0.1"                                   # the host's address on the NMS network
SUBSCRIPTIONS = {
    "iface": ["openconfig-interfaces:/interfaces/interface/state/oper-status",
              "openconfig-interfaces:/interfaces/interface/state/counters"],
    "isis": ["Cisco-IOS-XR-clns-isis-oper:/isis/instances/instance/neighbors/neighbor/neighbor-state"],
    "bgp": ["Cisco-IOS-XR-ipv4-bgp-oper:/bgp/instances/instance/instance-active/default-vrf/afs/af/neighbor-af-table/neighbor"],
    "bfd": ["Cisco-IOS-XR-ip-bfd-oper:/bfd/ipv6-single-hop-session-briefs/ipv6-single-hop-session-brief/state"],
    "te": ["Cisco-IOS-XR-infra-xtc-agent-oper:/xtc/policies/policy/administrative-up",
           "Cisco-IOS-XR-infra-xtc-agent-oper:/xtc/policies/policy/operational-up",
           "Cisco-IOS-XR-infra-xtc-agent-oper:/xtc/policies/policy/color",
           "Cisco-IOS-XR-infra-xtc-agent-oper:/xtc/policies/policy/destination-address"],
    "system": ["Cisco-IOS-XR-wdsysmon-fd-oper:/system-monitoring/cpu-utilization/total-cpu-one-minute",
               "Cisco-IOS-XR-wdsysmon-fd-oper:/system-monitoring/cpu-utilization/total-cpu-five-minute",
               "Cisco-IOS-XR-nto-misc-oper:/memory-summary/nodes/node/summary/free-physical-memory",
               "Cisco-IOS-XR-nto-misc-oper:/memory-summary/nodes/node/summary/system-ram-memory"],
}

# The Starlark processor. gnmic hands it each sample as events (tags: the list keys, values: full path -> value) and
# exports what it returns; the maps are filled in from the inventory (the %s below).
STARLARK = '''
PEER_BY_IF = %(peer_by_if)s
TENANT_BY_IF = %(tenant_by_if)s
NAME_BY_SYSID = %(name_by_sysid)s
NAME_BY_ADDR = %(name_by_addr)s
POLICY_BY_COLOR = %(policy_by_color)s
UP = ["UP", "isis-adj-up-state", "bgp-st-estab", "bfd-mgmt-session-state-up"]
IFACE = {"in-octets": "in_octets", "out-octets": "out_octets", "in-pkts": "in_pkts", "out-pkts": "out_pkts",
         "in-errors": "in_errors", "out-errors": "out_errors", "in-discards": "in_discards", "out-discards": "out_discards"}
SYSTEM = {"total-cpu-one-minute": "cpu_1m", "total-cpu-five-minute": "cpu_5m",
          "free-physical-memory": "memory_free_bytes", "system-ram-memory": "memory_total_bytes"}

def leaf(k):
    return k.split("/")[-1]

def tag(e, suffix):
    for k, v in e.tags.items():
        if k.endswith(suffix):
            return v
    return ""

def up(v):
    return 1.0 if v in UP else 0.0

def apply(*events):
    out = []
    for e in events:
        sub = e.tags.get("subscription-name", "")
        t = {"lab": %(lab)r, "node": %(node)r, "subscription-name": sub}
        vals = {}
        if sub == "iface":
            i = tag(e, "interface_name")
            t["interface"] = i
            t["peer"] = PEER_BY_IF.get(i, "")
            t["tenant"] = TENANT_BY_IF.get(i, "")
            if not t["peer"]:
                continue                                  # Null0, SINT0, the loopback, ports with no link
            for k, v in e.values.items():
                n = leaf(k)
                if n == "oper-status":
                    vals["oper_up"] = up(v)
                elif n in IFACE and "/counters/" + n in k:
                    vals[IFACE[n]] = float(v)
        elif sub == "isis":
            i = tag(e, "interface-name")
            t["interface"] = i
            t["peer"] = NAME_BY_SYSID.get(tag(e, "system-id"), tag(e, "system-id"))
            for k, v in e.values.items():
                if leaf(k) == "neighbor-state":
                    vals["adjacency_up"] = up(v)
        elif sub == "bgp":
            af = tag(e, "af-name")
            fam = af.split("-")[0]
            a = tag(e, "neighbor-address")
            t["peer"] = NAME_BY_ADDR.get(a, a)
            t["family"] = fam
            idx = ""
            for k, v in e.values.items():
                if k.endswith("/af-name") and "/af-data." in k and v == fam:
                    idx = k.split("/af-data.")[1].split("/")[0]
            for k, v in e.values.items():
                n = leaf(k)
                if n == "connection-state" and "/af-data." not in k:
                    vals["established"] = up(v)
                elif idx and "/af-data." + idx + "/" in k and n in ("prefixes-accepted", "prefixes-advertised"):
                    vals[n.replace("-", "_")] = float(v)
        elif sub == "bfd":
            i = tag(e, "interface-name")
            t["interface"] = i
            t["peer"] = PEER_BY_IF.get(i, "")
            for k, v in e.values.items():
                if leaf(k) == "state":
                    vals["session_up"] = up(v)
        elif sub == "te":
            c = ""
            for k, v in e.values.items():
                n = leaf(k)
                if n == "color":
                    c = str(int(float(v)))
                elif n == "administrative-up":
                    vals["admin_up"] = float(v)
                elif n == "operational-up":
                    vals["oper_up"] = float(v)
                elif n == "ipv6" and "/destination-address/" in k:
                    t["endpoint"] = NAME_BY_ADDR.get(v, v)
            if not c:
                continue
            t["color"] = c
            t["policy"] = POLICY_BY_COLOR.get(c, "")
        elif sub == "system":
            t["location"] = tag(e, "node-name")           # 0/RP0/CPU0 (the route processor), 0/0/CPU0 (the line card)
            for k, v in e.values.items():
                n = leaf(k)
                if n in SYSTEM:
                    vals[SYSTEM[n]] = float(v)
        if vals:
            e.tags.clear()
            e.tags.update(t)
            e.values.clear()
            e.values.update(vals)
            out.append(e)
    return out
'''


def inventory():
    env = {k: v for k, v in os.environ.items() if k != "FORCE_COLOR"}
    return json.loads(subprocess.run([str(LAB / "lab.sh"), "inventory"], capture_output=True, text=True, check=True, env=env).stdout)


def config(inv):
    xr = next(n for n in inv["nodes"] if n["os"] == "iosxr")
    sysid = lambda net: ".".join(net.split(".")[-4:-1])          # 49.0001.0000.0000.0011.00 -> 0000.0000.0011
    peer_by_if = {p["name"]: p["peer"] for p in xr["ports"] if p.get("peer")} | {"MgmtEth0/RP0/CPU0/0": "oob"}
    maps = {
        "lab": inv["lab"], "node": xr["name"],
        "peer_by_if": json.dumps(peer_by_if, sort_keys=True),
        "tenant_by_if": json.dumps({p["name"]: p["tenant"] for p in xr["ports"] if p.get("tenant")}, sort_keys=True),
        "name_by_sysid": json.dumps({sysid(n["isis_net"]): n["name"] for n in inv["nodes"] if n.get("isis_net")}, sort_keys=True),
        "name_by_addr": json.dumps({str(ipaddress.ip_address(n["loopback6"])): n["name"] for n in inv["nodes"] if n.get("loopback6")}, sort_keys=True),
        "policy_by_color": json.dumps({str(te["color"]): te["policy"] for te in inv["te"]}, sort_keys=True),
    }
    u, pw = inv["credentials"]["iosxr"]
    return {
        "username": u, "password": pw, "insecure": True, "encoding": "json_ietf", "log": True,
        "targets": {xr["name"]: {"address": f"{RELAY}:{inv['gnmi_relay_port']}"}},
        "subscriptions": {k: {"paths": v, "mode": "stream", "stream-mode": "sample", "sample-interval": SAMPLE} for k, v in SUBSCRIPTIONS.items()},
        "processors": {"lab-names": {"event-starlark": {"source": STARLARK % maps}}},
        "outputs": {"prom": {"type": "prometheus", "listen": ":9805", "path": "/metrics", "metric-prefix": "xr",
                             "append-subscription-name": True, "export-timestamps": False, "expiration": "30s",
                             "event-processors": ["lab-names"]}},
    }


def main():
    out = Path(__file__).with_name("srv6-xr.yml")
    out.write_text(f"# Generated by gen_srv6_xr.py from {LAB}/lab.conf (lab.sh inventory) — do not edit\n"
                   + yaml.safe_dump(config(inventory()), sort_keys=False, width=200))
    print("wrote", out.name)


if __name__ == "__main__":
    main()
