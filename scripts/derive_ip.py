#!/usr/bin/env python3
"""Собирает компактный IP-список из доменных категорий.

Полный ru.txt (12 800+ подсетей) не поднимается на Android/iOS: клиент
не справляется с таким числом маршрутов в таблице VPN-интерфейса.

Здесь список выводится из адресов, которые fill_ips.py уже проставил
доменам в amnezia.json. Каждый адрес расширяется до сети владельца:

* адрес принадлежит небольшой российской автономной системе (порог
  AS_SIZE_CAP) — берём её целиком. Это сети конкретных сервисов, и
  расширение закрывает эндпоинты, которых нет в geosite. Например, Ozon
  владеет AS44386 и AS207986: DNS выводит на 11 их префиксов из 16, а
  мобильное приложение ходит и в остальные пять;
* адрес принадлежит крупной провайдерской AS (Ростелеком и подобные) —
  расширять её нельзя, иначе список раздувается до нескольких тысяч
  маршрутов. Берём только покрывающий префикс из ru.txt;
* адрес вне RU (сервис на зарубежном CDN) — добавляем как /32.

Получается ~1000 подсетей вместо 12 840 при более точном покрытии:
внутри только сети реальных сервисов, а не весь российский сегмент.

Использование: derive_ip.py <sites.json> <ru.txt> <ip2asn.tsv> <out.json>
"""
import bisect
import ipaddress
import json
import sys
from collections import defaultdict

SITES, RU_TXT, IP2ASN, OUT = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

# Порог «сервисная AS против провайдерской»: 16384 адреса — это /18.
# Ozon, Wildberries и прочие укладываются, магистральные операторы — нет.
AS_SIZE_CAP = 1 << 14


def load_index(path):
    """Отсортированные диапазоны + список начал для двоичного поиска."""
    ranges = []
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or ':' in line:  # IPv6 не поддерживается
            continue
        net = ipaddress.ip_network(line)
        ranges.append((int(net.network_address), int(net.broadcast_address), net))
    ranges.sort()
    return ranges, [r[0] for r in ranges]


ru_ranges, ru_starts = load_index(RU_TXT)


def covering(ip):
    """Префикс из ru.txt, содержащий адрес, либо None."""
    i = bisect.bisect_right(ru_starts, ip) - 1
    if i >= 0 and ip <= ru_ranges[i][1]:
        return ru_ranges[i][2]
    return None


# --- карта адрес -> автономная система --------------------------------------
asn_ranges = []
asn_blocks = defaultdict(list)
asn_country = {}
for line in open(IP2ASN, encoding='utf-8'):
    first, last, asn, country, _name = line.rstrip('\n').split('\t')
    asn = int(asn)
    if asn == 0:  # не анонсируется
        continue
    lo, hi = int(ipaddress.ip_address(first)), int(ipaddress.ip_address(last))
    asn_ranges.append((lo, hi, asn))
    asn_blocks[asn].append((lo, hi))
    asn_country[asn] = country
asn_ranges.sort()
asn_starts = [r[0] for r in asn_ranges]
asn_size = {asn: sum(hi - lo + 1 for lo, hi in blocks)
            for asn, blocks in asn_blocks.items()}


def owner_asn(ip):
    i = bisect.bisect_right(asn_starts, ip) - 1
    if i >= 0 and ip <= asn_ranges[i][1]:
        return asn_ranges[i][2]
    return None


# --- адреса доменов ---------------------------------------------------------
# Берём готовые из amnezia.json: их проставил fill_ips.py через российские DNS.
# Свой резолв дал бы ответы geo-DNS для дата-центра сборки, а не для устройства.
entries = json.load(open(SITES, encoding='utf-8'))
addresses = {ip for e in entries for ip in e.get('ips', []) if ip}
if not addresses:
    sys.exit("в списке нет адресов — сначала запустите fill_ips.py")

nets = []
expanded, by_prefix, cdn = set(), 0, 0
for addr in addresses:
    ip = int(ipaddress.ip_address(addr))
    asn = owner_asn(ip)
    if asn and asn_country.get(asn) == 'RU' and asn_size[asn] <= AS_SIZE_CAP:
        if asn not in expanded:
            expanded.add(asn)
            for lo, hi in asn_blocks[asn]:
                nets += ipaddress.summarize_address_range(
                    ipaddress.ip_address(lo), ipaddress.ip_address(hi))
        continue
    net = covering(ip)
    if net is None:
        net = ipaddress.ip_network(addr + '/32')
        cdn += 1
    else:
        by_prefix += 1
    nets.append(net)

result = sorted(ipaddress.collapse_addresses(nets),
                key=lambda n: (n.network_address, n.prefixlen))
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump([{"hostname": str(n), "ip": ""} for n in result], f,
              ensure_ascii=False, indent=2)

print(f"{len(addresses)} адресов -> {len(result)} подсетей "
      f"({len(expanded)} сервисных AS целиком, {by_prefix} по префиксу ru.txt, "
      f"{cdn} вне RU как /32)")
