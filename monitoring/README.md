# Lab monitoring: Prometheus + VictoriaMetrics + Grafana

Runs on the NMS VM (10.0.0.10) next to Nautobot and Gitea, and serves every lab on the host.

| Component | Port | Role |
|---|---|---|
| Prometheus v3 | 9090 | scrapes every target every 30 s, evaluates `prometheus/alerts.yml` (per lab: internet breakout, tunnels, headend reachability / capacity / CPU, and for the IPsec lab's certificate mode `CertificateExpiringSoon` < 30 days / `CertificateExpired` from the portal's `lab_cert_not_after_seconds`, and configuration drift: `ConfigDrift` when Nautobot Golden Config reports a router non-compliant for 10 min, `ConfigComplianceStale` when the portal's scheduled Golden Config run has not happened for twice its interval while the lab is up — from `lab_config_compliance_ok` / `lab_config_compliance_last_run_timestamp_seconds`), keeps 2 days locally, remote-writes everything to VictoriaMetrics |
| VictoriaMetrics | 8428 | the store of record: 180-day retention, Prometheus-compatible query API, `/vmui` |
| VictoriaLogs | 9428 (HTTP), 5514/udp (syslog), 5515/udp (evpn-clab's syslog, stamped `lab=evpn-clab`), 5516/udp (srl-evpn's SR Linux syslog, `lab=srl-evpn`) | the VyOS nodes' syslog (`system syslog remote`), streams by hostname / app_name, 90-day retention, `/select/vmui`; Grafana datasource plugin `victoriametrics-logs-datasource` |
| vmalert-logs | 8880 | log-derived alerts: LogsQL rules (`vmalert/logs-alerts.yml`) against VictoriaLogs every minute — BGP neighbour down, IS-IS adjacency change, BFD session change, FRR daemon restart, zebra install failures, commits, SSH failures, silent node; for the IPsec lab's VyOS firewalls (remote syslog, `lab=cat8000v-ipsec` on the rule) `FirewallDropBurst` (>20 drops from one source in 5 min) and `FirewallDroppingPeerTraffic` (IKE / ESP hitting the drop rule); alert state is remote-written to VictoriaMetrics as `ALERTS{evaluator="vmalert-logs"}` (no Alertmanager yet: `--notifier.blackhole`) |
| goflow2 + Fluent Bit | 6343/udp (sFlow), 8083 (goflow2 metrics) | sFlow collector: the PEs' and Ps' core-facing ports sample 1 in 16 packets (`system sflow`, hsflowd); goflow2 decodes each sample (outer IPv6, SRH addresses and segments-left included) into JSON, Fluent Bit ships it into VictoriaLogs (stream per exporter) — dashboard **SRv6 flows (sFlow)**: which source PE sends to which SID, which router forwards it, steered (uSID carrier) packets, protocols |
| goflow2-evpn-clab | 6344/udp (sFlow), 8084 (goflow2 metrics) | evpn-clab's own sFlow listener: its spines, leaves and wan sample 1 in 128 on ingress (tc `sample` → psample → hsflowd); `flows/evpn-clab-mapping.yaml` reads VXLAN's VNI and inner packet at fixed offsets (goflow2 has no VXLAN parser); Fluent Bit's Lua filter `flows/evpn-clab.lua` names each sample from the lab's fabric.yml (`flows/evpn-clab-names.lua`, written by `flows/gen_evpn_clab.py`), drops egress samples, stamps `lab=evpn-clab` (stream `lab`, `node`) — dashboard **EVPN clab: flows (sFlow)** |
| gnmic-srl-evpn | 9804 (Prometheus exporter, inside the compose network) | srl-evpn's 16 SR Linux nodes over **gNMI** (port 57400 on 172.20.110.0/24, reached through the host): BGP sessions, BFD, interface state and counters, Ethernet Segments, active routes and MAC entries, CPU and memory, sampled every 15 s; states become numbers (up / established = 1), names are shortened (`srl_bgp_session_state{node, network_instance_name, neighbor_peer_address}`), every series carries lab / node / role / pod. Its targets come from the lab's fabric.yml: `gnmic/gen_srl_evpn.py` writes `gnmic/srl-evpn.yml`. Its syslog (SR Linux's structured `|EV|<eventName>|` events) comes in on VictoriaLogs' UDP 5516; log rules `srl-evpn-events` in vmalert-logs; an events row on the dashboard. Prometheus job `srl-evpn`, alert group `srl-evpn` (BGP / BFD / enabled port / Ethernet Segment down, CPU / memory high), dashboard **SR Linux EVPN Clab: overview (gNMI)**. When the lab is down its series expire (60 s) and nothing fires |
| gnmic-srv6-xr | 9805 (Prometheus exporter, inside the compose network) | srv6-xr's IOS-XRv9000 PE (xr-pe1) over **gNMI**: XR's gRPC server (port 57400, plain text) sits on the lab's isolated OOB network, so gnmic dials the host's relay 10.0.0.1:57409 (the lab's `srv6-xr-gnmi-relay` user unit, socat). Sampled every 10 s: OpenConfig interface state and counters, and Cisco native IS-IS adjacencies, BGP VPNv4/v6 sessions and prefix counts, BFD sessions, SR policies, CPU and memory per card. A Starlark processor puts the lab's names on the series (XR names a neighbour by system id or link-local address: `peer="p1"`, `policy="VIA-P3"`, `tenant="tenant-a"`), turns states into numbers (up = 1) and leaves out unused interfaces: `xr_iface_in_octets{interface, peer, tenant}`, `xr_isis_adjacency_up`, `xr_bgp_established{family}`, `xr_bfd_session_up`, `xr_te_oper_up{policy, color}`, `xr_system_cpu_1m{location}`. `gnmic/gen_srv6_xr.py` writes `gnmic/srv6-xr.yml` from the lab's inventory. Prometheus job `srv6-xr-gnmi`; alerts in group `srv6-xr` (gNMI silent while xr-pe1 runs, port down, BFD down, port errors, CPU); a row on **srv6-xr: overview**. XR's YANG layer answers "Server not ready" for a minute or two after gRPC is enabled or the router boots; gnmic retries |
| Grafana | 3001 | dashboards from `grafana/dashboards/*.json` (generated by `gen_dashboards.py`), datasources provisioned: VictoriaMetrics (default) + Prometheus (alert state); anonymous viewer, admin / admin |

**Pushed telemetry** (no scraping): every VyOS node runs its built-in Telegraf (`service monitoring telegraf`) with the
InfluxDB v2 output pointed at VictoriaMetrics' InfluxDB write API — host metrics, `vyos_services_status` (the FRR daemons),
kernel `nstat_*` counters, ethtool, conntrack, systemd units — every series tagged `lab`, `role`, `dc` (`global-tag`).
Telegraf's Loki output was tried for syslog and rejected: it ships *every metric* as a log line and the CLI cannot filter
it, so syslog goes the native way (`system syslog remote <nms> port 5514`) into VictoriaLogs' syslog receiver. Note
VyOS insists on an InfluxDB-shaped token (86 chars + `==`); VictoriaMetrics ignores it. Dashboard **VyOS telemetry**.

Targets are not configured by hand: each lab portal exposes **`/api/sd`** (Prometheus HTTP service discovery) listing its
node-exporters (:9100), frr-exporters (:9342 on VyOS) and itself, labelled `lab`, `node`, `role`, `dc`, `tenant`, `job`;
and **`/metrics`** with what the exporters cannot know (tenant health, adjacency counts, VM state, run and test outcomes —
the shared part comes from `labportal.metrics`).

```bash
python3 gen_dashboards.py    # after editing a panel
./deploy.sh                  # rsync to lab@10.0.0.10:/opt/monitoring, docker compose up -d, reload Prometheus
```

LAN access from the lab host goes through relays, one user unit per port bound to the LAN address
(`lab-relay-*.service.example`). Grafana, Prometheus, VictoriaMetrics and VictoriaLogs are behind the Lab Hub's sign-in
(`lab-proxy`, labportal/proxy.py: sign in once at the hub, :8088; the host's own requests pass); Nautobot and Gitea,
which have their own logins, are plain socat relays — http://192.168.50.231:3001 (theme: Grafana follows the OS / browser dark-mode setting; the ☾ Dark / ☀ Light links on every dashboard open it in a new tab with the theme forced — the `theme=` URL parameter only takes effect on a full page load) (dashboards: SRv6 core overview, **EVPN fabric overview** — node health, fabric BGP / BFD, remote VTEPs per L2VNI, the DF per Ethernet Segment, server LACP legs, default routes via both borders, fw-ext's edge sessions, the fabric's FRR syslog — from the evpn-fabric portal's `lab_bgp_*` / `lab_evpn_*` / `lab_es_df` / `lab_default_route_paths` gauges, **C8000v IPsec overview** — tunnels, headend capacity/CPU, from the IPsec portal's `lab_tunnel_*` / `lab_headend_*` gauges, plus each spoke's IKE authentication (PSK or certificate) and certificate expiry, plus a Firewalls row from the VyOS firewalls' syslog in VictoriaLogs: drops and new flows per firewall, top dropped flows, the kernel log), :9091 (Prometheus — 9090 on the host is Cockpit), :8428, :9428; Nautobot :8080 and Gitea :3000. The hub links to them.

The **Ubuntu lab host itself** is scraped too (job `lab-host`, labels `lab="lab-host", node="ubuntu", role="hypervisor"`):
`node_exporter` 1.12 runs as a `systemd --user` unit on the host (`lab-host-node-exporter.service.example` — install it under
`~/.config/systemd/user/`, binary in `~/.local/bin`) listening on the OOB bridge address 10.0.0.1:9100, which the NMS reaches.
Every overview dashboard ends with a "Ubuntu lab host (KVM): CPU and memory" row — busy %, memory, load vs cores, swap, per
mode and the busiest cores — so a lab's behaviour can be read against what the hypervisor has left.

**evpn-fabric** (job `evpn-fabric`, SD `http://10.106.0.1:8095/api/sd`): the NMS reaches the fabric on its own NIC on
`evpn-oob` (eth6, 10.106.0.10 — `~/cat9000v/lab.sh`, `/etc/netplan/63-evpn-oob.yaml`); the SD labels carry `rack` where the
other labs have `dc`. Its alert group (`evpn-fabric`) covers fabric sessions, VTEPs missing on a VNI, a segment without
exactly one DF, a default route through fewer than both borders, fw-ext's edge sessions, a dual-homed server down to one
LACP leg and node health. Its log rules (`evpn-fabric-events`) carry `lab: evpn-fabric`; the generic `routing-events` and
`operations-events` exclude its hostnames (they would be labelled `lab=srv6-core` by the evaluator), and the IPsec lab's
firewall rules match `fw-(east|central|west)` exactly rather than `fw-*` (evpn-fabric has an `fw-ext`).

**p4-lab** (job `p4-lab`, the portal `10.0.0.1:8103`): BMv2 switches running the lab's own P4 program, programmed by its
own controller — no exporters; the portal reads the controller. Sessions, links (the controller's probes), ECMP groups,
ports, drops, punts, and **INT** (in-band network telemetry): every tenant packet carries a record from each switch it
crosses, the egress leaf reports each flow, and the portal exports per-switch hop latency (`lab_p4_int_hop_latency_us`
p50 / avg / max over the last minute), queue depth, flows per spine and path latency per host pair — and ships one
record per flow every 15 s into **VictoriaLogs** over HTTP (`/insert/jsonline`; stream `lab=p4-lab, type=INT`; fields
`path`, `spine`, `latency_us`, `hop_<switch>_us`, `tenant`, `src_host` / `dst_host`, `proto`, ports). Alert group
`p4-lab` (controller, sessions, pipeline, links, no path, write errors, a switch holding packets > 5 ms for 5 min, and
an info alert while a heavy hitter is policed). Since p4-lab 0.5 its stateful data plane too: firewall and policer drops
(`lab_p4_drops_total{reason="firewall"|"policed"}`), `lab_p4_heavy_hitters{tenant}`, `lab_p4_policed_flows{switch}`,
and one VictoriaLogs record per heavy hitter per 15 s (`lab:p4-lab type:HH`). Dashboard **p4-lab: overview (P4 / INT)**.

**evpn-prefect** (job `evpn-prefect`, SD `http://10.0.0.1:8105/api/sd`): VyOS on containerlab, built and run by Prefect
flows. Management network 172.20.118.0/24, reached through the host. Every switch's node- and frr-exporter (`job` node
/ frr, labels `node`, `role`) and the portal: Prefect (`lab_prefect_up`, workers, finished runs, runs waiting for
approval, each deployment's last outcome), the switches against the applied intent (`lab_switch_drift_lines`), the
last validation (`lab_fabric_checks`), each switch's VyOS version (`lab_switch_version_info`) and maintenance, the event
bridge (`lab_eventd_*`). The portal ships into **VictoriaLogs** every finished flow run (`lab:evpn-prefect type:RUN`),
the flows' logs (`type:FLOWLOG`) and the switches' events (`type:EVENT`). Alert group `evpn-prefect`. Its alerts with
`incident="true"` (failing checks, drift, an exporter down) go through **Alertmanager** to the portal, which turns them
into Prefect events that start the lab's diagnosis. Dashboard **evpn-prefect: overview (Prefect / EVPN)**.

**evpn-nodered** (job `evpn-nodered`, SD `http://10.0.0.1:8107/api/sd`): the same fabric built and run by Node-RED flows.
Management network 172.20.120.0/24, reached through the host. Every switch's node- and frr-exporter and the portal:
Node-RED (`lab_nodered_up`), each workflow's last outcome (`lab_workflow_last_ok`, state as a label), runs in the last
24 h (`lab_runs_24h`), runs waiting for approval, the switches against the applied intent at the last drift check
(`lab_switch_drift_lines`), the last validation (`lab_fabric_checks`), maintenance, VyOS versions (`lab_switch_version`),
Node-RED's syslog listener (`lab_syslog_lines_total`, `lab_switch_events_total`). The portal ships into **VictoriaLogs**
every finished run (`lab:evpn-nodered type:RUN`), the runs' log lines (`type:RUNLOG`) and the switches' events
(`type:EVENT`). Alert group `evpn-nodered`; its `incident="true"` alerts go through **Alertmanager** straight to Node-RED
(`/hooks/alertmanager`, basic auth: `/opt/monitoring-secrets/alertmanager/evpn-nodered.password`, written by the lab's
`lab.sh nms-secret`), which starts fabric-diagnose. Dashboard **evpn-nodered: overview (Node-RED / EVPN)**.

**homa-lab** (job `homa-lab`, SD `http://10.0.0.1:8108/api/sd`, every 30 s): the Homa transport kernel module on four KVM
hosts and sw1 (a Linux bridge with 8 strict priority queues per port). Everything comes from the portal, which reads each
host's `/proc/net/homa_metrics` and sw1's queue counters over SSH (a scrape takes ~1.5 s): `homa_counter_total` (a
selection of Homa's counters, `counter` label), `homa_module_loaded`, `homa_resend_ticks`, `homa_link_mbps`,
`sw1_port_bytes_total` / `sw1_port_drops_total` (per port, `host` = the host it faces), `sw1_queue_*_total` (per port and
band), `sw1_qos_mode`, and `homa_lab_run_active`. Tests and measurements load the network and change sw1 and the timers
on purpose, so the timer, link-rate and timeout alerts wait for no run to be going. Alert group `homa-lab`; dashboard
**homa-lab: overview (Homa kernel module)**.

**Disk (found 2026-10-27):** nothing caps the containers' json logs or the journal on the NMS, and its disk is 19 GB. With
srl-evpn off, gnmic-srl-evpn logs a subscribe error every 10 s (about 1.3 GB a day); on 2026-10-23 the disk filled and
Prometheus stopped storing anything. To do: `logging: {driver: json-file, options: {max-size: 50m, max-file: "3"}}` on
every service (a deploy then recreates the containers, dropping their current logs), and `SystemMaxUse=500M` for
journald. Check with `df -h /` on the NMS.

**mongodb-lab** (job `mongodb-lab`, SD `http://10.0.0.1:8115/api/sd`, every 15 s): a MongoDB 9.0 replica set (rs0) and a sharded
cluster (shards sh1, sh2, config servers cfg, routers q1, q2) across three emulated data centres (containerlab, tc netem). The portal
samples every mongod and mongos once a second: `mongodb_node_{up,ops_per_second,connections}` (`node`, `role`, `dc`, `op`),
`mongodb_rs_{has_primary,primary_preferred}` (`rs`), `mongodb_rs_member_{lag_seconds,chained}`, `mongodb_wan_{rtt_ms,loss_pct}` (`link`),
`mongodb_wan_dc_cut` (`dc`), `mongodb_balancer_on`, `mongodb_shard_documents` (`ns`, `shard`), `mongodb_lab_run_active`, and while a
load run goes `mongodb_load_{ops,errors}_per_second` and `mongodb_load_latency_ms` (`q`). Alert group `mongodb-lab` (node down, no
primary, primary not the preferred member, lag, a chained secondary, a DC left cut off, failed tests; the outage alerts wait for no run
to be going); dashboard **mongodb-lab: overview**.

**tcp-cc-lab** (job `tcp-cc-lab`, SD `http://10.0.0.1:8112/api/sd`, every 15 s): TCP congestion control (CUBIC, BBR, Reno
hosts in two sites) across a Linux WAN emulator between two VyOS routers. The portal reads wanem's netem/tbf counters and
each host's algorithm, qdisc and iperf3 server over SSH (hosts cached 20 s): `tcpcc_wan_{rtt_ms,rate_mbit,loss_pct,buffer_bdp,queue_bytes}`,
`tcpcc_bottleneck_{bytes,drops}_total` and `tcpcc_bottleneck_backlog_bytes` (`direction` a2b / b2a), `tcpcc_wanem_up`,
`tcpcc_host_{up,cc_ok,fq,iperf3_up,mtu}`, `tcpcc_link_mtu` (r1 / r2 / wanem data links, always 9000), while a portal flow run goes `tcpcc_flow_{mbps,rtt_ms,cwnd_bytes}` (`cc`), the last
compete run's `tcpcc_last_compete_{mbps,share,jain}`, and `tcpcc_lab_run_active`. Alert group `tcp-cc-lab` (wanem
unreachable, host down, host on the wrong algorithm, no fq / iperf3, infrastructure MTU not 9000, hosts on mixed MTUs, failed tests); dashboard **tcp-cc-lab: overview**.

**openstack-lab** (job `openstack-lab`, SD `http://10.0.0.1:8110/api/sd`, every 30 s): OpenStack 2026.1 (Kolla-Ansible)
in three nested-virtualisation VMs. The portal reads the cloud every 20 s with the openstack CLI on osl-ctl and serves it
from memory: `openstack_service_up` (Nova and Cinder services, Neutron/OVN agents; `service`, `host`),
`openstack_containers` (Kolla containers per node, `state` up / bad), `openstack_nested_kvm`, `openstack_instances`
(`host`, `status`), `openstack_compute_mem_available_mib` / `_total_mib`, `openstack_compute_guests`,
`openstack_problems`, and `openstack_lab_run_active`. The evacuation suite powers osl-cmp2 off on purpose, so the
service and container alerts wait for no run to be going. Alert group `openstack-lab`; dashboard **openstack-lab:
overview (OpenStack on nested KVM)**.

**Alertmanager** (`:9093`, `alertmanager/alertmanager.yml`): Prometheus sends it every alert. A lab that wants its
alerts acted on gets a route with its own receiver (today only evpn-prefect's incident alerts, as a webhook to its
portal). Everything else goes to `blackhole`, so the Hub and Grafana show alerts as before. Webhook tokens are not in
this repository: they live on the NMS in `/opt/monitoring-secrets/alertmanager/`, outside what `deploy.sh` syncs, and
are mounted at `/etc/alertmanager-secrets`. evpn-prefect's `./lab.sh nms-token` writes its own.

**evpn-clab** (job `evpn-clab`, SD `http://10.0.0.1:8096/api/sd`): VyOS on containerlab, management network
172.20.108.0/24 on the host's Docker bridge — the NMS reaches it through the host (its default route), no NIC of its own.
Telegraf pushes and syslog leave the containers masqueraded to the host's address. Its hostnames are evpn-fabric's
(spine1, leaf1, border1 …), so its syslog comes in on a VictoriaLogs listener of its own, **UDP 5515**, which adds
`lab=evpn-clab` (`-syslog.extraFields.udp`); its log rules (`evpn-clab-events`) select `lab:evpn-clab`, and the generic
rules, evpn-fabric's rules and evpn-fabric's dashboard exclude it (`-lab:evpn-clab`). Its alert group (`evpn-clab`) covers
OSPF adjacencies, BFD, EVPN and edge sessions, VTEPs per VNI, default-route paths, links, stopped nodes and a failing
verify. Dashboard **EVPN clab: overview**. Note FRR's OSPF logs `AdjChg: … Full -> Deleted`, not `ADJCHANGE`.

Alerts are gated on the lab being powered on (`lab_vm_running`), so a stopped lab does not raise ExporterDown / tenant
alerts; only the portals themselves are expected up at all times.

**Event overlays.** Every dashboard carries four annotation layers: portal runs (start → finish, from `labportal.grafana`,
called by `RunBase.execute`), test events (the failover cut, the reflector shutdown — `Grafana Annotate` keyword in the
Robot suites), steering changes (the portal's steering API), and the router syslog alerts above (from the `ALERTS`
series). A dip on a graph therefore comes with the reason next to it.

**FRR logging level.** VyOS renders `log syslog notifications`; FRR's state changes (`%ADJCHANGE`, BFD) are
*informational*, so by default a failover leaves no syslog trace. `srv6-core/tools/frr_logging.py` (run by that lab's
`lab.sh configure`) installs VyOS's pre-config boot hook that sets the `vyos.frr.debug` flag and raises the level live.
VyOS's rsyslog also delivers each FRR line twice and sometimes tens of seconds late — the rules `uniq` by time/host/message.

**evpn-clab's flows** (`lab:evpn-clab type:SFLOW_5` — `type` tells them from its syslog, which carries `lab=evpn-clab` too,
so the overview's syslog panels exclude `type:SFLOW_5` and the SRv6 flows dashboard `lab:evpn-clab`). One record per sampled
packet, each named in the fabric's terms: `node`, `view` (access = a leaf's host ports, fabric = a spine, edge = the wan),
`from` (the neighbour port, by its fixed MAC), `pair` ("h1 -> h3", the `_msg`), `tenant`, `vtep_src` / `vtep_dst`,
`vni_name`, `inner_*`, `app_proto` / `app_port` (the lower port: the service), `entry` (where the packet entered the lab:
count only those to count each packet once) and `est_bytes` (bytes × sampling rate). After a change to evpn-clab's
fabric.yml: `python3 flows/gen_evpn_clab.py && ./deploy.sh` (deploy restarts Fluent Bit, which does not re-read its
config or Lua). The files are truncated hourly (`flows/rotate.sh`): a throughput test writes thousands of samples a second.

**Flows.** VictoriaLogs doubles as the flow store: one record per sampled packet, queried with LogsQL `stats` from
Grafana (bytes are *sampled* bytes; multiply by the sampling rate for an estimate). The interesting dimension in this lab
is the outer IPv6 flow: `fd00:a::1 -> fd00:c:3:e000::` on p2 is "pe1 to pe3's tenant-a uDT4 SID through p2"; a
destination with three or more uSIDs is a steered packet. Akvorado would give a nicer UI at the price of ClickHouse and
Kafka on an 8 GiB NMS; goflow2 + VictoriaLogs costs nothing extra.
