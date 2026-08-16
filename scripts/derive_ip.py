#!/usr/bin/env python3
"""Собирает компактный IP-список из доменных категорий.

Полный ru.txt (12 800+ подсетей) не поднимается на Android/iOS: клиент
не справляется с таким числом маршрутов в таблице VPN-интерфейса.

Здесь список выводится из тех же категорий, что и amnezia.json: домены
резолвятся, и каждый адрес заменяется на содержащий его префикс из ru.txt
(граница сети оператора). Получается ~650 подсетей вместо 12 840 — при
этом покрытие точнее, потому что внутри только сети реальных сервисов,
а не весь российский сегмент вместе с провайдерскими пулами.

Адреса, не попавшие ни в один RU-префикс (сервисы на зарубежном CDN),
добавляются отдельными /32.

Использование: derive_ip.py <domains.json> <ru.txt> <out.json>
"""
import bisect
import ipaddress
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

DOMAINS, RU_TXT, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
WORKERS = 128
TIMEOUT = 3

# --- индекс RU-префиксов для поиска покрывающей подсети ---------------------
ranges = []
for line in open(RU_TXT, encoding='utf-8'):
    line = line.strip()
    if not line or line.startswith('#') or ':' in line:  # IPv6 не поддерживается
        continue
    net = ipaddress.ip_network(line)
    ranges.append((int(net.network_address), int(net.broadcast_address), net))
ranges.sort()
starts = [r[0] for r in ranges]


def covering(ip):
    """Префикс из ru.txt, содержащий адрес, либо None."""
    i = bisect.bisect_right(starts, ip) - 1
    if i >= 0 and ip <= ranges[i][1]:
        return ranges[i][2]
    return None


# --- адреса доменов ---------------------------------------------------------
# Берём готовые из amnezia.json: их уже проставил fill_ips.py через российские
# DNS. Свой резолв здесь дал бы ответы geo-DNS для дата-центра сборки, а не
# для устройства пользователя. Fallback нужен, только если файл ещё не заполнен.
entries = json.load(open(DOMAINS, encoding='utf-8'))
addresses = {ip for e in entries for ip in e.get('ips', []) if ip}
ok = sum(1 for e in entries if e.get('ips'))

if not addresses:
    print("в списке нет адресов, резолвлю сам (запустите сначала fill_ips.py)",
          file=sys.stderr)
    socket.setdefaulttimeout(TIMEOUT)

    def resolve(name):
        try:
            return {info[4][0] for info in socket.getaddrinfo(name, None, socket.AF_INET)}
        except OSError:
            return set()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        resolved = list(pool.map(resolve, [e['hostname'] for e in entries]))
    addresses = set().union(*resolved) if resolved else set()
    ok = sum(1 for r in resolved if r)

domains = entries

# --- адрес -> покрывающий префикс (или /32 для зарубежного хостинга) --------
# Локальные зоны из доменного списка (localhost, lan, home.arpa) и домены
# с перехваченным DNS резолвятся в 127.0.0.1 / 0.0.0.0 / серые адреса.
# В split tunneling такая запись увела бы loopback и LAN в туннель и убила
# бы подключение, поэтому всё не-global отбрасываем.
nets, cdn, skipped = set(), 0, 0
for addr in addresses:
    ip = ipaddress.ip_address(addr)
    if not ip.is_global:
        skipped += 1
        continue
    net = covering(int(ip))
    if net is None:
        net = ipaddress.ip_network(addr + '/32')
        cdn += 1
    nets.add(net)

result = sorted(ipaddress.collapse_addresses(nets),
                key=lambda n: (n.network_address, n.prefixlen))
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump([{"hostname": str(n), "ip": ""} for n in result], f,
              ensure_ascii=False, indent=2)

print(f"{ok}/{len(domains)} доменов отрезолвилось -> {len(addresses)} IPv4 "
      f"-> {len(result)} подсетей ({cdn} вне RU как /32, "
      f"{skipped} локальных отброшено)")

if ok < len(domains) * 0.5:
    sys.exit("ошибка: отрезолвилось меньше половины доменов, список неполный")
