#!/usr/bin/env python3
"""Конвертирует текстовый CIDR-список (v2fly/geoip) в JSON-формат Amnezia.

Оставляет только IPv4 (Amnezia не поддерживает IPv6) и схлопывает
смежные подсети для уменьшения размера списка.
"""
import ipaddress, json, sys

SRC, OUT = sys.argv[1], sys.argv[2]

nets = []
for line in open(SRC, encoding='utf-8'):
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    try:
        net = ipaddress.ip_network(line, strict=False)
    except ValueError:
        print(f"warning: skip invalid entry {line!r}", file=sys.stderr)
        continue
    if net.version == 4:
        nets.append(net)

collapsed = list(ipaddress.collapse_addresses(nets))
result = [{"hostname": str(n), "ip": ""} for n in collapsed]
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"{len(nets)} IPv4 CIDRs -> {len(collapsed)} after collapsing")
