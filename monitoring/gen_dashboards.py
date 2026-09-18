#!/usr/bin/env python3
"""Writes the provisioned Grafana dashboards (grafana/dashboards/*.json). Dashboards are generated rather than hand-edited
so a panel change is a one-line change here; run it and ./deploy.sh (Grafana re-reads the files within 30 s)."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "grafana" / "dashboards"; OUT.mkdir(exist_ok=True)
VM, PROM = {"type": "prometheus", "uid": "victoriametrics"}, {"type": "prometheus", "uid": "prometheus"}
_id = [0]


def panel(title, kind, targets, x, y, w, h, unit=None, ds=VM, legend=True, **opts):
    _id[0] += 1
    p = {"id": _id[0], "title": title, "type": kind, "datasource": ds, "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [{"refId": chr(65 + i), "datasource": ds, "expr": t[0], "legendFormat": t[1], **(t[2] if len(t) > 2 else {})} for i, t in enumerate(targets)],
         "fieldConfig": {"defaults": {"unit": unit} if unit else {}, "overrides": []}, "options": {}}
    if kind == "timeseries": p["options"] = {"legend": {"displayMode": "list" if legend else "hidden", "placement": "bottom"}, "tooltip": {"mode": "multi"}}
    if kind == "stat": p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "textMode": "value_and_name", "colorMode": "background", "graphMode": "none"}
    if kind == "state-timeline": p["options"] = {"showValue": "never", "mergeValues": True, "rowHeight": 0.8, "legend": {"displayMode": "list", "placement": "bottom"}}
    if kind == "table": p["options"] = {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}
    for k, v in opts.items():
        if k in ("thresholds", "mappings", "min", "max", "decimals", "color", "custom"): p["fieldConfig"]["defaults"][k] = v
        elif k == "overrides": p["fieldConfig"]["overrides"] = v
        else: p["options"][k] = v
    return p


def row(title, y):
    _id[0] += 1; return {"id": _id[0], "type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


UPDOWN = {"steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}
HEALTH = [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "1": {"text": "degraded", "color": "orange"}, "2": {"text": "up", "color": "green"}}}]
UPDOWN_MAP = [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "1": {"text": "up", "color": "green"}}}]
SITE_MAP = [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "1": {"text": "Established", "color": "green"}}}]
BGP_MAP = [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "1": {"text": "Established", "color": "green"}, "2": {"text": "admin down", "color": "gray"}}}]
GB = {"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}


def dashboard(uid, title, panels, tags, variables=None, refresh="30s"):
    return {"uid": uid, "title": title, "tags": tags, "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True, "refresh": refresh, "time": {"from": "now-3h", "to": "now"},
            "templating": {"list": variables or []}, "panels": panels, "links": [{"title": "Lab hub", "type": "link", "url": "http://192.168.50.231:8088", "targetBlank": True},
                                                                                {"title": "Prometheus alerts", "type": "link", "url": "http://192.168.50.231:9090/alerts", "targetBlank": True}]}


# ---------------------------------------------------------------- SRv6 core overview
L = 'lab="srv6-core"'
p = []
p.append(row("Tenants", 0))
p.append(panel("Tenant health", "stat", [(f"lab_tenant_health{{{L}}}", "{{tenant}}")], 0, 1, 6, 4, mappings=HEALTH))
p.append(panel("Hosts reachable", "stat", [(f"sum(lab_host_reachable{{{L}}})", "reachable"), (f"count(lab_host_reachable{{{L}}})", "hosts")], 6, 1, 4, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Steering policies", "stat", [(f"lab_steering_policies{{{L}}}", "explicit paths")], 10, 1, 3, 4, colorMode="value", thresholds={"steps": [{"color": "blue", "value": None}]}))
p.append(panel("Last test run", "stat", [(f"lab_tests_last_passed{{{L}}}", "passed"), (f"lab_tests_last_failed{{{L}}}", "failed")], 13, 1, 5, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}]}}]}]))
p.append(panel("Firing alerts", "stat", [('count(ALERTS{alertstate="firing"}) or vector(0)', "firing")], 18, 1, 6, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("PE - CE eBGP per tenant site", "state-timeline", [(f"lab_tenant_site_bgp_up{{{L}}}", "{{tenant}} {{dc}} ({{pe}}-{{ce}})")], 0, 5, 12, 8, mappings=SITE_MAP, thresholds=UPDOWN))
p.append(panel("Tenant host reachability (SSH over OOB)", "state-timeline", [(f"lab_host_reachable{{{L}}}", "{{host}} ({{tenant}})")], 12, 5, 12, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("VRF routes on the PE per tenant site", "timeseries", [(f"lab_tenant_vrf_routes{{{L}}}", "{{tenant}} {{dc}} total"), (f"lab_tenant_srv6_routes{{{L}}}", "{{tenant}} {{dc}} SRv6")], 0, 13, 12, 7, min=0))
p.append(panel("Prefixes from the CEs (frr-exporter, per PE VRF session)", "timeseries", [(f'frr_bgp_peer_prefixes_received_count_total{{{L},safi="unicast"}}', "{{node}} {{vrf}} from {{peer}}")], 12, 13, 12, 7, min=0))

p.append(row("Core: IS-IS, BFD, VPNv4", 20))
p.append(panel("IS-IS adjacencies up vs expected", "timeseries", [(f"lab_isis_adjacencies_up{{{L}}}", "{{node}} up"), (f"lab_isis_adjacencies_expected{{{L}}}", "{{node}} expected", {"hide": False})], 0, 21, 8, 7, min=0,
               overrides=[{"matcher": {"id": "byRegexp", "options": ".* expected"}, "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "gray"}}]}]))
p.append(panel("BFD sessions up per node", "timeseries", [(f"lab_bfd_sessions_up{{{L}}}", "{{node}}")], 8, 21, 8, 7, min=0))
p.append(panel("VPNv4 sessions PE - route reflector", "state-timeline", [(f"lab_vpnv4_session_up{{{L}}}", "{{pe}} -> {{reflector}}")], 16, 21, 8, 7, mappings=SITE_MAP, thresholds=UPDOWN))
p.append(panel("BGP peers (frr-exporter): every session on every node", "state-timeline", [(f'frr_bgp_peer_state{{{L}}}', "{{node}} {{vrf}} {{safi}} {{peer}}")], 0, 28, 12, 9, mappings=BGP_MAP, thresholds=UPDOWN))
p.append(panel("Routes in the RIB / FIB per node (frr-exporter)", "timeseries", [(f'sum by (node) (frr_route_total{{{L}}})', "{{node}} RIB"), (f'sum by (node) (frr_route_total_fib{{{L}}})', "{{node}} FIB")], 12, 28, 12, 9, min=0))

p.append(row("Nodes: CPU, memory, links", 37))
p.append(panel("CPU busy %", "timeseries", [(f'100 * (1 - avg by (node) (rate(node_cpu_seconds_total{{{L},mode="idle"}}[5m])))', "{{node}}")], 0, 38, 8, 8, unit="percent", min=0, max=100))
p.append(panel("Memory used %", "timeseries", [(f'100 * (1 - node_memory_MemAvailable_bytes{{{L}}} / node_memory_MemTotal_bytes{{{L}}})', "{{node}}")], 8, 38, 8, 8, unit="percent", min=0, max=100))
p.append(panel("Load (1 min)", "timeseries", [(f'node_load1{{{L}}}', "{{node}}")], 16, 38, 8, 8, min=0))
p.append(panel("Core link traffic (bit/s, PE and P data ports, received)", "timeseries", [(f'rate(node_network_receive_bytes_total{{{L},role=~"pe|p",device=~"eth[1-9]"}}[2m]) * 8', "{{node}} {{device}}")], 0, 46, 12, 8, unit="bps", min=0))
p.append(panel("Tenant host traffic (bit/s, transmitted)", "timeseries", [(f'rate(node_network_transmit_bytes_total{{{L},role="host",device="eth1"}}[2m]) * 8', "{{node}} ({{tenant}})")], 12, 46, 12, 8, unit="bps", min=0))
p.append(panel("Exporters up", "state-timeline", [(f'up{{{L}}}', "{{node}} {{job}}")], 0, 54, 24, 8, ds=PROM, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "srv6-core-overview.json").write_text(json.dumps(dashboard("srv6-core-overview", "SRv6 core: overview", p, ["srv6-core", "lab"]), indent=1))

# ---------------------------------------------------------------- node detail (any lab, any node)
_id[0] = 0
NV = [{"name": "lab", "type": "query", "datasource": VM, "query": "label_values(node_uname_info, lab)", "refresh": 2, "includeAll": False, "current": {"text": "srv6-core", "value": "srv6-core"}},
      {"name": "node", "type": "query", "datasource": VM, "query": 'label_values(node_uname_info{lab="$lab"}, node)', "refresh": 2, "includeAll": False, "multi": False, "current": {"text": "pe1", "value": "pe1"}}]
N = 'lab="$lab",node="$node"'
p = []
p.append(panel("Uptime", "stat", [(f'time() - node_boot_time_seconds{{{N}}}', "uptime")], 0, 0, 4, 4, unit="s", colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("CPU busy %", "stat", [(f'100 * (1 - avg(rate(node_cpu_seconds_total{{{N},mode="idle"}}[5m])))', "cpu")], 4, 0, 4, 4, unit="percent", colorMode="value", thresholds=GB))
p.append(panel("Memory used %", "stat", [(f'100 * (1 - node_memory_MemAvailable_bytes{{{N}}} / node_memory_MemTotal_bytes{{{N}}})', "mem")], 8, 0, 4, 4, unit="percent", colorMode="value", thresholds=GB))
FS = 'mountpoint=~"/|/usr/lib/live/mount/persistence",fstype!="tmpfs"'   # the persistent disk: "/" on the hosts, the persistence mount on VyOS (live-boot overlay)
p.append(panel("Persistent disk used %", "stat", [(f'max(100 * (1 - node_filesystem_avail_bytes{{{N},{FS}}} / node_filesystem_size_bytes{{{N},{FS}}}))', "disk")], 12, 0, 4, 4, unit="percent", colorMode="value", thresholds=GB))
p.append(panel("Routes RIB / FIB (FRR)", "stat", [(f'sum(frr_route_total{{{N}}})', "RIB"), (f'sum(frr_route_total_fib{{{N}}})', "FIB")], 16, 0, 4, 4, colorMode="value", thresholds={"steps": [{"color": "blue", "value": None}]}))
p.append(panel("BGP peers established", "stat", [(f'sum(frr_bgp_peer_state{{{N}}} == 1) or vector(0)', "established"), (f'count(frr_bgp_peer_state{{{N}}}) or vector(0)', "configured")], 20, 0, 4, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("CPU by mode", "timeseries", [(f'avg by (mode) (rate(node_cpu_seconds_total{{{N},mode!="idle"}}[2m])) * 100', "{{mode}}")], 0, 4, 12, 8, unit="percent", min=0, custom={"stacking": {"mode": "normal"}, "fillOpacity": 30}))
p.append(panel("Memory", "timeseries", [(f'node_memory_MemTotal_bytes{{{N}}} - node_memory_MemAvailable_bytes{{{N}}}', "used"), (f'node_memory_MemAvailable_bytes{{{N}}}', "available")], 12, 4, 12, 8, unit="bytes", min=0))
p.append(panel("Interface traffic received (bit/s)", "timeseries", [(f'rate(node_network_receive_bytes_total{{{N},device!~"lo|dum.*|vrf.*"}}[2m]) * 8', "{{device}}")], 0, 12, 12, 8, unit="bps", min=0))
p.append(panel("Interface traffic transmitted (bit/s)", "timeseries", [(f'rate(node_network_transmit_bytes_total{{{N},device!~"lo|dum.*|vrf.*"}}[2m]) * 8', "{{device}}")], 12, 12, 12, 8, unit="bps", min=0))
p.append(panel("Packets / s received", "timeseries", [(f'rate(node_network_receive_packets_total{{{N},device!~"lo|dum.*|vrf.*"}}[2m])', "{{device}}")], 0, 20, 8, 7, unit="pps", min=0))
p.append(panel("Errors + drops / s", "timeseries", [(f'rate(node_network_receive_errs_total{{{N}}}[2m]) + rate(node_network_receive_drop_total{{{N}}}[2m])', "{{device}} rx"), (f'rate(node_network_transmit_errs_total{{{N}}}[2m]) + rate(node_network_transmit_drop_total{{{N}}}[2m])', "{{device}} tx")], 8, 20, 8, 7, min=0))
p.append(panel("Load", "timeseries", [(f'node_load1{{{N}}}', "1 min"), (f'node_load5{{{N}}}', "5 min"), (f'node_load15{{{N}}}', "15 min")], 16, 20, 8, 7, min=0))
p.append(panel("BGP peer state (FRR)", "state-timeline", [(f'frr_bgp_peer_state{{{N}}}', "{{vrf}} {{safi}} {{peer}} (AS {{peer_as}})")], 0, 27, 12, 8, mappings=BGP_MAP, thresholds=UPDOWN))
p.append(panel("BFD peer state (FRR)", "state-timeline", [(f'frr_bfd_peer_state{{{N}}}', "{{peer}}")], 12, 27, 12, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("BGP prefixes received per peer", "timeseries", [(f'frr_bgp_peer_prefixes_received_count_total{{{N}}}', "{{vrf}} {{safi}} {{peer}}")], 0, 35, 12, 7, min=0))
p.append(panel("BGP messages / min per peer", "timeseries", [(f'rate(frr_bgp_peer_message_received_total{{{N}}}[5m]) * 60', "{{vrf}} {{peer}} rx"), (f'rate(frr_bgp_peer_message_sent_total{{{N}}}[5m]) * 60', "{{vrf}} {{peer}} tx")], 12, 35, 12, 7, min=0))
(OUT / "node-detail.json").write_text(json.dumps(dashboard("lab-node-detail", "Lab node detail", p, ["lab", "node"], NV), indent=1))

# ---------------------------------------------------------------- lab hosts (the physical lab host is not scraped; this is every lab's fleet)
_id[0] = 0
p = []
p.append(panel("Exporters up per lab", "stat", [('sum by (lab) (up{job=~"node|frr|portal"})', "{{lab}} up"), ('count by (lab) (up{job=~"node|frr|portal"})', "{{lab}} targets")], 0, 0, 12, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Monitoring stack", "stat", [('up{job="monitoring"}', "{{component}}")], 12, 0, 12, 4, ds=PROM, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("CPU busy % by node (all labs)", "timeseries", [('100 * (1 - avg by (lab, node) (rate(node_cpu_seconds_total{mode="idle"}[5m])))', "{{lab}} {{node}}")], 0, 4, 12, 9, unit="percent", min=0, max=100))
p.append(panel("Memory used % by node (all labs)", "timeseries", [('100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)', "{{lab}} {{node}}")], 12, 4, 12, 9, unit="percent", min=0, max=100))
p.append(panel("Tests: passed / failed per lab", "timeseries", [("lab_tests_last_passed", "{{lab}} passed"), ("lab_tests_last_failed", "{{lab}} failed")], 0, 13, 12, 7, min=0))
p.append(panel("Firing alerts", "table", [('ALERTS{alertstate="firing"}', "", {"instant": True, "format": "table"})], 12, 13, 12, 7, ds=PROM))
p.append(panel("Samples scraped / s (Prometheus)", "timeseries", [('rate(prometheus_tsdb_head_samples_appended_total[5m])', "samples/s")], 0, 20, 12, 6, min=0, ds=PROM))
p.append(panel("VictoriaMetrics: active series", "timeseries", [('vm_cache_entries{type="storage/hour_metric_ids"}', "active series")], 12, 20, 12, 6, min=0))
(OUT / "labs-fleet.json").write_text(json.dumps(dashboard("labs-fleet", "Labs: fleet and monitoring", p, ["lab", "fleet"]), indent=1))
# ---------------------------------------------------------------- VyOS Telegraf (pushed by the nodes) + syslog (VictoriaLogs)
_id[0] = 0
VL = {"type": "victoriametrics-logs-datasource", "uid": "victorialogs"}
TV = [{"name": "lab", "type": "query", "datasource": VM, "query": "label_values(cpu_usage_idle, lab)", "refresh": 2, "includeAll": False, "current": {"text": "srv6-core", "value": "srv6-core"}}]
T = 'lab="$lab"'
p = []
p.append(panel("Nodes pushing (Telegraf, last 2 min)", "stat", [(f'count(count by (host) (cpu_usage_idle{{{T},cpu="cpu-total"}}))', "nodes")], 0, 0, 4, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("FRR services down (vyos_services_status)", "stat", [(f'count(vyos_services_status{{{T}}} == 0) or vector(0)', "down")], 4, 0, 4, 4, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("systemd units failed", "stat", [(f'count(systemd_units_active_code{{{T},active="failed"}}) or vector(0)', "failed")], 8, 0, 4, 4, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Ip6OutNoRoutes / s (SRv6 encapsulated packets with no outer route — the VRF leak quirk)", "stat", [(f'sum(rate(nstat_Ip6OutNoRoutes{{{T}}}[5m]))', "per s")], 12, 0, 6, 4, colorMode="background", decimals=2, thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 0.5}]}))
p.append(panel("Syslog lines / min (VictoriaLogs)", "stat", [('* | stats count() as n', "lines")], 18, 0, 6, 4, ds=VL, colorMode="value", thresholds={"steps": [{"color": "blue", "value": None}]}))
p.append(panel("VyOS services (FRR daemons) per node", "state-timeline", [(f'vyos_services_status{{{T}}}', "{{host}} {{service}}")], 0, 4, 12, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("CPU busy % (Telegraf)", "timeseries", [(f'100 - cpu_usage_idle{{{T},cpu="cpu-total"}}', "{{host}}")], 12, 4, 12, 9, unit="percent", min=0, max=100))
p.append(panel("Kernel nstat: IPv6 forwarded / s", "timeseries", [(f'rate(nstat_Ip6OutForwDatagrams{{{T}}}[2m])', "{{host}}")], 0, 13, 8, 7, unit="pps", min=0))
p.append(panel("Kernel nstat: Ip6OutNoRoutes / s per node", "timeseries", [(f'rate(nstat_Ip6OutNoRoutes{{{T}}}[2m])', "{{host}}")], 8, 13, 8, 7, unit="pps", min=0))
p.append(panel("NIC drops / s (ethtool)", "timeseries", [(f'rate(ethtool_rx_drops{{{T}}}[2m])', "{{host}} {{interface}} rx")], 16, 13, 8, 7, min=0))
p.append(panel("Conntrack entries", "timeseries", [(f'conntrack_ip_conntrack_count{{{T}}}', "{{host}}")], 0, 20, 8, 7, min=0))
p.append(panel("Memory used % (Telegraf)", "timeseries", [(f'mem_used_percent{{{T}}}', "{{host}}")], 8, 20, 8, 7, unit="percent", min=0, max=100))
p.append(panel("Interrupts / s", "timeseries", [(f'sum by (host) (rate(interrupts_total{{{T}}}[2m]))', "{{host}}")], 16, 20, 8, 7, min=0))
p.append(panel("Routing daemons: BGP / IS-IS / BFD / zebra syslog", "logs", [('app_name:in(bgpd, isisd, bfdd, zebra, staticd, vtysh)', "")], 0, 27, 24, 10, ds=VL, showTime=True, wrapLogMessage=True, sortOrder="Descending"))
p.append(panel("Commits and configuration changes (vyos-configd / commit)", "logs", [('app_name:in(vyos-configd, commit, vyos-commitd) OR _msg:"commit"', "")], 0, 37, 24, 8, ds=VL, showTime=True, wrapLogMessage=True, sortOrder="Descending"))
(OUT / "vyos-telegraf.json").write_text(json.dumps(dashboard("vyos-telegraf", "VyOS telemetry: Telegraf and syslog", p, ["lab", "vyos", "telegraf"], TV), indent=1))
print("wrote", ", ".join(f.name for f in sorted(OUT.glob("*.json"))))
