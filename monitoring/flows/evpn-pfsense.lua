-- Fluent Bit filter for evpn-pfsense's sFlow records (goflow2 JSON, evpn-clab-mapping.yaml — the same VXLAN offsets): says
-- each sample in the fabric's terms. The names come from the lab's fabric.yml (evpn-pfsense-names.lua, gen_evpn_pfsense.py).
-- Generated from evpn-clab.lua's logic; the views: spine = fabric, leaf = access, border = edge (what the firewall pairs send back).
--   drops hsflowd's egress samples (in_if = 0x3fffffff): each packet is counted once per view, where it entered
--   node / role / view   the exporter: spine = "fabric" (VXLAN between VTEPs), leaf = "access" (what the hosts send),
--                        wan = "edge" (north-south through the firewall)
--   from                 the neighbour port the packet came from (port MACs are 02:ec:01:00:<idx>:<port>)
--   src_name / dst_name  the outer addresses named; for VXLAN also vtep_src / vtep_dst, vni_name, inner_* and inner names
--   host_src / host_dst  the endpoints whatever the encapsulation (inner for VXLAN); pair = "h1 -> h3"
--   app_proto / app_port the (inner) protocol and the lower of the two ports — the service, whichever way the packet goes
--   tenant               from the VNI, the edge VLAN or the hosts' subnets
--   entry                true where a packet enters the lab (an access port, or the wan from the internet): counting only
--                        these counts each packet once — the top talkers
--   est_bytes            bytes x sampling rate (an estimate of the real traffic)
local N = dofile("/etc/flows/evpn-pfsense-names.lua")
local EGRESS = 1073741823
local PROTO = {[1] = "ICMP", [6] = "TCP", [17] = "UDP"}
local INNER = {"vxlan_vni", "inner_etype", "inner_src_addr", "inner_dst_addr", "inner_proto", "inner_src_port", "inner_dst_port"}

local function ip2n(a)
  local o1, o2, o3, o4 = string.match(a or "", "^(%d+)%.(%d+)%.(%d+)%.(%d+)$")
  if not o1 then return nil end
  return ((tonumber(o1) * 256 + tonumber(o2)) * 256 + tonumber(o3)) * 256 + tonumber(o4)
end

local NETS = {}
for _, n in ipairs(N.tenant_nets) do
  local base, len = string.match(n[1], "^(.+)/(%d+)$")
  local size = 2 ^ (32 - tonumber(len))
  NETS[#NETS + 1] = {lo = ip2n(base), hi = ip2n(base) + size - 1, tenant = n[2]}
end

local function tenant_of(a)
  local x = ip2n(a)
  if not x then return nil end
  for _, n in ipairs(NETS) do if x >= n.lo and x <= n.hi then return n.tenant end end
  return nil
end

local function service_port(a, b)                     -- the lower port is the service (5201), the other ephemeral
  if not a or a == 0 then return b or 0 end
  if not b or b == 0 then return a end
  return math.min(a, b)
end

local function port_of(mac)                           -- a data port's fixed MAC: 02:ec:01:00:<idx>:<port>
  local idx, port = string.match(mac or "", "^02:ec:01:00:(%x%x):(%x%x)$")
  if idx and N.port_macs[idx] then return N.port_macs[idx] .. " eth" .. tonumber(port, 16) end
  return nil
end

function enrich(tag, ts, r)
  if r.in_if == EGRESS then return -1, ts, r end
  r.lab = N.lab
  local e = N.exporters[r.sampler_address]
  if e then r.node, r.role, r.view = e.node, e.role, e.view else r.node = r.sampler_address end
  r.src_name, r.dst_name = N.addresses[r.src_addr], N.addresses[r.dst_addr]
  r.from = port_of(r.src_mac)

  if r.proto == "UDP" and r.dst_port == 4789 then
    r.encap = "vxlan"
    r.vtep_src, r.vtep_dst = r.src_name or r.src_addr, r.dst_name or r.dst_addr
    local v = N.vnis[r.vxlan_vni]
    if v then r.vni_name, r.tenant = v.name, v.tenant end
    if r.inner_etype == 2048 then
      r.inner_proto_name = PROTO[r.inner_proto] or tostring(r.inner_proto)
      if r.inner_proto ~= 6 and r.inner_proto ~= 17 then r.inner_src_port, r.inner_dst_port = 0, 0 end
      r.inner_src_name, r.inner_dst_name = N.addresses[r.inner_src_addr], N.addresses[r.inner_dst_addr]
      r.host_src = r.inner_src_name or r.inner_src_addr
      r.host_dst = r.inner_dst_name or r.inner_dst_addr
      r.app_proto, r.app_port = r.inner_proto_name, service_port(r.inner_src_port, r.inner_dst_port)
    else                                                -- ARP and the like: no IPv4 header where the offsets look
      r.inner_proto_name = r.inner_etype == 2054 and "ARP" or string.format("0x%04x", r.inner_etype or 0)
      r.inner_src_addr, r.inner_dst_addr, r.inner_proto, r.inner_src_port, r.inner_dst_port = "", "", 0, 0, 0
      r.host_src, r.host_dst, r.app_proto, r.app_port = r.vtep_src, r.vtep_dst, r.inner_proto_name, 0
    end
  elseif r.etype ~= "IPv4" and r.etype ~= "IPv6" then    -- ARP, LACP …: no addresses; say the port it came from instead
    for _, k in ipairs(INNER) do r[k] = nil end
    r.host_src = r.from or r.src_mac
    r.host_dst = (r.dst_mac == "ff:ff:ff:ff:ff:ff") and "broadcast" or (string.sub(r.dst_mac or "", 1, 8) == "01:80:c2" and "slow-protocols") or port_of(r.dst_mac) or r.dst_mac
    local etype = (r.etype ~= nil and r.etype ~= "" and r.etype ~= 0) and tostring(r.etype) or nil
    r.app_proto = etype or (r.host_dst == "slow-protocols" and "LACP") or "non-IP"
    r.app_port, r.proto = 0, nil                       -- goflow2 says HOPOPT (protocol 0) for a frame with no IP header
  else
    for _, k in ipairs(INNER) do r[k] = nil end
    r.host_src, r.host_dst = r.src_name or r.src_addr, r.dst_name or r.dst_addr
    r.app_proto, r.app_port = r.proto, ((r.proto == "TCP" or r.proto == "UDP") and service_port(r.src_port, r.dst_port) or 0)
  end
  r.pair = tostring(r.host_src) .. " -> " .. tostring(r.host_dst)
  r.msg = r.pair                                      -- VictoriaLogs takes _msg from this (and consumes it)
  if not r.tenant and r.vlan_id and r.vlan_id > 0 then r.tenant = N.edge_vlans[r.vlan_id] end
  if not r.tenant then r.tenant = tenant_of(r.src_addr) or tenant_of(r.dst_addr) or tenant_of(r.inner_src_addr) end
  if not r.tenant then r.tenant = "none" end          -- the fabric's own traffic: BFD, OSPF, BGP
  -- entry: where a packet enters the lab — an access port, or the internet coming back in at a border (not a tenant's address)
  r.entry = (r.view == "access") or (r.view == "edge" and tenant_of(r.src_addr) == nil and N.addresses[r.src_addr] == nil)
  r.est_bytes = (r.bytes or 0) * (r.sampling_rate or 1)
  r.est_packets = (r.packets or 1) * (r.sampling_rate or 1)
  return 2, ts, r
end
