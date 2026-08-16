#!/usr/bin/env python3
"""Проставляет IP-адреса доменам в списке для Amnezia.

Без этого доменный список не работает. Клиент требует у записи адрес, но
резолвит его только при добавлении сайта руками:

    // ipSplitTunnelingController.cpp, addSite()
    if (addSiteInternal(normalizedHostname, {})) {
        QHostInfo::lookupHost(normalizedHostname, this, SLOT(onHostResolved(QHostInfo)));

При импорте файла (importSitesFromJson) вызова lookupHost нет — адреса
берутся только из полей "ips"/"ip". Поэтому импортированный домен с
пустым "ip" попадает в настройки без адресов: запись в интерфейсе видна,
VPN поднимается, но маршрута из неё не возникает и правило не работает.

Резолвим через российские DNS: geo-DNS отдаёт разные адреса в зависимости
от того, откуда пришёл запрос, а сборка идёт в дата-центре GitHub. Скажем,
mos.ru через 77.88.8.8 — это 94.79.51.170, а через 8.8.8.8 — 212.11.151.58.
Нужен первый: он совпадает с тем, что видит устройство пользователя.

Формат вывода — как у exportSitesToJson() того же контроллера: массив
"ips" плюс "ip" с первым адресом для обратной совместимости.

Использование: fill_ips.py <sites.json> [out.json]
"""
import ipaddress
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import dns.resolver

SRC = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else SRC
WORKERS = 64
TIMEOUT = 4

RU_DNS = ['77.88.8.8', '77.88.8.1']   # Яндекс.DNS
FALLBACK_DNS = ['8.8.8.8']            # только если российские молчат
SAMPLES = 2                           # повторов на резолвер, см. ниже


def make_resolver(server):
    r = dns.resolver.Resolver(configure=False)
    r.nameservers = [server]
    r.timeout = TIMEOUT
    r.lifetime = TIMEOUT
    return r


ru = [make_resolver(s) for s in RU_DNS]
fallback = [make_resolver(s) for s in FALLBACK_DNS]


def query(resolver, hostname):
    try:
        return {rr.address for rr in resolver.resolve(hostname, 'A')}
    except Exception:
        return set()


def resolve(hostname):
    """Глобальные IPv4 домена. CIDR и голые адреса пропускаем."""
    try:
        ipaddress.ip_network(hostname, strict=False)
        return []          # это уже адрес/подсеть — маршрут строится из hostname
    except ValueError:
        pass

    # Крупные сервисы отдают адреса по кругу: mos.ru за четыре запроса выдал
    # и 94.79.51.169-171, и 212.11.151.56-58. Одиночный запрос поймал бы
    # только один пул, поэтому опрашиваем каждый резолвер по нескольку раз
    # и объединяем ответы.
    found = set()
    for resolver in ru:
        for _ in range(SAMPLES):
            found |= query(resolver, hostname)
    if not found:
        for resolver in fallback:
            found |= query(resolver, hostname)
    # localhost/lan и перехваченный DNS дают 127.0.0.1, 0.0.0.0, серые адреса:
    # такой маршрут увёл бы loopback и локальную сеть в туннель.
    return sorted(ip for ip in found if ipaddress.ip_address(ip).is_global)


entries = json.load(open(SRC, encoding='utf-8'))
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    resolved = list(pool.map(resolve, [e['hostname'] for e in entries]))

result = [{"hostname": e['hostname'], "ips": ips, "ip": ips[0] if ips else ""}
          for e, ips in zip(entries, resolved)]

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

with_ips = sum(1 for r in result if r['ips'])
print(f"{with_ips}/{len(result)} доменов получили адреса "
      f"({sum(len(r['ips']) for r in result)} IPv4 всего)")

if with_ips < len(result) * 0.5:
    sys.exit("ошибка: адреса получены меньше чем у половины доменов")
