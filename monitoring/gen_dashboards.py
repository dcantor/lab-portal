#!/usr/bin/env python3
"""Writes the provisioned Grafana dashboards (grafana/dashboards/*.json). Dashboards are generated rather than hand-edited
so a panel change is a one-line change here; run it and ./deploy.sh (Grafana re-reads the files within 30 s)."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "grafana" / "dashboards"; OUT.mkdir(exist_ok=True)
VM, PROM = {"type": "prometheus", "uid": "victoriametrics"}, {"type": "prometheus", "uid": "prometheus"}
VL = {"type": "victoriametrics-logs-datasource", "uid": "victorialogs"}
def logs_ts(q, legend): return (q, legend, {"queryType": "statsRange"})   # VictoriaLogs `stats by (_time:step, ...)` as time series: statsRange runs it over the
# dashboard range and returns one series per label set (queryType stats + format time_series returns one single-point frame per bucket instead)
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
    if kind == "table" and ds is VL and opts.get("columns"):   # a LogsQL `stats by (...)` with ONE stats value arrives as one single-row frame per group:
        # labels -> columns, frames -> rows, drop the query time and the metric name; the group-by columns in the given order, the value (named after its `as`) last
        cols = opts.pop("columns")
        stat = targets[0][0].rsplit(" as ", 1)[-1].split()[0].strip("|")
        p["transformations"] = [{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}},
                                {"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True}, "renameByName": {"Value": stat}, "indexByName": {c: i for i, c in enumerate(cols + ["Value"])}}}]
    if kind == "bargauge": p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True}
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


def annotations(lab):
    """Event overlays: what the portal / the tests / the steering tool did (Grafana annotations by tag) and what the routers'
    syslog said (vmalert-logs alerts, as ALERTS series in VictoriaMetrics)."""
    return {"list": [
        {"name": "Runs and tests", "enable": True, "iconColor": "#5794F2", "datasource": {"type": "grafana", "uid": "-grafana-"}, "type": "tags", "target": {"type": "tags", "tags": [lab, "run"], "matchAny": False, "limit": 200}},
        {"name": "Test events (failover, reflector)", "enable": True, "iconColor": "#F2495C", "datasource": {"type": "grafana", "uid": "-grafana-"}, "type": "tags", "target": {"type": "tags", "tags": [lab, "test"], "matchAny": False, "limit": 200}},
        {"name": "Steering changes", "enable": True, "iconColor": "#FF9830", "datasource": {"type": "grafana", "uid": "-grafana-"}, "type": "tags", "target": {"type": "tags", "tags": [lab, "steering"], "matchAny": False, "limit": 200}},
        {"name": "Router syslog alerts (vmalert-logs)", "enable": True, "iconColor": "#B877D9", "datasource": VM, "expr": f'ALERTS{{lab="{lab}",evaluator="vmalert-logs",alertstate="firing",severity!="info"}}', "step": "30s", "titleFormat": "{{alertname}}", "textFormat": "{{hostname}}", "tagKeys": "alertname,hostname"},
    ]}


def dashboard(uid, title, panels, tags, variables=None, refresh="30s", lab="srv6-core"):
    return {"uid": uid, "title": title, "tags": tags, "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True, "refresh": refresh, "time": {"from": "now-3h", "to": "now"},
            "annotations": annotations(lab), "templating": {"list": variables or []}, "panels": panels, "links": [{"title": "Lab hub", "type": "link", "url": "http://192.168.50.231:8088", "targetBlank": True},
                                                                                {"title": "Prometheus alerts", "type": "link", "url": "http://192.168.50.231:9091/alerts", "targetBlank": True},
                                                                                # theme switch: the same dashboard with ?theme=dark / light (time range and variables kept). Grafana only applies
                                                                                # the theme parameter on a full page load, and in-app links never reload, so these open a new tab. The default
                                                                                # theme is "system" (GF_USERS_DEFAULT_THEME): the OS / browser dark-mode setting decides unless a link forces it.
                                                                                {"title": "\u263e Dark", "type": "link", "url": f"http://192.168.50.231:3001/d/{uid}?theme=dark", "keepTime": True, "includeVars": True, "targetBlank": True, "tooltip": "this dashboard in the dark theme (new tab)"},
                                                                                {"title": "\u2600 Light", "type": "link", "url": f"http://192.168.50.231:3001/d/{uid}?theme=light", "keepTime": True, "includeVars": True, "targetBlank": True, "tooltip": "this dashboard in the light theme (new tab)"}]}


def host_row(p, y):
    """The Ubuntu KVM host that runs every lab (node_exporter on the OOB bridge): CPU, memory, load, the busiest cores."""
    HL = 'lab="lab-host"'
    p.append(row("Ubuntu lab host (KVM): CPU and memory", y))
    p.append(panel("Host CPU busy %", "stat", [(f'100 * (1 - avg(rate(node_cpu_seconds_total{{{HL},mode="idle"}}[2m])))', "cpu")], 0, y + 1, 4, 4, unit="percent", colorMode="value", thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}))
    p.append(panel("Host memory used %", "stat", [(f'100 * (1 - node_memory_MemAvailable_bytes{{{HL}}} / node_memory_MemTotal_bytes{{{HL}}})', "mem")], 4, y + 1, 4, 4, unit="percent", colorMode="value", thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 90}]}))
    p.append(panel("Host memory used / total", "stat", [(f'node_memory_MemTotal_bytes{{{HL}}} - node_memory_MemAvailable_bytes{{{HL}}}', "used"), (f'node_memory_MemTotal_bytes{{{HL}}}', "total")], 8, y + 1, 5, 4, unit="bytes", colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
    p.append(panel("Host load (1 / 5 / 15 min) vs cores", "stat", [(f'node_load1{{{HL}}}', "1m"), (f'node_load5{{{HL}}}', "5m"), (f'node_load15{{{HL}}}', "15m"), (f'count(node_cpu_seconds_total{{{HL},mode="idle"}})', "cores")], 13, y + 1, 7, 4, colorMode="value", decimals=1, thresholds={"steps": [{"color": "green", "value": None}]}))
    p.append(panel("Host swap used %", "stat", [(f'100 * (1 - node_memory_SwapFree_bytes{{{HL}}} / node_memory_SwapTotal_bytes{{{HL}}})', "swap")], 20, y + 1, 4, 4, unit="percent", colorMode="value", thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 20}, {"color": "red", "value": 60}]}))
    p.append(panel("Host CPU busy % (all cores) and per mode", "timeseries", [(f'100 * (1 - avg(rate(node_cpu_seconds_total{{{HL},mode="idle"}}[2m])))', "busy"), (f'100 * avg(rate(node_cpu_seconds_total{{{HL},mode="user"}}[2m]))', "user"), (f'100 * avg(rate(node_cpu_seconds_total{{{HL},mode="system"}}[2m]))', "system"), (f'100 * avg(rate(node_cpu_seconds_total{{{HL},mode="iowait"}}[2m]))', "iowait"), (f'100 * avg(rate(node_cpu_seconds_total{{{HL},mode="steal"}}[2m]))', "steal")], 0, y + 5, 8, 8, unit="percent", min=0, max=100))
    p.append(panel("Host memory (bytes)", "timeseries", [(f'node_memory_MemTotal_bytes{{{HL}}} - node_memory_MemAvailable_bytes{{{HL}}}', "used"), (f'node_memory_Cached_bytes{{{HL}}} + node_memory_Buffers_bytes{{{HL}}}', "cache + buffers"), (f'node_memory_MemAvailable_bytes{{{HL}}}', "available"), (f'node_memory_SwapTotal_bytes{{{HL}}} - node_memory_SwapFree_bytes{{{HL}}}', "swap used")], 8, y + 5, 8, 8, unit="bytes", min=0))
    p.append(panel("Busiest host cores (busy %, top 8)", "timeseries", [(f'topk(8, 100 * (1 - rate(node_cpu_seconds_total{{{HL},mode="idle"}}[2m])))', "core {{cpu}}")], 16, y + 5, 8, 8, unit="percent", min=0, max=100))
    return y + 13


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
p.append(panel("Firing alerts (metrics)", "stat", [('count(ALERTS{alertstate="firing"}) or vector(0)', "firing")], 18, 1, 3, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Firing alerts (syslog)", "stat", [(f'count(ALERTS{{{L},evaluator="vmalert-logs",alertstate="firing",severity!="info"}}) or vector(0)', "firing")], 21, 1, 3, 4, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("PE - CE eBGP per tenant site", "state-timeline", [(f"lab_tenant_site_bgp_up{{{L}}}", "{{tenant}} {{dc}} ({{pe}}-{{ce}})")], 0, 5, 12, 8, mappings=SITE_MAP, thresholds=UPDOWN))
p.append(panel("Tenant host reachability (SSH over OOB)", "state-timeline", [(f"lab_host_reachable{{{L}}}", "{{host}} ({{tenant}})")], 12, 5, 12, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("VRF routes on the PE per tenant site", "timeseries", [(f"lab_tenant_vrf_routes{{{L}}}", "{{tenant}} {{dc}} total"), (f"lab_tenant_srv6_routes{{{L}}}", "{{tenant}} {{dc}} SRv6")], 0, 13, 12, 7, min=0))
p.append(panel("Prefixes from the CEs (frr-exporter, per PE VRF session)", "timeseries", [(f'frr_bgp_peer_prefixes_received_count_total{{{L},safi="unicast"}}', "{{node}} {{vrf}} from {{peer}}")], 12, 13, 12, 7, min=0))
p.append(panel("Internet breakout: firewall, eBGP per tenant, default route per tenant VRF on every PE", "state-timeline",
               [(f"lab_internet_fw_reachable{{{L}}}", "{{fw}} reachable"), (f"lab_internet_bgp_up{{{L}}}", "eBGP {{pe}} - fw ({{tenant}})"), (f"lab_internet_default_route{{{L}}}", "0/0 on {{pe}} ({{tenant}})")], 0, 20, 24, 7, mappings=UPDOWN_MAP, thresholds=UPDOWN))

p.append(row("Core: IS-IS, BFD, VPNv4", 27))
p.append(panel("IS-IS adjacencies up vs expected", "timeseries", [(f"lab_isis_adjacencies_up{{{L}}}", "{{node}} up"), (f"lab_isis_adjacencies_expected{{{L}}}", "{{node}} expected", {"hide": False})], 0, 28, 8, 7, min=0,
               overrides=[{"matcher": {"id": "byRegexp", "options": ".* expected"}, "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "gray"}}]}]))
p.append(panel("BFD sessions up per node", "timeseries", [(f"lab_bfd_sessions_up{{{L}}}", "{{node}}")], 8, 28, 8, 7, min=0))
p.append(panel("VPNv4 sessions PE - route reflector", "state-timeline", [(f"lab_vpnv4_session_up{{{L}}}", "{{pe}} -> {{reflector}}")], 16, 28, 8, 7, mappings=SITE_MAP, thresholds=UPDOWN))
p.append(panel("BGP peers (frr-exporter): every session on every node", "state-timeline", [(f'frr_bgp_peer_state{{{L}}}', "{{node}} {{vrf}} {{safi}} {{peer}}")], 0, 35, 12, 9, mappings=BGP_MAP, thresholds=UPDOWN))
p.append(panel("Routes in the RIB / FIB per node (frr-exporter)", "timeseries", [(f'sum by (node) (frr_route_total{{{L}}})', "{{node}} RIB"), (f'sum by (node) (frr_route_total_fib{{{L}}})', "{{node}} FIB")], 12, 35, 12, 9, min=0))

p.append(row("Nodes: CPU, memory, links", 44))
p.append(panel("CPU busy %", "timeseries", [(f'100 * (1 - avg by (node) (rate(node_cpu_seconds_total{{{L},mode="idle"}}[5m])))', "{{node}}")], 0, 45, 8, 8, unit="percent", min=0, max=100))
p.append(panel("Memory used %", "timeseries", [(f'100 * (1 - node_memory_MemAvailable_bytes{{{L}}} / node_memory_MemTotal_bytes{{{L}}})', "{{node}}")], 8, 45, 8, 8, unit="percent", min=0, max=100))
p.append(panel("Load (1 min)", "timeseries", [(f'node_load1{{{L}}}', "{{node}}")], 16, 45, 8, 8, min=0))
p.append(panel("Core link traffic (bit/s, PE and P data ports, received)", "timeseries", [(f'rate(node_network_receive_bytes_total{{{L},role=~"pe|p",device=~"eth[1-9]"}}[2m]) * 8', "{{node}} {{device}}")], 0, 53, 12, 8, unit="bps", min=0))
p.append(panel("Tenant host traffic (bit/s, transmitted)", "timeseries", [(f'rate(node_network_transmit_bytes_total{{{L},role="host",device="eth1"}}[2m]) * 8', "{{node}} ({{tenant}})")], 12, 53, 12, 8, unit="bps", min=0))
p.append(panel("Exporters up", "state-timeline", [(f'up{{{L}}}', "{{node}} {{job}}")], 0, 61, 24, 8, ds=PROM, mappings=UPDOWN_MAP, thresholds=UPDOWN))
host_row(p, 69)
(OUT / "srv6-core-overview.json").write_text(json.dumps(dashboard("srv6-core-overview", "SRv6 core: overview", p, ["srv6-core", "lab"]), indent=1))

# ---------------------------------------------------------------- C8000v IPsec lab overview (the portal's lab_tunnel_* / lab_headend_* gauges)
_id[0] = 0
L = 'lab="cat8000v-ipsec"'
p = []
p.append(row("Tunnels", 0))
p.append(panel("Tunnels up", "stat", [(f"lab_tunnels_up{{{L}}}", "up"), (f"lab_tunnels_total{{{L}}}", "modelled")], 0, 1, 5, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "modelled"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "gray"}}]}], thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Tunnels down or degraded", "stat", [(f"count(lab_tunnel_health{{{L}}} < 2) or vector(0)", "not up")], 5, 1, 4, 4, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("VMs running", "stat", [(f'sum(lab_vm_running{{{L},role="hub"}})', "hubs"), (f'sum(lab_vm_running{{{L},role="spoke"}})', "spokes"), (f'sum(lab_vm_running{{{L},role="firewall"}})', "firewalls"), (f'sum(lab_vm_running{{{L},role="host"}})', "LAN hosts")], 9, 1, 6, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Last test run", "stat", [(f"lab_tests_last_passed{{{L}}}", "passed"), (f"lab_tests_last_failed{{{L}}}", "failed")], 15, 1, 5, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}]}}]}]))
p.append(panel("Firing alerts", "stat", [(f'count(ALERTS{{{L},alertstate="firing"}}) or vector(0)', "firing")], 20, 1, 4, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Tunnel health (IKEv2 SA + VTI + eBGP), per tunnel", "state-timeline", [(f"lab_tunnel_health{{{L}}}", "{{tunnel}} ({{region}})")], 0, 5, 14, 10, mappings=HEALTH, thresholds={"steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 2}]}))
p.append(panel("IKEv2 SA age per tunnel", "timeseries", [(f"lab_tunnel_ike_sa_age_seconds{{{L}}}", "{{tunnel}}")], 14, 5, 10, 5, unit="s", min=0))
p.append(panel("Prefixes from the spoke per tunnel (eBGP)", "timeseries", [(f"lab_tunnel_bgp_prefixes{{{L}}}", "{{tunnel}}")], 14, 10, 10, 5, min=0))
p.append(panel("ESP packets per second per tunnel (encaps + decaps on the headend)", "timeseries", [(f"rate(lab_tunnel_esp_encaps_packets_total{{{L}}}[5m])", "{{tunnel}} encaps"), (f"rate(lab_tunnel_esp_decaps_packets_total{{{L}}}[5m])", "{{tunnel}} decaps")], 0, 15, 12, 7, unit="short", min=0))
p.append(panel("ESP errors per second per tunnel (send + receive)", "timeseries", [(f"rate(lab_tunnel_esp_send_errors_total{{{L}}}[5m]) + rate(lab_tunnel_esp_recv_errors_total{{{L}}}[5m])", "{{tunnel}}")], 12, 15, 12, 7, unit="short", min=0))
p.append(panel("VTI traffic per tunnel (bit/s, headend side)", "timeseries", [(f"lab_tunnel_in_bps{{{L}}}", "{{tunnel}} in"), (f"lab_tunnel_out_bps{{{L}}}", "{{tunnel}} out")], 0, 22, 24, 7, unit="bps", min=0))

p.append(row("Headends: capacity and resources", 29))
p.append(panel("Tunnels per headend: up vs modelled vs capacity", "bargauge", [(f"lab_headend_tunnels_up{{{L}}}", "{{headend}} up"), (f"lab_headend_tunnels{{{L}}}", "{{headend}} modelled")], 0, 30, 8, 7, min=0,
               thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Utilisation of the binding constraint (tunnels / bandwidth / CPU)", "bargauge", [(f"lab_headend_utilisation_pct{{{L}}}", "{{headend}}")], 8, 30, 8, 7, unit="percent", min=0, max=100,
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}))
p.append(panel("Free tunnel slots (after every constraint)", "bargauge", [(f"lab_headend_effective_free{{{L}}}", "{{headend}}")], 16, 30, 8, 7, min=0, thresholds={"steps": [{"color": "red", "value": None}, {"color": "orange", "value": 1}, {"color": "green", "value": 5}]}))
p.append(panel("Control-plane CPU %", "timeseries", [(f"lab_headend_cpu_pct{{{L}}}", "{{headend}}")], 0, 37, 8, 7, unit="percent", min=0, max=100))
p.append(panel("QFP (data-plane) CPU %", "timeseries", [(f"lab_headend_qfp_cpu_pct{{{L}}}", "{{headend}}")], 8, 37, 8, 7, unit="percent", min=0, max=100))
p.append(panel("DRAM used %", "timeseries", [(f"lab_headend_dram_pct{{{L}}}", "{{headend}}")], 16, 37, 8, 7, unit="percent", min=0, max=100))
p.append(panel("Bandwidth committed vs firewall bandwidth (Mbit/s)", "timeseries", [(f"lab_headend_bandwidth_used_mbps{{{L}}}", "{{headend}} committed"), (f"lab_headend_bandwidth_mbps{{{L}}}", "{{headend}} firewall", {"hide": False})], 0, 44, 12, 7, min=0,
               overrides=[{"matcher": {"id": "byRegexp", "options": ".* firewall"}, "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "gray"}}]}]))
p.append(panel("IKEv2 sessions per headend", "timeseries", [(f"lab_headend_ike_sessions{{{L}}}", "{{headend}}")], 12, 44, 6, 7, min=0))
p.append(panel("Headend live collection", "state-timeline", [(f"1 - lab_headend_collect_error{{{L}}}", "{{headend}}")], 18, 44, 6, 7, mappings=UPDOWN_MAP, thresholds=UPDOWN))
# IKE certificate authentication (chosen per spoke): days left on every router certificate the lab CA issued, and each spoke's method
p.append(panel("Router certificates: days to expiry (lab CA)", "bargauge", [(f"(lab_cert_not_after_seconds{{{L}}} - time()) / 86400", "{{device}}")], 0, 51, 16, 6, unit="d", min=0, max=365, decimals=0,
               thresholds={"steps": [{"color": "red", "value": None}, {"color": "orange", "value": 30}, {"color": "green", "value": 90}]}))
p.append(panel("IKE authentication per spoke (each branch chooses; default: lab_ike_certificate_auth)", "stat", [(f"lab_spoke_certificate_auth{{{L}}}", "{{spoke}}")], 16, 51, 8, 6, colorMode="background",
               mappings=[{"type": "value", "options": {"0": {"text": "PSK", "color": "orange"}, "1": {"text": "certificate", "color": "green"}}}], thresholds={"steps": [{"color": "orange", "value": None}, {"color": "green", "value": 1}]}))

# the VyOS firewalls' kernel forward-filter log, shipped by syslog to VictoriaLogs (render_vyos: system syslog remote). Rule 900 is the
# logged drop at the end of the policy; the IKE / ESP / ICMP accept rules log the first packet of each flow (later packets take rule 5)
FW = 'hostname:fw-* app_name:kernel "FWD-filter" '
FWX = '| extract "[ipv4-<fwd>-filter-<rule>-<verdict>]IN=<in> OUT=<out> " | extract " SRC=<src> DST=<dst> " | extract " PROTO=<proto> " | extract " DPT=<dport> " '
p.append(row("Firewalls: forward filter (syslog -> VictoriaLogs)", 57))
p.append(panel("Dropped packets / 5 min per firewall (rule 900)", "timeseries", [logs_ts(FW + '"FWD-filter-900-D" | stats by (_time:5m, hostname) count() as drops', "{{hostname}}")], 0, 58, 8, 7, ds=VL, min=0))
p.append(panel("New flows accepted / 5 min per firewall (IKE, ESP, ICMP first packets)", "timeseries", [logs_ts(FW + '"-A]IN=" | stats by (_time:5m, hostname) count() as accepts', "{{hostname}}")], 8, 58, 8, 7, ds=VL, min=0))   # the accept tags end in -A]
p.append(panel("Drops / 5 min by source (all firewalls)", "timeseries", [logs_ts(FW + '"FWD-filter-900-D" ' + FWX + '| stats by (_time:5m, src) count() as drops', "{{src}}")], 16, 58, 8, 7, ds=VL, min=0))
p.append(panel("Top dropped flows (selected range)", "table", [(FW + '"FWD-filter-900-D" ' + FWX + '| stats by (hostname, in, out, src, dst, proto, dport) count() as hits | sort by (hits desc) | limit 20', "", {"queryType": "stats"})], 0, 65, 12, 9, ds=VL, columns=["hostname", "in", "out", "src", "dst", "proto", "dport"]))
p.append(panel("Firewall log (newest first)", "logs", [(FW, "")], 12, 65, 12, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))

y = host_row(p, 74)
p.append(row("Lab and runs", y))
p.append(panel("VMs running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, y + 1, 12, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 12, y + 1, 12, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "cat8000v-ipsec-overview.json").write_text(json.dumps(dashboard("cat8000v-ipsec-overview", "C8000v IPsec: overview", p, ["cat8000v-ipsec", "lab"], lab="cat8000v-ipsec"), indent=1))

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
host_row(p, 26)
(OUT / "labs-fleet.json").write_text(json.dumps(dashboard("labs-fleet", "Labs: fleet and monitoring", p, ["lab", "fleet"]), indent=1))
# ---------------------------------------------------------------- VyOS Telegraf (pushed by the nodes) + syslog (VictoriaLogs)
_id[0] = 0
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
p.append(panel("Log-derived alerts (vmalert-logs), last 3 h", "state-timeline", [(f'max by (alertname, hostname) (ALERTS{{{T},evaluator="vmalert-logs"}})', "{{alertname}} {{hostname}}")], 0, 27, 24, 7, mappings=[{"type": "value", "options": {"1": {"text": "firing", "color": "red"}}}], thresholds={"steps": [{"color": "red", "value": None}]}))
p.append(panel("Routing daemons: BGP / IS-IS / BFD / zebra syslog", "logs", [('app_name:in(bgpd, isisd, bfdd, zebra, staticd, vtysh) | uniq by (_time, hostname, _msg)', "")], 0, 34, 24, 10, ds=VL, showTime=True, wrapLogMessage=True, sortOrder="Descending"))
p.append(panel("Commits and configuration changes (vyos-configd / commit)", "logs", [('app_name:in(vyos-configd, commit, vyos-commitd) OR _msg:"commit"', "")], 0, 37, 24, 8, ds=VL, showTime=True, wrapLogMessage=True, sortOrder="Descending"))
(OUT / "vyos-telegraf.json").write_text(json.dumps(dashboard("vyos-telegraf", "VyOS telemetry: Telegraf and syslog", p, ["lab", "vyos", "telegraf"], TV), indent=1))
# ---------------------------------------------------------------- Flows (sFlow from PEs / Ps -> goflow2 -> VictoriaLogs)
_id[0] = 0
FL = 'sampler_address:* '                                   # every sFlow record (goflow2 JSON, one per sampled packet)
SR = FL + 'proto:"IPv6-Route" '                              # SRv6-encapsulated packets: outer IPv6 with a routing header
p = []
p.append(panel("Sampled packets / min per exporter", "stat", [(FL + '| stats by (sampler_address) count() as samples', "{{sampler_address}}", {"queryType": "stats"})], 0, 0, 12, 4, ds=VL, colorMode="value", thresholds={"steps": [{"color": "blue", "value": None}]}))
p.append(panel("SRv6 traffic seen in the core (sampled bytes × rate, last range)", "stat", [(SR + '| stats sum(bytes) as b | math b * 16 as bytes | fields bytes', "bytes", {"queryType": "stats"})], 12, 0, 6, 4, ds=VL, unit="bytes", colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Distinct SRv6 paths (src PE -> SID)", "stat", [(SR + '| stats count_uniq(src_addr, dst_addr) as paths', "paths", {"queryType": "stats"})], 18, 0, 6, 4, ds=VL, colorMode="value", thresholds={"steps": [{"color": "blue", "value": None}]}))
p.append(panel("SRv6 flows: source PE -> destination SID (uDT4 / uSID carrier), estimated bytes", "table", [(SR + '| stats by (src_addr, dst_addr) sum(bytes) as sampled_bytes, count() as samples, count_uniq(sampler_address) as seen_by | math sampled_bytes * 16 as est_bytes | sort by (est_bytes desc) | limit 30', "", {"queryType": "stats"})], 0, 4, 14, 10, ds=VL))
p.append(panel("Which router forwards what (sampler x destination SID)", "table", [(SR + '| stats by (sampler_address, dst_addr) sum(bytes) as sampled_bytes, count() as samples | sort by (sampler_address, sampled_bytes desc) | limit 40', "", {"queryType": "stats"})], 14, 4, 10, 10, ds=VL))
p.append(panel("SRv6 bytes / min per destination SID (sampled × 16)", "timeseries", [logs_ts(SR + '| stats by (_time:1m, dst_addr) sum(bytes) as sampled | math sampled * 16 as bytes | fields _time, dst_addr, bytes', "{{dst_addr}}")], 0, 14, 12, 8, ds=VL, unit="bytes", min=0))
p.append(panel("Sampled packets / min per exporter", "timeseries", [logs_ts(FL + '| stats by (_time:1m, sampler_address) count() as samples', "{{sampler_address}}")], 12, 14, 12, 8, ds=VL, min=0))
p.append(panel("Protocols in the core (sampled packets)", "table", [(FL + '| stats by (proto, etype) count() as samples | sort by (samples desc) | limit 12', "", {"queryType": "stats"})], 0, 22, 8, 8, ds=VL))
p.append(panel("Steered packets: uSID carrier (3+ uSIDs in the destination) or an SRH with segments left", "table", [(SR + '(ipv6_routing_header_seg_left:>0 OR dst_addr:~"^fd00:c(:[0-9a-f]+){3,}::") | stats by (sampler_address, src_addr, dst_addr, ipv6_routing_header_addresses) count() as samples | sort by (samples desc) | limit 12', "", {"queryType": "stats"})], 8, 22, 16, 8, ds=VL))
(OUT / "flows.json").write_text(json.dumps(dashboard("srv6-flows", "SRv6 flows (sFlow)", p, ["srv6-core", "flows", "sflow"]), indent=1))
print("wrote", ", ".join(f.name for f in sorted(OUT.glob("*.json"))))
