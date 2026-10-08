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
        elif k == "transformations": p["transformations"] = v
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
# The BGP looking glass (the lg VM: a passive route collector peering with every reflector). Its own /metrics carries what
# the core's VPN table holds and how much it moves — the detail behind each prefix lives in the looking glass itself.
LG = f'{L},job="lookingglass"'
p.append(row("BGP looking glass (route collector)", 69))
p.append(panel("Prefixes in the core (collector)", "stat", [(f'sum(lg_prefixes{{{L},source="collector",afi="ipv4"}})', "VPNv4"), (f'sum(lg_prefixes{{{L},source="collector",afi="ipv6"}})', "VPNv6")], 0, 70, 5, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Collector sessions to the reflectors", "stat", [(f'sum(lg_session_up{{{L}}})', "up"), (f'count(lg_session_up{{{L}}})', "sessions")], 5, 70, 4, 4, colorMode="value", thresholds={"steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}))
p.append(panel("Changes in the last hour", "stat", [(f'sum(lg_events_1h{{{L},kind="announce"}}) or vector(0)', "announce"), (f'sum(lg_events_1h{{{L},kind="change"}}) or vector(0)', "change"), (f'sum(lg_events_1h{{{L},kind="withdraw"}}) or vector(0)', "withdraw")], 9, 70, 6, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("History kept", "stat", [(f'lg_db_events{{{L}}}', "events"), (f'lg_db_paths{{{L}}}', "live paths"), (f'lg_db_bytes{{{L}}}', "database")], 15, 70, 5, 4, colorMode="value", thresholds={"steps": [{"color": "blue", "value": None}]},
               overrides=[{"matcher": {"id": "byName", "options": "database"}, "properties": [{"id": "unit", "value": "bytes"}]}]))
p.append(panel("Oldest collection (age)", "stat", [(f'max(lg_poll_age_seconds{{{L}}})', "age")], 20, 70, 4, 4, unit="s", colorMode="value", thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 300}, {"color": "red", "value": 900}]}))
p.append(panel("Paths the collector holds, per tenant VRF and family", "timeseries", [(f'lg_paths{{{L},source="collector"}}', "{{afi}} {{safi}} {{vrf}}")], 0, 74, 8, 8, min=0))
p.append(panel("Table movement (announce / change / withdraw per 5 min)", "timeseries", [(f'lg_events_5m{{{L}}}', "{{kind}}")], 8, 74, 8, 8, min=0))
p.append(panel("Collector sessions and per-router collections", "state-timeline", [(f'lg_session_up{{{L}}}', "session {{peer}}"), (f'lg_poll_ok{{{L}}}', "{{source}} ({{via}})")], 16, 74, 8, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
# what the looking glass reads from the routers themselves (their own HTTPS API) beside what the session delivers
p.append(panel("Read from the routers: RIB entries per router", "timeseries", [(f'sum by (source) (lg_paths{{{L},safi="rib"}})', "{{source}}")], 0, 82, 8, 7, min=0))
p.append(panel("Paths by how they were read", "timeseries", [(f'sum by (via) (lg_paths{{{L}}})', "{{via}}")], 8, 82, 8, 7, min=0))
p.append(panel("IS-IS adjacencies the routers report (through their API)", "timeseries", [(f'lg_isis_adjacencies_up{{{L}}}', "{{node}}")], 16, 82, 8, 7, min=0))
host_row(p, 89)
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
# configuration compliance: Nautobot Golden Config's verdict per router, exported by the portal (/api/compliance -> /metrics); the portal
# schedules a Golden Config run every GOLDEN_INTERVAL_HOURS, so drift surfaces here (and as the ConfigDrift alert) without an operator
p.append(row("Configuration compliance (Nautobot Golden Config, scheduled by the portal)", 57))
p.append(panel("Compliant vs drifted per router (1 = every feature matches the model)", "state-timeline", [(f"lab_config_compliance_ok{{{L}}}", "{{device}}")], 0, 58, 12, 7,
               mappings=[{"type": "value", "options": {"0": {"text": "drifted", "color": "red"}, "1": {"text": "compliant", "color": "green"}}}], thresholds=UPDOWN))
p.append(panel("Drifted compliance features per router", "bargauge", [(f"lab_config_noncompliant_features{{{L}}}", "{{device}}")], 12, 58, 6, 7, min=0, max=5, decimals=0,
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Hours since the last compliance run", "stat", [(f"(time() - lab_config_compliance_last_run_timestamp_seconds{{{L}}}) / 3600", "since last run")], 18, 58, 6, 4, unit="h", decimals=1,
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 7}, {"color": "red", "value": 13}]}))
p.append(panel("Routers compliant / scheduled every", "stat", [(f"sum(lab_config_compliance_ok{{{L}}})", "compliant"), (f"count(lab_config_compliance_ok{{{L}}})", "routers"), (f"lab_config_compliance_interval_seconds{{{L}}} / 3600", "interval (h)")], 18, 62, 6, 3, colorMode="none", decimals=0))

# the data-centre interconnect: it carries no tunnels, so its health is the NAT table, the aggregates the two sides exchange
# instead of their overlapping prefixes, and the two DNS zones (portal /metrics <- webapp/interconnect.py). The fix-up verdict
# is what the packet capture of the last test run saw on both sides of the DCI.
p.append(row("Data-centre interconnect: twice-NAT, aggregates and the DNS fix-up", 65))
p.append(panel("Static translations on the DCI (two per overlapping prefix)", "stat",
               [(f"lab_nat_static_translations{{{L}}}", "on the router"), (f"lab_nat_static_translations_expected{{{L}}}", "the model says")],
               0, 66, 5, 7, decimals=0, colorMode="none"))
p.append(panel("Static translations missing (router vs model)", "stat",
               [(f"lab_nat_static_translations_expected{{{L}}} - lab_nat_static_translations{{{L}}}", "missing")], 5, 66, 4, 7, decimals=0,
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("NAT table on the DCI", "timeseries",
               [(f"lab_nat_active_translations{{{L}}}", "active"), (f"lab_nat_static_translations{{{L}}}", "static")], 9, 66, 8, 7, min=0, decimals=0))
p.append(panel("Packets dropped for want of a translation (per 15 min)", "timeseries",
               [(f"increase(lab_nat_drops_total{{{L}}}[15m])", "{{direction}}")], 17, 66, 7, 7, min=0, decimals=0,
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Aggregates in the DCI's routing table (what crosses instead of the overlapping prefixes)", "state-timeline",
               [(f"lab_nat_aggregate_route{{{L}}}", "{{prefix}} ({{kind}}, {{source}})")], 0, 73, 12, 6,
               mappings=[{"type": "value", "options": {"0": {"text": "missing", "color": "red"}, "1": {"text": "routed", "color": "green"}}}], thresholds=UPDOWN))
p.append(panel("DNS records per zone (router vs model)", "timeseries",
               [(f"lab_dns_zone_records{{{L}}}", "{{zone}} on {{router}}"), (f"lab_dns_zone_records_expected{{{L}}}", "{{zone}} in the model")], 12, 73, 7, 6, min=0, decimals=0))
p.append(panel("DNS fix-up, last capture on both sides", "stat", [(f"lab_dns_fixup_ok{{{L}}}", "fix-up")], 19, 73, 5, 3,
               mappings=[{"type": "value", "options": {"0": {"text": "NOT translated", "color": "red"}, "1": {"text": "proven on the wire", "color": "green"}}}], thresholds=UPDOWN))
p.append(panel("Hours since that capture", "stat", [(f"(time() - lab_dns_fixup_timestamp_seconds{{{L}}}) / 3600", "since the capture")], 19, 76, 5, 3, unit="h", decimals=1,
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 24}, {"color": "red", "value": 72}]}))

p.append(row("Firewalls: forward filter (syslog -> VictoriaLogs)", 79))
p.append(panel("Dropped packets / 5 min per firewall (rule 900)", "timeseries", [logs_ts(FW + '"FWD-filter-900-D" | stats by (_time:5m, hostname) count() as drops', "{{hostname}}")], 0, 80, 8, 7, ds=VL, min=0))
p.append(panel("New flows accepted / 5 min per firewall (IKE, ESP, ICMP first packets)", "timeseries", [logs_ts(FW + '"-A]IN=" | stats by (_time:5m, hostname) count() as accepts', "{{hostname}}")], 8, 80, 8, 7, ds=VL, min=0))   # the accept tags end in -A]
p.append(panel("Drops / 5 min by source (all firewalls)", "timeseries", [logs_ts(FW + '"FWD-filter-900-D" ' + FWX + '| stats by (_time:5m, src) count() as drops', "{{src}}")], 16, 80, 8, 7, ds=VL, min=0))
p.append(panel("Top dropped flows (selected range)", "table", [(FW + '"FWD-filter-900-D" ' + FWX + '| stats by (hostname, in, out, src, dst, proto, dport) count() as hits | sort by (hits desc) | limit 20', "", {"queryType": "stats"})], 0, 87, 12, 9, ds=VL, columns=["hostname", "in", "out", "src", "dst", "proto", "dport"]))
p.append(panel("Firewall log (newest first)", "logs", [(FW, "")], 12, 87, 12, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))

y = host_row(p, 96)
p.append(row("Lab and runs", y))
p.append(panel("VMs running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, y + 1, 12, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 12, y + 1, 12, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "cat8000v-ipsec-overview.json").write_text(json.dumps(dashboard("cat8000v-ipsec-overview", "C8000v IPsec: overview", p, ["cat8000v-ipsec", "lab"], lab="cat8000v-ipsec"), indent=1))

# ---------------------------------------------------------------- c8000v-dmvpn-lab overview (the portal measures the C8000vs; the
# VyOS provider is scraped and pushes Telegraf; every router's syslog is in VictoriaLogs)
_id[0] = 0
L = 'lab="c8000v-dmvpn-lab"'
IOS = 'facility_keyword:local7 '                                     # IOS-XE syslog (origin-id hostname: the router is in the message)
IOSX = '| extract "<seq>: <router>: " '
p = []
p.append(row("The DMVPN cloud", 0))
p.append(panel("Health", "stat", [(f"lab_health_ok{{{L}}}", "health")], 0, 1, 4, 4, colorMode="background",
               mappings=[{"type": "value", "options": {"0": {"text": "problems", "color": "red"}, "1": {"text": "healthy", "color": "green"}}}], thresholds=UPDOWN))
p.append(panel("Registrations (customers x hubs)", "stat", [(f"sum(lab_dmvpn_nhs_up{{{L}}})", "up"), (f"sum(lab_dmvpn_nhs_expected{{{L}}})", "expected")], 4, 1, 5, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Sites peering with the provider", "stat", [(f"sum(lab_provider_customers_up{{{L}}})", "up"), (f"sum(lab_provider_customers_expected{{{L}}})", "expected")], 9, 1, 5, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Shortcuts live (customer pairs)", "stat", [(f"sum(lab_dmvpn_shortcuts{{{L}}}) / 2", "pairs")], 14, 1, 3, 4, colorMode="value", decimals=0, thresholds={"steps": [{"color": "blue", "value": None}]}))
p.append(panel("Last test run", "stat", [(f"lab_tests_last_passed{{{L}}}", "passed"), (f"lab_tests_last_failed{{{L}}}", "failed")], 17, 1, 4, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}]}}]}]))
p.append(panel("Firing alerts", "stat", [(f'count(ALERTS{{{L},alertstate="firing"}}) or vector(0)', "firing")], 21, 1, 3, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Customer registered with every hub (NHRP NHS up = expected)", "state-timeline", [(f"lab_dmvpn_nhs_up{{{L}}} == bool lab_dmvpn_nhs_expected{{{L}}}", "{{router}} ({{region}})")], 0, 5, 12, 7, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("eBGP session with the provider, per router", "state-timeline", [(f"lab_bgp_underlay_up{{{L}}} > bool 0", "{{router}}")], 12, 5, 12, 7, mappings=SITE_MAP, thresholds=UPDOWN))
p.append(panel("Registrations held by each hub", "timeseries", [(f"lab_dmvpn_registrations{{{L}}}", "{{router}}")], 0, 12, 8, 7, min=0, decimals=0))
p.append(panel("Customer-to-customer shortcuts (phase 3), per customer", "timeseries", [(f"lab_dmvpn_shortcuts{{{L}}}", "{{router}}")], 8, 12, 8, 7, min=0, decimals=0))
p.append(panel("IPsec sessions UP-ACTIVE per router", "timeseries", [(f"lab_ipsec_sessions_up{{{L}}}", "{{router}}")], 16, 12, 8, 7, min=0, decimals=0))
p.append(panel("Overlay iBGP: Established / sessions per router", "timeseries", [(f"lab_bgp_overlay_up{{{L}}}", "{{router}} up"), (f"lab_bgp_overlay_sessions{{{L}}}", "{{router}} sessions")], 0, 19, 12, 7, min=0, decimals=0,
               overrides=[{"matcher": {"id": "byRegexp", "options": ".* sessions"}, "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}]}]))
p.append(panel("C8000v CPU % (one-minute average)", "timeseries", [(f"lab_router_cpu_pct{{{L}}}", "{{router}}")], 12, 19, 12, 7, unit="percent", min=0, max=100))

p.append(row("The provider (VyOS: exporters and Telegraf)", 26))
p.append(panel("Sites peering with the provider", "timeseries", [(f"lab_provider_customers_up{{{L}}}", "up"), (f"lab_provider_customers_expected{{{L}}}", "expected")], 0, 27, 8, 7, min=0, decimals=0))
p.append(panel("Provider CPU busy %", "timeseries", [(f'100 * (1 - avg by (node) (rate(node_cpu_seconds_total{{{L},node="mpls",mode="idle"}}[2m])))', "{{node}}")], 8, 27, 8, 7, unit="percent", min=0, max=100))
p.append(panel("Access links: traffic per port (bit/s)", "timeseries", [(f'8 * rate(node_network_receive_bytes_total{{{L},node="mpls",device=~"eth([1-9]|1[0-2])"}}[2m])', "{{device}} in"), (f'8 * rate(node_network_transmit_bytes_total{{{L},node="mpls",device=~"eth([1-9]|1[0-2])"}}[2m])', "{{device}} out")], 16, 27, 8, 7, unit="bps", min=0))

p.append(row("Router syslog (VictoriaLogs)", 34))
p.append(panel("BGP neighbour Down events / 5 min per router", "timeseries", [logs_ts(IOS + '"%BGP-5-ADJCHANGE" "Down" ' + IOSX + '| stats by (_time:5m, router) count() as downs', "{{router}}")], 0, 35, 8, 7, ds=VL, min=0))
p.append(panel("NHRP / IKE / IPsec events / 5 min per router", "timeseries", [logs_ts(IOS + '("%DMVPN-" OR "%NHRP-" OR "%IKEV2-" OR "%CRYPTO-") ' + IOSX + '| stats by (_time:5m, router) count() as events', "{{router}}")], 8, 35, 8, 7, ds=VL, min=0))
p.append(panel("Syslog lines / 5 min per router", "timeseries", [logs_ts(IOS + IOSX + '| stats by (_time:5m, router) count() as lines', "{{router}}"), logs_ts('hostname:mpls | stats by (_time:5m) count() as lines', "mpls")], 16, 35, 8, 7, ds=VL, min=0))
p.append(panel("C8000v syslog (newest first)", "logs", [(IOS, "")], 0, 42, 24, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))

y = host_row(p, 51)
p.append(row("Lab and runs", y))
p.append(panel("VMs running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, y + 1, 8, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("LAN hosts answering", "state-timeline", [(f"lab_host_up{{{L}}}", "{{host}}")], 8, y + 1, 8, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 16, y + 1, 8, 9, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "c8000v-dmvpn-lab-overview.json").write_text(json.dumps(dashboard("c8000v-dmvpn-lab-overview", "C8000v DMVPN: overview", p, ["c8000v-dmvpn-lab", "lab"], lab="c8000v-dmvpn-lab"), indent=1))

# ---------------------------------------------------------------- evpn-fabric overview (EVPN/VXLAN leaf-spine on VyOS: the portal measures the
# fabric — sessions, VNIs, segments, default routes — every node is scraped and pushes Telegraf, every node's syslog is in VictoriaLogs)
_id[0] = 0
L = 'lab="evpn-fabric"'
EVH = '-lab:evpn-clab -lab:srl-evpn -lab:evpn-pfsense hostname:~"^(spine[0-9]+|leaf[0-9]+|border[0-9]+|fw-ext)$" '   # the other EVPN labs have the same hostnames: their syslog carries their lab
HEALTH3 = [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "0.5": {"text": "degraded", "color": "orange"}, "1": {"text": "ok", "color": "green"}}}]
p = []
p.append(row("The fabric", 0))
p.append(panel("Nodes healthy", "stat", [(f"count(lab_node_health{{{L}}} == 1) or vector(0)", "ok"), (f"count(lab_node_health{{{L}}})", "nodes")], 0, 1, 5, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Fabric BGP sessions", "stat", [(f'sum(lab_bgp_sessions_up{{{L},afi="ipv4"}})', "up"), (f"sum(lab_bgp_sessions_expected{{{L}}})", "expected")], 5, 1, 5, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Defaults via both borders (leaf x tenant)", "stat", [(f'count(lab_default_route_paths{{{L},role="leaf"}} >= 2) or vector(0)', "both"), (f'count(lab_default_route_paths{{{L},role="leaf"}})', "of")], 10, 1, 5, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Last test run", "stat", [(f"lab_tests_last_passed{{{L}}}", "passed"), (f"lab_tests_last_failed{{{L}}}", "failed")], 15, 1, 5, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}]}}]}]))
p.append(panel("Firing alerts", "stat", [(f'count(ALERTS{{{L},alertstate="firing",severity!="info"}}) or vector(0)', "firing")], 20, 1, 4, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Node health (the portal's view)", "state-timeline", [(f"lab_node_health{{{L}}}", "{{node}}")], 0, 5, 24, 9, mappings=HEALTH3,
               thresholds={"steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 1}]}))

p.append(row("Underlay and overlay", 14))
p.append(panel("Fabric BGP sessions Established per node (IPv4 / EVPN)", "timeseries", [(f'lab_bgp_sessions_up{{{L},afi="ipv4"}}', "{{node}}")], 0, 15, 8, 8, min=0, decimals=0))
p.append(panel("BFD peers up per node", "timeseries", [(f"lab_bfd_peers_up{{{L}}}", "{{node}}")], 8, 15, 8, 8, min=0, decimals=0))
p.append(panel("Remote VTEPs per L2VNI (each leaf should see the other 5)", "timeseries", [(f"min by (vni) (lab_evpn_l2vni_remote_vteps{{{L}}})", "VNI {{vni}} (worst leaf)")], 16, 15, 8, 8, min=0, decimals=0))

p.append(row("Multihoming and the way out", 23))
p.append(panel("Designated forwarder per Ethernet Segment", "state-timeline", [(f"lab_es_df{{{L}}}", "{{node}} {{esi}}")], 0, 24, 12, 8,
               mappings=[{"type": "value", "options": {"0": {"text": "non-DF", "color": "blue"}, "1": {"text": "DF", "color": "green"}}}]))
p.append(panel("Server LACP legs up", "timeseries", [(f"lab_server_bond_legs_up{{{L}}}", "{{node}}")], 12, 24, 6, 8, min=0, max=2, decimals=0))
p.append(panel("fw-ext sessions to the borders", "state-timeline", [(f"lab_edge_session_up{{{L}}}", "{{border_ip}}")], 18, 24, 6, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Default-route paths per leaf and tenant (2 = both borders)", "timeseries", [(f'lab_default_route_paths{{{L},role="leaf"}}', "{{node}} {{tenant}}")], 0, 32, 12, 7, min=0, max=2, decimals=0))
p.append(panel("Fabric traffic: busiest ports (bit/s)", "timeseries", [(f'topk(8, 8 * rate(node_network_transmit_bytes_total{{{L},device=~"eth[1-8]|bond[0-9]+"}}[2m]))', "{{node}} {{device}} out")], 12, 32, 12, 7, unit="bps", min=0))

p.append(row("Node syslog (VictoriaLogs)", 39))
p.append(panel("BGP / BFD events / 5 min per node", "timeseries", [logs_ts(EVH + '(app_name:bgpd "%ADJCHANGE") OR (app_name:bfdd "state-change") | uniq by (_time, hostname, _msg) | stats by (_time:5m, hostname) count() as events', "{{hostname}}")], 0, 40, 12, 7, ds=VL, min=0))
p.append(panel("Syslog lines / 5 min per node", "timeseries", [logs_ts(EVH + '| stats by (_time:5m, hostname) count() as lines', "{{hostname}}")], 12, 40, 12, 7, ds=VL, min=0))
p.append(panel("Fabric syslog: FRR, commits, protodown (newest first)", "logs", [(EVH + '(app_name:bgpd OR app_name:bfdd OR app_name:zebra OR app_name:watchfrr OR app_name:commit OR app_name:evpn-fabric-protodown)', "")], 0, 47, 24, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))

y = host_row(p, 56)
p.append(row("Lab and runs", y))
p.append(panel("VMs running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, y + 1, 12, 10, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 12, y + 1, 12, 10, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "evpn-fabric-overview.json").write_text(json.dumps(dashboard("evpn-fabric-overview", "EVPN fabric: overview", p, ["evpn-fabric", "lab"], lab="evpn-fabric"), indent=1))

# ---------------------------------------------------------------- evpn-clab overview (EVPN/VXLAN leaf-spine on VyOS containers, containerlab:
# OSPF underlay, iBGP EVPN to the spines; the portal :8096 measures it, every node is scraped and pushes Telegraf, its syslog
# carries lab=evpn-clab — the hostnames are evpn-fabric's, so every log query filters on that field)
_id[0] = 0
L = 'lab="evpn-clab"'
ECH = 'lab:evpn-clab -type:SFLOW_5 '   # its syslog; its sFlow records (lab=evpn-clab too) have type SFLOW_5
p = []
p.append(row("The fabric", 0))
p.append(panel("Nodes healthy", "stat", [(f"count(lab_node_health{{{L}}} == 1) or vector(0)", "ok"), (f"count(lab_node_health{{{L}}})", "nodes")], 0, 1, 4, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("OSPF adjacencies Full", "stat", [(f"sum(lab_ospf_neighbors_full{{{L}}})", "full"), (f"sum(lab_ospf_neighbors_expected{{{L}}})", "expected")], 4, 1, 4, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("EVPN sessions", "stat", [(f'sum(lab_bgp_sessions_up{{{L},afi="evpn"}})', "up"), (f'sum(lab_bgp_sessions_expected{{{L},afi="evpn"}})', "expected")], 8, 1, 4, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Links up", "stat", [(f"sum(lab_link_up{{{L}}})", "up"), (f"count(lab_link_up{{{L}}})", "links")], 12, 1, 4, 4, colorMode="value", thresholds={"steps": [{"color": "green", "value": None}]}))
p.append(panel("Last verify", "stat", [(f"lab_verify_checks_passed{{{L}}}", "passed"), (f"lab_verify_checks_total{{{L}}} - lab_verify_checks_passed{{{L}}}", "failed")], 16, 1, 4, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}]}}]}]))
p.append(panel("Firing alerts", "stat", [(f'count(ALERTS{{{L},alertstate="firing",severity!="info"}}) or vector(0)', "firing")], 20, 1, 4, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Node health (the portal's view)", "state-timeline", [(f"lab_node_health{{{L}}}", "{{node}}")], 0, 5, 24, 8, mappings=HEALTH3,
               thresholds={"steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 1}]}))

p.append(row("Underlay (OSPF, BFD) and overlay (EVPN)", 13))
p.append(panel("OSPF adjacencies Full per node", "timeseries", [(f"lab_ospf_neighbors_full{{{L}}}", "{{node}}")], 0, 14, 8, 8, min=0, decimals=0))
p.append(panel("BFD sessions up per node", "timeseries", [(f"lab_bfd_peers_up{{{L}}}", "{{node}}")], 8, 14, 8, 8, min=0, decimals=0))
p.append(panel("EVPN / edge BGP sessions Established", "timeseries", [(f"lab_bgp_sessions_up{{{L}}}", "{{node}} {{afi}}")], 16, 14, 8, 8, min=0, decimals=0))
p.append(panel("Remote VTEPs per L2VNI (each leaf should see the other 3)", "timeseries", [(f"min by (vni) (lab_evpn_l2vni_remote_vteps{{{L}}})", "VNI {{vni}} (worst leaf)")], 0, 22, 8, 7, min=0, decimals=0))
p.append(panel("MACs per VNI (all VTEPs)", "timeseries", [(f"max by (vni, type) (lab_evpn_vni_macs{{{L}}})", "{{type}} VNI {{vni}}")], 8, 22, 8, 7, min=0, decimals=0))
p.append(panel("Default-route paths per leaf and tenant (2 = both borders)", "timeseries", [(f"lab_default_route_paths{{{L}}}", "{{node}} {{tenant}}")], 16, 22, 8, 7, min=0, max=2, decimals=0))
p.append(panel("Links (the portal's view)", "state-timeline", [(f"lab_link_up{{{L}}}", "{{link}}")], 0, 29, 12, 12, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Fabric traffic: busiest ports (bit/s)", "timeseries", [(f'topk(8, 8 * rate(node_network_transmit_bytes_total{{{L},device=~"eth[1-9]"}}[2m]))', "{{node}} {{device}} out")], 12, 29, 12, 12, unit="bps", min=0))

p.append(row("Node syslog (VictoriaLogs, lab=evpn-clab)", 41))
p.append(panel("OSPF / BGP / BFD events / 5 min per node", "timeseries", [logs_ts(ECH + '((app_name:bgpd "%ADJCHANGE") OR (app_name:ospfd "AdjChg") OR (app_name:bfdd "state-change")) | uniq by (_time, hostname, _msg) | stats by (_time:5m, hostname) count() as events', "{{hostname}}")], 0, 42, 12, 7, ds=VL, min=0))
p.append(panel("Syslog lines / 5 min per node", "timeseries", [logs_ts(ECH + '| stats by (_time:5m, hostname) count() as lines', "{{hostname}}")], 12, 42, 12, 7, ds=VL, min=0))
p.append(panel("Fabric syslog: FRR and commits (newest first)", "logs", [(ECH + '(app_name:bgpd OR app_name:ospfd OR app_name:bfdd OR app_name:zebra OR app_name:watchfrr OR app_name:commit)', "")], 0, 49, 24, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))

p.append(row("Containers (Telegraf)", 58))
p.append(panel("CPU busy per node (%)", "timeseries", [(f'100 - cpu_usage_idle{{{L},cpu="cpu-total"}}', "{{host}}")], 0, 59, 12, 7, unit="percent", min=0))
p.append(panel("Memory used per node", "timeseries", [(f"mem_used{{{L}}}", "{{host}}")], 12, 59, 12, 7, unit="bytes", min=0))

p.append(row("EVPN multihoming (dual-homed hosts)", 66))
p.append(panel("Designated forwarder per Ethernet Segment", "state-timeline", [(f"lab_es_df{{{L}}}", "{{host}} on {{node}}")], 0, 67, 10, 8,
               mappings=[{"type": "value", "options": {"0": {"text": "non-DF", "color": "blue"}, "1": {"text": "DF", "color": "green"}}}]))
p.append(panel("Hosts' LACP legs in the active aggregator", "timeseries", [(f"lab_host_bond_legs_up{{{L}}}", "{{node}}")], 10, 67, 7, 8, min=0, max=2, decimals=0))
p.append(panel("Segment VTEPs seen per leaf (2 = aliasing through both)", "timeseries", [(f"min by (host) (lab_es_vteps{{{L}}})", "{{host}} (worst leaf)")], 17, 67, 7, 8, min=0, max=2, decimals=0))

p.append(row("The wan's firewall (zone pairs: tenants, internet, the wan itself)", 75))
p.append(panel("Dropped packets/s per zone pair", "timeseries", [(f'sum by (from, to) (rate(lab_firewall_packets_total{{{L},action="drop"}}[2m]))', "{{from}} → {{to}}")], 0, 76, 12, 8, unit="pps", min=0))
p.append(panel("Allowed new connections/s per rule (replies excluded)", "timeseries", [(f'sum by (from, to, rule) (rate(lab_firewall_packets_total{{{L},action="accept",rule!="1"}}[2m]))', "{{from}} → {{to}} rule {{rule}}")], 12, 76, 12, 8, unit="pps", min=0))

p.append(row("Throughput (the last throughput tests, iperf3 TCP)", 84))
p.append(panel("Mbit/s per kind of path", "bargauge", [(f"lab_throughput_mbps{{{L}}}", "{{path}}")], 0, 85, 10, 7, unit="Mbits", min=0, orientation="horizontal"))
p.append(panel("Dual-homed hosts: share of each leg (16 streams)", "bargauge", [(f"lab_throughput_leg_share{{{L}}}", "{{host}} {{direction}} {{leg}}")], 10, 85, 9, 7, unit="percentunit", min=0, max=1, orientation="horizontal"))
p.append(panel("Stall when a leg is cut under load", "stat", [(f"lab_throughput_failover_stall_seconds{{{L}}}", "stalled")], 19, 85, 5, 7, unit="s", colorMode="value",
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "orange", "value": 0.5}, {"color": "red", "value": 1.5}]}))

y = host_row(p, 92)
p.append(row("Lab and runs", y))
p.append(panel("Containers running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, y + 1, 12, 10, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 12, y + 1, 12, 10, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "evpn-clab-overview.json").write_text(json.dumps(dashboard("evpn-clab-overview", "EVPN clab: overview", p, ["evpn-clab", "lab"], lab="evpn-clab"), indent=1))

# ---------------------------------------------------------------- evpn-pfsense overview (EVPN on VyOS containers, four tenant VRFs in two domains:
# red/blue routed only through a pfSense HA pair, green/yellow only through an OPNsense pair, KVM). The portal :8098 is the only source: the fabric through docker exec, the firewalls over SSH — CARP per VIP,
# BGP and BFD with the borders, pfsync, pf's states, counters and per-rule packets. No exporters, no syslog (the firewalls' management
# network is isolated from the NMS)
_id[0] = 0
L = 'lab="evpn-pfsense"'
G1 = {"steps": [{"color": "green", "value": None}]}
CARP_MAP = [{"type": "value", "options": {"0": {"text": "BACKUP", "color": "blue"}, "1": {"text": "MASTER", "color": "green"}}}]
p = []
p.append(row("The lab", 0))
p.append(panel("Nodes healthy", "stat", [(f'count(lab_node_health{{{L},role!="firewall"}} == 1) or vector(0)', "ok"), (f'count(lab_node_health{{{L},role!="firewall"}})', "nodes")], 0, 1, 4, 4, colorMode="value", thresholds=G1))
p.append(panel("Firewalls healthy", "stat", [(f'count(lab_node_health{{{L},role="firewall"}} == 1) or vector(0)', "ok"), (f'count(lab_node_health{{{L},role="firewall"}})', "firewalls")], 4, 1, 4, 4, colorMode="value", thresholds=G1))
p.append(panel("CARP masters per VIP (want 1)", "stat", [(f"sum by (pair, vip) (lab_carp_master{{{L}}})", "{{pair}} {{vip}}")], 8, 1, 4, 4, colorMode="background", decimals=0,
               thresholds={"steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}, {"color": "red", "value": 2}]}))
p.append(panel("Links up", "stat", [(f"sum(lab_link_up{{{L}}})", "up"), (f"count(lab_link_up{{{L}}})", "links")], 12, 1, 4, 4, colorMode="value", thresholds=G1))
p.append(panel("Last verify", "stat", [(f"lab_verify_checks_passed{{{L}}}", "passed"), (f"lab_verify_checks_total{{{L}}} - lab_verify_checks_passed{{{L}}}", "failed")], 16, 1, 4, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": G1}]}]))
p.append(panel("Firing alerts", "stat", [(f'count(ALERTS{{{L},alertstate="firing",severity!="info"}}) or vector(0)', "firing")], 20, 1, 4, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Node health (the portal's view)", "state-timeline", [(f"lab_node_health{{{L}}}", "{{node}}")], 0, 5, 24, 9, mappings=HEALTH3,
               thresholds={"steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0.5}, {"color": "green", "value": 1}]}))

p.append(row("The firewall pairs (pfSense: red, blue · OPNsense: green, yellow — CARP, BGP and BFD with the borders, pfsync, pf)", 14))
p.append(panel("CARP state per firewall and VIP (MASTER should be pf1 / opn1)", "state-timeline", [(f"lab_carp_master{{{L}}}", "{{node}} {{vip}}")], 0, 15, 12, 8, mappings=CARP_MAP,
               thresholds={"steps": [{"color": "blue", "value": None}, {"color": "green", "value": 1}]}))
p.append(panel("BGP with the borders (per firewall, border, tenant)", "state-timeline", [(f"lab_firewall_bgp_up{{{L}}}", "{{node}} ↔ {{border}} {{tenant}}")], 12, 15, 12, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("BFD sessions up per firewall", "timeseries", [(f"lab_firewall_bfd_peers_up{{{L}}}", "{{node}} up"), (f"lab_firewall_bfd_peers_total{{{L}}}", "{{node}} configured")], 0, 23, 6, 7, min=0, decimals=0))
p.append(panel("pf state table entries", "timeseries", [(f"lab_pf_states{{{L}}}", "{{node}}")], 6, 23, 6, 7, min=0, decimals=0))
p.append(panel("State mismatches / 5 min (streams dropped as out of window)", "timeseries", [(f'increase(lab_pf_counter_total{{{L},counter="state-mismatch"}}[5m])', "{{node}}")], 12, 23, 6, 7, min=0, decimals=0))
p.append(panel("Kernel routes / pfsync maxupd", "state-timeline", [(f"lab_firewall_kernel_ok{{{L}}}", "{{node}} kernel routes"), (f"lab_pfsync_maxupd{{{L}}} == bool 1", "{{node}} maxupd 1")], 18, 23, 6, 7, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("The policy: packets/s per rule (each pair, both firewalls)", "timeseries", [(f'sum by (pair, rule) (rate(lab_firewall_packets_total{{{L},rule=~".*->.*"}}[2m]))', "{{pair}}: {{rule}}")], 0, 30, 12, 8, unit="pps", min=0))
p.append(panel("Blocked between tenants (packets/s)", "timeseries", [(f'sum by (rule, node) (rate(lab_firewall_packets_total{{{L},rule=~".*blocked"}}[2m]))', "{{node}} {{rule}}")], 12, 30, 6, 8, unit="pps", min=0))
p.append(panel("pf counters / 5 min", "timeseries", [(f'increase(lab_pf_counter_total{{{L},counter!~"match|state-insert"}}[5m]) > 0', "{{node}} {{counter}}")], 18, 30, 6, 8, min=0, decimals=0))

p.append(row("Underlay (OSPF, BFD) and overlay (EVPN)", 38))
p.append(panel("OSPF adjacencies Full per node", "timeseries", [(f"lab_ospf_neighbors_full{{{L}}}", "{{node}}")], 0, 39, 8, 8, min=0, decimals=0))
p.append(panel("BFD sessions up per node (borders: the firewalls too)", "timeseries", [(f"lab_bfd_peers_up{{{L}}}", "{{node}}")], 8, 39, 8, 8, min=0, decimals=0))
p.append(panel("EVPN / border–firewall BGP sessions Established", "timeseries", [(f"lab_bgp_sessions_up{{{L}}}", "{{node}} {{afi}}")], 16, 39, 8, 8, min=0, decimals=0))
p.append(panel("Remote VTEPs per L2VNI (each leaf should see the other 3)", "timeseries", [(f"min by (vni) (lab_evpn_l2vni_remote_vteps{{{L}}})", "VNI {{vni}} (worst leaf)")], 0, 47, 8, 7, min=0, decimals=0))
p.append(panel("MACs per VNI (all VTEPs)", "timeseries", [(f"max by (vni, type) (lab_evpn_vni_macs{{{L}}})", "{{type}} VNI {{vni}}")], 8, 47, 8, 7, min=0, decimals=0))
p.append(panel("Default-route paths per leaf and tenant (2 = both borders)", "timeseries", [(f"lab_default_route_paths{{{L}}}", "{{node}} {{tenant}}")], 16, 47, 8, 7, min=0, max=2, decimals=0))
p.append(panel("Links (the portal's view)", "state-timeline", [(f"lab_link_up{{{L}}}", "{{link}}")], 0, 54, 24, 12, mappings=UPDOWN_MAP, thresholds=UPDOWN))

p.append(row("EVPN multihoming (dual-homed hosts)", 66))
p.append(panel("Designated forwarder per Ethernet Segment", "state-timeline", [(f"lab_es_df{{{L}}}", "{{host}} on {{node}}")], 0, 67, 10, 8,
               mappings=[{"type": "value", "options": {"0": {"text": "non-DF", "color": "blue"}, "1": {"text": "DF", "color": "green"}}}]))
p.append(panel("Hosts' LACP legs in the active aggregator", "timeseries", [(f"lab_host_bond_legs_up{{{L}}}", "{{node}}")], 10, 67, 7, 8, min=0, max=2, decimals=0))
p.append(panel("Segment VTEPs seen per leaf (2 = aliasing through both)", "timeseries", [(f"min by (host) (lab_es_vteps{{{L}}})", "{{host}} (worst leaf)")], 17, 67, 7, 8, min=0, max=2, decimals=0))

PSL = 'lab:evpn-pfsense -type:SFLOW_5 '   # its syslog (the VyOS nodes, and the four firewalls through the host's relay)
p.append(row("Syslog (VictoriaLogs, lab=evpn-pfsense: the switches and the four firewalls)", 75))
p.append(panel("BGP / OSPF / BFD / CARP events / 5 min per node", "timeseries", [logs_ts(PSL + '((app_name:bgpd "%ADJCHANGE") OR (app_name:ospfd "AdjChg") OR (app_name:bfdd "state-change") OR ("carp:" "->")) | uniq by (_time, hostname, _msg) | stats by (_time:5m, hostname) count() as events', "{{hostname}}")], 0, 76, 12, 7, ds=VL, min=0))
p.append(panel("Syslog lines / 5 min per node", "timeseries", [logs_ts(PSL + '| stats by (_time:5m, hostname) count() as lines', "{{hostname}}")], 12, 76, 12, 7, ds=VL, min=0))
p.append(panel("The firewalls' syslog (pfSense, OPNsense — newest first)", "logs", [(PSL + 'hostname:~"^(pf|opn)[0-9]+([.].*)?$" -app_name:filterlog', "")], 0, 83, 12, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))
p.append(panel("The fabric's syslog: FRR and commits (newest first)", "logs", [(PSL + '(app_name:bgpd OR app_name:ospfd OR app_name:bfdd OR app_name:zebra OR app_name:watchfrr OR app_name:commit)', "")], 12, 83, 12, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))

p.append(row("Lab and runs", 92))
p.append(panel("Containers and firewall VMs running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, 93, 12, 10, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 12, 93, 12, 10, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "evpn-pfsense-overview.json").write_text(json.dumps(dashboard("evpn-pfsense-overview", "EVPN pfSense: overview", p, ["evpn-pfsense", "lab"], lab="evpn-pfsense"), indent=1))

# ---------------------------------------------------------------- mesh-lab overview (a service mesh on k3d: the same shop under no mesh, Istio ambient,
# Istio sidecars or Linkerd — and, on a second cluster, Cilium; the variable `cluster` picks one). The portal :8099 is the only source, through kubectl: the mesh and policy level, the shop's Deployments and whether
# their pods are in the data plane, the mesh's own components, the access matrix (every probe pod against every service), each mesh's latency
_id[0] = 0
L = 'lab="mesh-lab",cluster=~"$cluster"'     # two clusters (each its own portal, scraped with a cluster label): main and cilium
MLV = [{"name": "cluster", "label": "cluster", "type": "custom", "query": "main,cilium", "includeAll": True, "multi": False,
        "allValue": ".*", "current": {"text": "main", "value": "main"},
        "options": [{"text": t, "value": v, "selected": v == "main"} for t, v in (("All", "$__all"), ("main", "main"), ("cilium", "cilium"))]}]
p = []
p.append(row("The lab", 0))
p.append(panel("Mesh / policy level", "stat", [(f"lab_mesh_info{{{L}}}", "{{mesh}} · {{level}}")], 0, 1, 6, 4, colorMode="none", textMode="name"))
p.append(panel("Access matrix: cells not as the policy says", "stat", [(f"lab_access_mismatches{{{L}}}", "unexpected")], 6, 1, 5, 4, colorMode="background", decimals=0,
               thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Deployments in the mesh", "stat", [(f"count(lab_deploy_in_mesh{{{L}}} == 1) or vector(0)", "in mesh"), (f"count(lab_deploy_in_mesh{{{L}}})", "deployments")], 11, 1, 5, 4, colorMode="value", thresholds=G1))
p.append(panel("Last tests", "stat", [(f"lab_tests_last_passed{{{L}}}", "passed"), (f"lab_tests_last_failed{{{L}}}", "failed")], 16, 1, 4, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": G1}]}]))
p.append(panel("Firing alerts", "stat", [(f'count(ALERTS{{{L},alertstate="firing",severity!="info"}}) or vector(0)', "firing")], 20, 1, 4, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Mesh over time", "state-timeline", [(f"lab_mesh_info{{{L}}}", "{{mesh}} · {{level}}")], 0, 5, 24, 5, mappings=[{"type": "value", "options": {"1": {"text": "deployed", "color": "blue"}}}]))

p.append(row("Access (every probe pod against every shop service, at the protocol level)", 10))
p.append(panel("Each cell as the policy says? (probe -> service)", "state-timeline", [(f"lab_access_as_expected{{{L}}}", "{{probe}} -> {{service}} ({{expected}})")], 0, 11, 16, 18,
               mappings=[{"type": "value", "options": {"0": {"text": "unexpected", "color": "red"}, "1": {"text": "as expected", "color": "green"}}}], thresholds=UPDOWN))
p.append(panel("Answered (allowed) per probe", "timeseries", [(f"sum by (probe) (lab_access_answered{{{L}}})", "{{probe}}")], 16, 11, 8, 9, min=0, decimals=0))
p.append(panel("Unexpected cells", "timeseries", [(f"lab_access_mismatches{{{L}}}", "unexpected")], 16, 20, 8, 9, min=0, decimals=0))

p.append(row("Workloads and the mesh's own components", 29))
p.append(panel("Shop pods in the data plane (per Deployment)", "state-timeline", [(f"lab_deploy_in_mesh{{{L}}}", "{{deploy}}")], 0, 30, 12, 10,
               mappings=[{"type": "value", "options": {"0": {"text": "outside", "color": "orange"}, "1": {"text": "in mesh", "color": "green"}}}], thresholds=UPDOWN))
p.append(panel("Ready replicas per Deployment", "timeseries", [(f"lab_deploy_ready_replicas{{{L}}}", "{{deploy}}")], 12, 30, 6, 10, min=0, decimals=0))
p.append(panel("Mesh components ready / wanted", "timeseries", [(f"lab_mesh_component_ready{{{L}}}", "{{component}} ready"), (f"lab_mesh_component_want{{{L}}}", "{{component}} wanted")], 18, 30, 6, 10, min=0, decimals=0))

p.append(row("Latency each mesh adds (tools/bench.py: the page is a fan-out to six services, the hop one gRPC call)", 40))
p.append(panel("p99 at each mesh's last benchmark", "bargauge", [(f'lab_bench_latency_ms{{{L},quantile="p99"}}', "{{mesh}} {{target}}")], 0, 41, 12, 9, unit="ms", min=0))
p.append(panel("p50 at each mesh's last benchmark", "bargauge", [(f'lab_bench_latency_ms{{{L},quantile="p50"}}', "{{mesh}} {{target}}")], 12, 41, 12, 9, unit="ms", min=0))

p.append(row("Releases (a share of productcatalog routed to a candidate build) and outages (one service down: which pages still work)", 50))
p.append(panel("% routed to the candidate", "timeseries", [(f"lab_release_weight{{{L}}}", "{{candidate}}")], 0, 51, 8, 8, min=0, max=100, unit="percent", decimals=0))
p.append(panel("Home pages sampled, by the build that answered", "timeseries", [(f"lab_release_pages{{{L}}}", "{{build}}")], 8, 51, 8, 8, min=0, decimals=0, custom={"stacking": {"mode": "normal"}, "fillOpacity": 60},
               overrides=[{"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
                          for n, c in (("v1", "#64748b"), ("v2", "blue"), ("error", "red"))]))
p.append(panel("Home page latency during the release", "timeseries", [(f"lab_release_page_ms{{{L}}}", "{{quantile}}")], 16, 51, 8, 8, unit="ms", min=0))
p.append(panel("Outage table: page still works with the service down", "state-timeline", [(f"lab_outage_page_ok{{{L}}}", "{{service}} down: {{page}}")], 0, 59, 16, 14,
               mappings=[{"type": "value", "options": {"0": {"text": "broken", "color": "red"}, "1": {"text": "works", "color": "green"}}}], thresholds=UPDOWN))
p.append(panel("Recovery after each outage", "bargauge", [(f"lab_outage_recovery_seconds{{{L}}}", "{{service}}")], 16, 59, 8, 10, unit="s", min=0))
p.append(panel("Last progressive rollout", "stat", [(f"lab_release_rollout_rolled_back{{{L}}}", "{{candidate}} on {{mesh}}")], 16, 69, 8, 4, colorMode="background",
               mappings=[{"type": "value", "options": {"0": {"text": "completed", "color": "green"}, "1": {"text": "rolled back", "color": "orange"}}}]))

p.append(row("Lab and runs", 73))
p.append(panel("Node containers running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, 74, 12, 7, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 12, 74, 12, 7, mappings=UPDOWN_MAP, thresholds=UPDOWN))
(OUT / "mesh-lab-overview.json").write_text(json.dumps(dashboard("mesh-lab-overview", "mesh-lab: overview", p, ["mesh-lab", "lab"], variables=MLV, lab="mesh-lab"), indent=1))

# ---------------------------------------------------------------- evpn-fabric: Kubernetes on the fabric (k3s + Cilium on the k8s-* nodes: every
# node peers BGP with its leaves; the portal measures nodes / sessions / services, Cilium's agents and Hubble are scraped on every node)
_id[0] = 0
K = 'lab="evpn-fabric"'
KN = 'lab="evpn-fabric",role="k8s"'
UP1 = [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "1": {"text": "up", "color": "green"}}}]
READY = [{"type": "value", "options": {"0": {"text": "NotReady", "color": "red"}, "1": {"text": "Ready", "color": "green"}}}]
GREEN = {"steps": [{"color": "green", "value": None}]}
p = []
p.append(row("The cluster", 0))
p.append(panel("Nodes Ready", "stat", [(f"count(lab_k8s_node_ready{{{K}}} == 1) or vector(0)", "Ready"), (f"count(lab_k8s_node_ready{{{K}}})", "nodes")], 0, 1, 5, 4, colorMode="value", thresholds=GREEN))
p.append(panel("BGP sessions to the leaves", "stat", [(f"sum(cilium_bgp_control_plane_session_state{{{KN}}})", "up"), (f"count(cilium_bgp_control_plane_session_state{{{KN}}})", "configured")], 5, 1, 5, 4, colorMode="value", thresholds=GREEN))
p.append(panel("Running pods", "stat", [(f"sum(lab_k8s_node_pods{{{K}}})", "pods")], 10, 1, 4, 4, colorMode="value", thresholds=GREEN))
p.append(panel("LoadBalancer services announced", "stat", [(f"count(lab_k8s_lb_service_announcers{{{K}}} > 0) or vector(0)", "announced"), (f"count(lab_k8s_lb_service_announcers{{{K}}})", "services")], 14, 1, 5, 4, colorMode="value", thresholds=GREEN))
p.append(panel("Kubernetes alerts firing", "stat", [(f'count(ALERTS{{{K},alertname=~"EvpnK8s.*",alertstate="firing"}}) or vector(0)', "firing")], 19, 1, 5, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Node Ready", "state-timeline", [(f"lab_k8s_node_ready{{{K}}}", "{{node}}")], 0, 5, 12, 7, mappings=READY, thresholds={"steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}))
p.append(panel("Running pods per node", "timeseries", [(f"lab_k8s_node_pods{{{K}}}", "{{node}}")], 12, 5, 12, 7, min=0, decimals=0))

p.append(row("BGP: the nodes and their leaves", 12))
p.append(panel("Sessions per node and leaf (IPv4 and IPv6)", "state-timeline", [(f"cilium_bgp_control_plane_session_state{{{KN}}}", "{{node}} → {{neighbor}} (AS {{neighbor_asn}})")], 0, 13, 12, 12, mappings=UP1, thresholds={"steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}))
p.append(panel("Routes each node announces (pod CIDR + LoadBalancer addresses)", "timeseries", [(f"max by (node, afi) (cilium_bgp_control_plane_advertised_routes{{{KN}}})", "{{node}} {{afi}}")], 12, 13, 12, 6, min=0, decimals=0))
p.append(panel("Nodes announcing each LoadBalancer service", "timeseries", [(f"lab_k8s_lb_service_announcers{{{K}}}", "{{service}} {{address}}")], 12, 19, 12, 6, min=0, decimals=0))

p.append(row("Flows (Hubble)", 25))
p.append(panel("Flows / s by verdict", "timeseries", [(f"sum by (verdict) (rate(hubble_flows_processed_total{{{KN}}}[2m]))", "{{verdict}}")], 0, 26, 8, 8, unit="ops", min=0))
p.append(panel("Drops / s by reason", "timeseries", [(f"sum by (reason) (rate(hubble_drop_total{{{KN}}}[5m]))", "{{reason}}")], 8, 26, 8, 8, unit="ops", min=0))
p.append(panel("Policy verdicts / s", "timeseries", [(f"sum by (direction, action) (rate(hubble_policy_verdicts_total{{{KN}}}[5m]))", "{{direction}} {{action}}")], 16, 26, 8, 8, unit="ops", min=0))
p.append(panel("Flows / s by node", "timeseries", [(f"sum by (node) (rate(hubble_flows_processed_total{{{KN}}}[2m]))", "{{node}}")], 0, 34, 8, 8, unit="ops", min=0))
p.append(panel("TCP: SYN and RST / s", "timeseries", [(f'sum by (flag) (rate(hubble_tcp_flags_total{{{KN},flag=~"SYN|RST"}}[2m]))', "{{flag}}")], 8, 34, 8, 8, unit="ops", min=0))
p.append(panel("Busiest destination ports (flows / s)", "bargauge", [(f'topk(8, sum by (protocol, port) (rate(hubble_port_distribution_total{{{KN}}}[10m])))', "{{protocol}} {{port}}")], 16, 34, 8, 8, unit="ops", min=0))

p.append(row("Cilium's datapath and agents", 42))
p.append(panel("Forwarded bit/s per node", "timeseries", [(f"8 * sum by (node) (rate(cilium_forward_bytes_total{{{KN}}}[2m]))", "{{node}}")], 0, 43, 8, 8, unit="bps", min=0))
p.append(panel("Dropped packets / s per node and reason", "timeseries", [(f"sum by (node, reason) (rate(cilium_drop_count_total{{{KN}}}[5m]))", "{{node}} {{reason}}")], 8, 43, 8, 8, unit="pps", min=0))
p.append(panel("Endpoints ready per node", "timeseries", [(f'cilium_endpoint_state{{{KN},endpoint_state="ready"}}', "{{node}}")], 16, 43, 4, 8, min=0, decimals=0))
p.append(panel("Failing controllers / agent warnings", "timeseries", [(f"cilium_controllers_failing{{{KN}}}", "{{node}} failing"), (f"sum by (node) (rate(cilium_errors_warnings_total{{{KN}}}[5m]))", "{{node}} warn/s")], 20, 43, 4, 8, min=0))
p.append(panel("Node CPU busy", "timeseries", [(f'1 - avg by (node) (rate(node_cpu_seconds_total{{{KN},mode="idle"}}[2m]))', "{{node}}")], 0, 51, 12, 7, unit="percentunit", min=0, max=1))
p.append(panel("Node memory used", "timeseries", [(f"1 - node_memory_MemAvailable_bytes{{{KN}}} / node_memory_MemTotal_bytes{{{KN}}}", "{{node}}")], 12, 51, 12, 7, unit="percentunit", min=0, max=1))
(OUT / "evpn-fabric-k8s.json").write_text(json.dumps(dashboard("evpn-fabric-k8s", "EVPN fabric: Kubernetes", p, ["evpn-fabric", "lab", "kubernetes"], lab="evpn-fabric"), indent=1))

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
FL = 'sampler_address:* -lab:evpn-clab -lab:evpn-pfsense '  # every srv6-core sFlow record (goflow2 JSON, one per sampled packet); evpn-clab and evpn-pfsense have their own
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
# ---------------------------------------------------------------- evpn-clab flows (sFlow: hsflowd on the spines, leaves and wan -> goflow2's
# evpn-clab listener, VXLAN decoded -> Fluent Bit names each sample (flows/evpn-clab.lua) -> VictoriaLogs). One record per sampled
# packet; est_bytes = bytes x sampling rate. Each node samples on ingress, so a packet is counted once per *view*: access (a leaf's
# host ports: what the hosts send), fabric (the spines: VXLAN between VTEPs), edge (the wan). `entry` marks where a packet enters
# the lab (access, or the wan from the internet): counting only those counts every packet once — the top talkers.
_id[0] = 0
EF = 'lab:evpn-clab type:SFLOW_5 '
EN = EF + 'entry:true '
SP = EF + 'view:fabric encap:vxlan '
ED = EF + 'view:edge '
BPS = lambda by: f'| stats by (_time:1m, {by}) sum(est_bytes) as b | math b * 8 / 60 as bps | fields _time, {by}, bps'
BL = {"steps": [{"color": "blue", "value": None}]}
BYTES = [{"matcher": {"id": "byName", "options": "bytes"}, "properties": [{"id": "unit", "value": "bytes"}]}]   # only that column: a port is not KiB
p = []
p.append(panel("Traffic entering the lab (estimated, selected range)", "stat", [(EN + '| stats sum(est_bytes) as bytes', "bytes", {"queryType": "stats"})], 0, 0, 6, 4, ds=VL, unit="bytes", colorMode="value", thresholds=BL))
p.append(panel("Conversations (host pairs)", "stat", [(EN + '| stats count_uniq(pair) as pairs', "pairs", {"queryType": "stats"})], 6, 0, 4, 4, ds=VL, colorMode="value", thresholds=BL))
p.append(panel("VTEP pairs carrying VXLAN", "stat", [(SP + '| stats count_uniq(vtep_src, vtep_dst) as pairs', "pairs", {"queryType": "stats"})], 10, 0, 4, 4, ds=VL, colorMode="value", thresholds=BL))
p.append(panel("Samples per exporter (selected range)", "stat", [(EF + '| stats by (node) count() as samples', "{{node}}", {"queryType": "stats"})], 14, 0, 10, 4, ds=VL, colorMode="value", thresholds=BL))
p.append(row("Who talks to whom (where packets enter the lab: each packet once)", 4))
p.append(panel("Top conversations (estimated bytes)", "table", [(EN + '| stats by (pair, tenant, app_proto, app_port) sum(est_bytes) as bytes | sort by (bytes desc) | limit 20', "", {"queryType": "stats"})], 0, 5, 10, 10, ds=VL, overrides=BYTES, columns=["pair", "tenant", "app_proto", "app_port"]))
p.append(panel("Conversations over time (bit/s, 1-min averages, estimated)", "timeseries", [logs_ts(EN + BPS("pair"), "{{pair}}")], 10, 5, 14, 10, ds=VL, unit="bps", min=0))
p.append(panel("Per tenant (bit/s, 1-min averages)", "timeseries", [logs_ts(EN + BPS("tenant"), "{{tenant}}")], 0, 15, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("Top senders (bit/s)", "timeseries", [logs_ts(EN + BPS("host_src"), "{{host_src}}")], 8, 15, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("Applications (protocol / destination port, estimated bytes)", "table", [(EN + '| stats by (app_proto, app_port) sum(est_bytes) as bytes | sort by (bytes desc) | limit 12', "", {"queryType": "stats"})], 16, 15, 8, 8, ds=VL, overrides=BYTES, columns=["app_proto", "app_port"]))
p.append(row("The fabric: VXLAN between VTEPs (sampled on the spines)", 23))
p.append(panel("VTEP pairs and VNIs (estimated bytes)", "table", [(SP + '| stats by (vtep_src, vtep_dst, vni_name) sum(est_bytes) as bytes | sort by (bytes desc) | limit 20', "", {"queryType": "stats"})], 0, 24, 10, 10, ds=VL, overrides=BYTES, columns=["vtep_src", "vtep_dst", "vni_name"]))
p.append(panel("ECMP: VXLAN through each spine (bit/s)", "timeseries", [logs_ts(SP + BPS("node"), "{{node}}")], 10, 24, 7, 10, ds=VL, unit="bps", min=0))
p.append(panel("Per VNI (bit/s)", "timeseries", [logs_ts(SP + BPS("vni_name"), "{{vni_name}}")], 17, 24, 7, 10, ds=VL, unit="bps", min=0))
p.append(panel("Which spine carries which conversation (flows hash onto one spine each)", "table", [(SP + '| stats by (pair, vtep_src, vtep_dst, node) sum(est_bytes) as bytes | sort by (bytes desc) | limit 20', "", {"queryType": "stats"})], 0, 34, 12, 9, ds=VL, overrides=BYTES, columns=["pair", "vtep_src", "vtep_dst", "node"]))
p.append(panel("Inside the tunnels (inner protocol, samples)", "table", [(SP + '| stats by (inner_proto_name, tenant) count() as samples | sort by (samples desc) | limit 10', "", {"queryType": "stats"})], 12, 34, 6, 9, ds=VL, columns=["inner_proto_name", "tenant"]))
p.append(panel("The fabric's own traffic on the spines (BFD, BGP, OSPF: samples)", "table", [(EF + 'view:fabric -encap:vxlan | stats by (app_proto, app_port, from) count() as samples | sort by (samples desc) | limit 12', "", {"queryType": "stats"})], 18, 34, 6, 9, ds=VL, columns=["app_proto", "app_port", "from"]))
p.append(row("North-south: the wan (borders <-> the internet, through the firewall)", 43))
p.append(panel("Into the wan, by where it came from (bit/s)", "timeseries", [logs_ts(ED + BPS("from"), "{{from}}")], 0, 44, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("Per tenant at the edge (bit/s)", "timeseries", [logs_ts(ED + '-tenant:none ' + BPS("tenant"), "{{tenant}}")], 8, 44, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("North-south conversations (estimated bytes)", "table", [(ED + '-tenant:none | stats by (pair, from, tenant) sum(est_bytes) as bytes | sort by (bytes desc) | limit 15', "", {"queryType": "stats"})], 16, 44, 8, 8, ds=VL, overrides=BYTES, columns=["pair", "from", "tenant"]))
p.append(row("Samples", 52))
p.append(panel("Newest samples (message = conversation; open one for every field)", "logs", [(EF, "")], 0, 53, 24, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))
(OUT / "evpn-clab-flows.json").write_text(json.dumps(dashboard("evpn-clab-flows", "EVPN clab: flows (sFlow)", p, ["evpn-clab", "flows", "sflow"], refresh="1m", lab="evpn-clab"), indent=1))

# ---------------------------------------------------------------- evpn-pfsense flows (sFlow: hsflowd on the spines, leaves and borders -> goflow2's
# evpn-pfsense listener, VXLAN decoded -> Fluent Bit names each sample (flows/evpn-pfsense.lua) -> VictoriaLogs). One record per sampled
# packet; est_bytes = bytes x sampling rate. Each node samples on ingress, so a packet is counted once per *view*: access (a leaf's
# host ports: what the hosts send), fabric (the spines: VXLAN between VTEPs), edge (a border's transit ports: what the firewall pairs send back). `entry` marks where
# a packet enters the lab (access, or the internet coming back in at a border): counting only those counts every packet once — the top talkers.
_id[0] = 0
EF = 'lab:evpn-pfsense type:SFLOW_5 '
EN = EF + 'entry:true '
SP = EF + 'view:fabric encap:vxlan '
ED = EF + 'view:edge '
BPS = lambda by: f'| stats by (_time:1m, {by}) sum(est_bytes) as b | math b * 8 / 60 as bps | fields _time, {by}, bps'
BL = {"steps": [{"color": "blue", "value": None}]}
BYTES = [{"matcher": {"id": "byName", "options": "bytes"}, "properties": [{"id": "unit", "value": "bytes"}]}]   # only that column: a port is not KiB
p = []
p.append(panel("Traffic entering the lab (estimated, selected range)", "stat", [(EN + '| stats sum(est_bytes) as bytes', "bytes", {"queryType": "stats"})], 0, 0, 6, 4, ds=VL, unit="bytes", colorMode="value", thresholds=BL))
p.append(panel("Conversations (host pairs)", "stat", [(EN + '| stats count_uniq(pair) as pairs', "pairs", {"queryType": "stats"})], 6, 0, 4, 4, ds=VL, colorMode="value", thresholds=BL))
p.append(panel("VTEP pairs carrying VXLAN", "stat", [(SP + '| stats count_uniq(vtep_src, vtep_dst) as pairs', "pairs", {"queryType": "stats"})], 10, 0, 4, 4, ds=VL, colorMode="value", thresholds=BL))
p.append(panel("Samples per exporter (selected range)", "stat", [(EF + '| stats by (node) count() as samples', "{{node}}", {"queryType": "stats"})], 14, 0, 10, 4, ds=VL, colorMode="value", thresholds=BL))
p.append(row("Who talks to whom (where packets enter the lab: each packet once)", 4))
p.append(panel("Top conversations (estimated bytes)", "table", [(EN + '| stats by (pair, tenant, app_proto, app_port) sum(est_bytes) as bytes | sort by (bytes desc) | limit 20', "", {"queryType": "stats"})], 0, 5, 10, 10, ds=VL, overrides=BYTES, columns=["pair", "tenant", "app_proto", "app_port"]))
p.append(panel("Conversations over time (bit/s, 1-min averages, estimated)", "timeseries", [logs_ts(EN + BPS("pair"), "{{pair}}")], 10, 5, 14, 10, ds=VL, unit="bps", min=0))
p.append(panel("Per tenant (bit/s, 1-min averages)", "timeseries", [logs_ts(EN + BPS("tenant"), "{{tenant}}")], 0, 15, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("Top senders (bit/s)", "timeseries", [logs_ts(EN + BPS("host_src"), "{{host_src}}")], 8, 15, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("Applications (protocol / destination port, estimated bytes)", "table", [(EN + '| stats by (app_proto, app_port) sum(est_bytes) as bytes | sort by (bytes desc) | limit 12', "", {"queryType": "stats"})], 16, 15, 8, 8, ds=VL, overrides=BYTES, columns=["app_proto", "app_port"]))
p.append(row("The fabric: VXLAN between VTEPs (sampled on the spines)", 23))
p.append(panel("VTEP pairs and VNIs (estimated bytes)", "table", [(SP + '| stats by (vtep_src, vtep_dst, vni_name) sum(est_bytes) as bytes | sort by (bytes desc) | limit 20', "", {"queryType": "stats"})], 0, 24, 10, 10, ds=VL, overrides=BYTES, columns=["vtep_src", "vtep_dst", "vni_name"]))
p.append(panel("ECMP: VXLAN through each spine (bit/s)", "timeseries", [logs_ts(SP + BPS("node"), "{{node}}")], 10, 24, 7, 10, ds=VL, unit="bps", min=0))
p.append(panel("Per VNI (bit/s)", "timeseries", [logs_ts(SP + BPS("vni_name"), "{{vni_name}}")], 17, 24, 7, 10, ds=VL, unit="bps", min=0))
p.append(panel("Which spine carries which conversation (flows hash onto one spine each)", "table", [(SP + '| stats by (pair, vtep_src, vtep_dst, node) sum(est_bytes) as bytes | sort by (bytes desc) | limit 20', "", {"queryType": "stats"})], 0, 34, 12, 9, ds=VL, overrides=BYTES, columns=["pair", "vtep_src", "vtep_dst", "node"]))
p.append(panel("Inside the tunnels (inner protocol, samples)", "table", [(SP + '| stats by (inner_proto_name, tenant) count() as samples | sort by (samples desc) | limit 10', "", {"queryType": "stats"})], 12, 34, 6, 9, ds=VL, columns=["inner_proto_name", "tenant"]))
p.append(panel("The fabric's own traffic on the spines (BFD, BGP, OSPF: samples)", "table", [(EF + 'view:fabric -encap:vxlan | stats by (app_proto, app_port, from) count() as samples | sort by (samples desc) | limit 12', "", {"queryType": "stats"})], 18, 34, 6, 9, ds=VL, columns=["app_proto", "app_port", "from"]))
p.append(row("Back from the firewall pairs (sampled on the borders' transit ports: the other tenant, the internet)", 43))
p.append(panel("From the firewalls into the fabric, by transit port (bit/s)", "timeseries", [logs_ts(ED + BPS("from"), "{{from}}")], 0, 44, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("Per tenant from the firewalls (bit/s)", "timeseries", [logs_ts(ED + '-tenant:none ' + BPS("tenant"), "{{tenant}}")], 8, 44, 8, 8, ds=VL, unit="bps", min=0))
p.append(panel("Through the firewalls: conversations (estimated bytes)", "table", [(ED + '-tenant:none | stats by (pair, from, tenant) sum(est_bytes) as bytes | sort by (bytes desc) | limit 15', "", {"queryType": "stats"})], 16, 44, 8, 8, ds=VL, overrides=BYTES, columns=["pair", "from", "tenant"]))
p.append(row("Samples", 52))
p.append(panel("Newest samples (message = conversation; open one for every field)", "logs", [(EF, "")], 0, 53, 24, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))
(OUT / "evpn-pfsense-flows.json").write_text(json.dumps(dashboard("evpn-pfsense-flows", "EVPN pfSense: flows (sFlow)", p, ["evpn-pfsense", "flows", "sflow"], refresh="1m", lab="evpn-pfsense"), indent=1))
# ---------------------------------------------------------------- srl-evpn: Nokia SR Linux 5-stage Clos over gNMI (gnmic-srl-evpn ->
# Prometheus -> VictoriaMetrics): sessions, BFD, interfaces, Ethernet Segments, traffic, routes, MACs, CPU and memory per node
_id[0] = 0
SL = 'lab="srl-evpn"'
EN = f'and on (node, interface_name) srl_iface_in_octets{{{SL}}}'          # enabled ports only (they report counters)
FP = f'interface_name=~"ethernet-.*"'
OKS = {"steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}
p = []
p.append(panel("BGP sessions established", "stat", [(f'sum(srl_bgp_session_state{{{SL}}})', "up"), (f'count(srl_bgp_session_state{{{SL}}})', "configured")], 0, 0, 6, 4, colorMode="value", thresholds={"steps": [{"color": "blue", "value": None}]}))
p.append(panel("BGP sessions down", "stat", [(f'count(srl_bgp_session_state{{{SL}}} == 0) or vector(0)', "down")], 6, 0, 3, 4, thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("BFD sessions down", "stat", [(f'count(srl_bfd_session_state{{{SL}}} == 0) or vector(0)', "down")], 9, 0, 3, 4, thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Enabled ports down", "stat", [(f'count((srl_iface_oper_state{{{SL},{FP}}} == 0) {EN}) or vector(0)', "down")], 12, 0, 3, 4, thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Ethernet Segments up", "stat", [(f'srl_es_oper_state{{{SL}}}', "{{node}} {{ethernet_segment_name}}")], 15, 0, 9, 4, colorMode="background", textMode="name", mappings=UPDOWN_MAP, thresholds=OKS))
p.append(row("Sessions per node", 4))
p.append(panel("BGP sessions established per node (underlay + overlay + edge)", "bargauge", [(f'sum by (node) (srl_bgp_session_state{{{SL}}})', "{{node}}")], 0, 5, 12, 10, min=0))
p.append(panel("Sessions not established", "table", [(f'srl_bgp_session_state{{{SL}}} == 0', "", {"instant": True, "format": "table"})], 12, 5, 12, 10,
               transformations=[{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}},
                                {"id": "organize", "options": {"excludeByName": {"Time": True, "Value": True, "__name__": True, "lab": True, "job": True, "instance": True, "site": True}}}]))
p.append(row("Traffic (bit/s, every enabled port)", 15))
p.append(panel("Per node, in + out", "timeseries", [(f'sum by (node) (rate(srl_iface_in_octets{{{SL},{FP}}}[2m]) + rate(srl_iface_out_octets{{{SL},{FP}}}[2m])) * 8', "{{node}}")], 0, 16, 12, 9, unit="bps", min=0))
p.append(panel("Per pod (leaf ports, in)", "timeseries", [(f'sum by (pod) (rate(srl_iface_in_octets{{{SL},role="leaf",{FP}}}[2m])) * 8', "pod {{pod}}")], 12, 16, 6, 9, unit="bps", min=0))
p.append(panel("Through the super-spines (between the pods)", "timeseries", [(f'sum by (node) (rate(srl_iface_in_octets{{{SL},role="superspine",{FP}}}[2m])) * 8', "{{node}}")], 18, 16, 6, 9, unit="bps", min=0))
p.append(panel("Busiest ports", "table", [(f'topk(15, (rate(srl_iface_in_octets{{{SL},{FP}}}[5m]) + rate(srl_iface_out_octets{{{SL},{FP}}}[5m])) * 8)', "", {"instant": True, "format": "table"})], 0, 25, 12, 9, unit="bps",
               transformations=[{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}},
                                {"id": "organize", "options": {"excludeByName": {"Time": True, "lab": True, "job": True, "instance": True, "pod": True, "site": True}}}]))
p.append(panel("Errors and discards / s", "timeseries", [(f'sum by (node) (rate(srl_iface_in_error_packets{{{SL}}}[5m]) + rate(srl_iface_in_discarded_packets{{{SL}}}[5m]) + rate(srl_iface_out_discarded_packets{{{SL}}}[5m]))', "{{node}}")], 12, 25, 12, 9, min=0))
p.append(row("Routes and MACs", 34))
p.append(panel("Active routes per tenant (each leaf / border)", "timeseries", [(f'srl_routes_active_routes{{{SL},network_instance_name=~"red|blue"}}', "{{node}} {{network_instance_name}}")], 0, 35, 12, 8, min=0))
p.append(panel("MAC entries per VLAN (each leaf)", "timeseries", [(f'srl_macs_active_entries{{{SL}}}', "{{node}} {{network_instance_name}}")], 12, 35, 12, 8, min=0))
p.append(row("The nodes: CPU and memory", 43))
p.append(panel("CPU % (1-minute average)", "timeseries", [(f'srl_system_average_1{{{SL}}}', "{{node}}")], 0, 44, 12, 8, unit="percent", min=0))
p.append(panel("Memory used %", "timeseries", [(f'srl_system_utilization{{{SL}}}', "{{node}}")], 12, 44, 12, 8, unit="percent", min=0, max=100))
p.append(row("Syslog (VictoriaLogs, lab=srl-evpn): SR Linux's events", 52))
SE = 'lab:srl-evpn "|EV|" '
p.append(panel("Events / 5 min per node", "timeseries", [logs_ts(SE + '| stats by (_time:5m, hostname) count() as events', "{{hostname}}")], 0, 53, 12, 8, ds=VL, min=0))
p.append(panel("Events by name (selected range)", "table", [(SE + '| extract "|EV|<event>|" | stats by (event) count() as events | sort by (events desc) | limit 15', "", {"queryType": "stats"})], 12, 53, 12, 8, ds=VL, columns=["event"]))
p.append(panel("Newest events (BGP, BFD, ports, LAGs, segments, commits)", "logs", [(SE, "")], 0, 61, 24, 10, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))
(OUT / "srl-evpn-overview.json").write_text(json.dumps(dashboard("srl-evpn-overview", "SR Linux EVPN Clab: overview (gNMI)", p, ["srl-evpn", "lab", "gnmi"], lab="srl-evpn"), indent=1))
# ---------------------------------------------------------------- p4-lab: our own switch (BMv2 running fabric.p4, programmed over P4Runtime by the lab's
# controller): sessions, links from the controller's probes, ECMP groups, and INT — every tenant packet carries a record from each switch it crosses; the
# portal (:8103) turns the reports into per-switch latency and per-pair path latency, and ships one record per flow to VictoriaLogs (lab=p4-lab type=INT)
_id[0] = 0
L = 'lab="p4-lab"'
PI = 'lab:p4-lab type:INT '
p = []
p.append(row("The lab", 0))
p.append(panel("P4Runtime sessions", "stat", [(f"sum(lab_p4rt_session_up{{{L}}})", "up"), (f"count(lab_p4rt_session_up{{{L}}})", "switches")], 0, 1, 4, 4, colorMode="value", thresholds=G1))
p.append(panel("Fabric links up", "stat", [(f"sum(lab_p4_link_up{{{L}}})", "up"), (f"count(lab_p4_link_up{{{L}}})", "links")], 4, 1, 4, 4, colorMode="value", thresholds=G1))
p.append(panel("ECMP groups with no path", "stat", [(f"count(lab_p4_ecmp_group_members{{{L}}} == 0) or vector(0)", "no path")], 8, 1, 4, 4, thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))
p.append(panel("Last tests", "stat", [(f"lab_tests_last_passed{{{L}}}", "passed"), (f"lab_tests_last_failed{{{L}}}", "failed")], 12, 1, 4, 4, colorMode="value",
               overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "thresholds", "value": {"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}]},
                          {"matcher": {"id": "byName", "options": "passed"}, "properties": [{"id": "thresholds", "value": G1}]}]))
p.append(panel("INT flows (last 30 s)", "stat", [(f"sum by (tenant) (lab_p4_int_flows{{{L}}})", "{{tenant}}")], 16, 1, 4, 4, colorMode="value", thresholds=G1))
p.append(panel("Firing alerts", "stat", [(f'count(ALERTS{{{L},alertstate="firing",severity!="info"}}) or vector(0)', "firing")], 20, 1, 4, 4, ds=PROM, colorMode="background", thresholds={"steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}))

p.append(row("INT: inside the switches (each tenant packet's hop records, reported by the egress leaf)", 5))
p.append(panel("Hop latency, median per switch (µs a packet spends inside it)", "timeseries", [(f'lab_p4_int_hop_latency_us{{{L},stat="p50"}}', "{{switch}}")], 0, 6, 12, 9, unit="µs", min=0))
p.append(panel("Hop latency, worst in the last minute", "timeseries", [(f'lab_p4_int_hop_latency_us{{{L},stat="max"}}', "{{switch}}")], 12, 6, 12, 9, unit="µs", min=0))
p.append(panel("Path latency per host pair (sum of hops, slowest flow)", "timeseries", [(f'lab_p4_int_path_latency_us{{{L}}}', "{{src}} -> {{dst}} ({{tenant}})")], 0, 15, 10, 9, unit="µs", min=0))
p.append(panel("Flows by the spine their tunnel crossed (ECMP)", "timeseries", [(f'lab_p4_int_flows_via{{{L}}}', "{{spine}}")], 10, 15, 7, 9, min=0, decimals=0, custom={"stacking": {"mode": "normal"}, "fillOpacity": 40}))
p.append(panel("Reports / s (periodic, latency events)", "timeseries", [(f'rate(lab_p4_int_reports_total{{{L}}}[2m])', "{{kind}}")], 17, 15, 7, 5, min=0))
p.append(panel("Deepest queue (packets)", "timeseries", [(f'lab_p4_int_queue_depth_max{{{L}}}', "{{switch}}")], 17, 20, 7, 4, min=0, decimals=0))

p.append(row("INT flow records (VictoriaLogs lab=p4-lab type=INT: one per flow every 15 s)", 24))
p.append(panel("Slowest path latency per path (1 min)", "timeseries", [logs_ts(PI + '| stats by (_time:1m, path) max(latency_us) as us', "{{path}}")], 0, 25, 12, 8, ds=VL, unit="µs", min=0))
p.append(panel("Flows per path (selected range)", "table", [(PI + '| stats by (tenant, path) count_uniq(_msg) as flows | sort by (flows desc) | limit 20', "", {"queryType": "stats"})], 12, 25, 12, 8, ds=VL, columns=["tenant", "path"]))
p.append(panel("Newest flow records", "logs", [(PI, "")], 0, 33, 24, 9, ds=VL, showTime=True, wrapLogMessage=False, sortOrder="Descending"))

p.append(row("Links (the controller's probes, 5 a second each way) and ECMP", 42))
p.append(panel("Fabric links", "state-timeline", [(f"lab_p4_link_up{{{L}}}", "{{link}}")], 0, 43, 12, 7, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Probe latency (controller -> switch -> link -> switch -> controller)", "timeseries", [(f"lab_p4_link_probe_latency_ms{{{L}}}", "{{from}} -> {{to}}")], 12, 43, 12, 7, unit="ms", min=0, legend=False))
p.append(panel("Live next hops per ECMP group (group = destination switch's id)", "timeseries", [(f"lab_p4_ecmp_group_members{{{L}}}", "{{switch}} -> {{group}}")], 0, 50, 12, 8, min=0, decimals=0, legend=False))
p.append(panel("Link state changes", "timeseries", [(f"increase(lab_p4_link_changes_total{{{L}}}[5m])", "{{link}}")], 12, 50, 12, 8, min=0, decimals=0))

p.append(row("Traffic, drops and the control plane", 58))
p.append(panel("Bit rate per switch (all ports, in)", "timeseries", [(f'sum by (switch) (rate(lab_p4_port_bytes_total{{{L},dir="in"}}[2m])) * 8', "{{switch}}")], 0, 59, 8, 8, unit="bps", min=0))
p.append(panel("Drops / s by reason", "timeseries", [(f'sum by (switch, reason) (rate(lab_p4_drops_total{{{L}}}[2m]))', "{{switch}} {{reason}}")], 8, 59, 8, 8, min=0))
p.append(panel("Packets to the controller / s (packet-in by reason)", "timeseries", [(f'sum by (reason) (rate(lab_p4_punts_total{{{L}}}[2m]))', "{{reason}}")], 16, 59, 8, 8, min=0))

p.append(row("Lab and runs", 67))
p.append(panel("Node containers running", "state-timeline", [(f"lab_vm_running{{{L}}}", "{{node}} ({{role}})")], 0, 68, 12, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
p.append(panel("Portal runs: last outcome per mode", "state-timeline", [(f"lab_run_last_success{{{L}}}", "{{mode}}")], 12, 68, 12, 8, mappings=UPDOWN_MAP, thresholds=UPDOWN))
host_row(p, 76)
(OUT / "p4-lab-overview.json").write_text(json.dumps(dashboard("p4-lab-overview", "p4-lab: overview (P4 / INT)", p, ["p4-lab", "lab", "int"], lab="p4-lab"), indent=1))
print("wrote", ", ".join(f.name for f in sorted(OUT.glob("*.json"))))
