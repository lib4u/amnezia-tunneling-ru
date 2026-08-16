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
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import dns.resolver

SRC = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else SRC
WORKERS = 128
TIMEOUT = 4

# Раннер GitHub режет исходящий UDP/53: сборка через обычные резолверы
# получила адреса лишь у 65% доменов против 84% локально. Основа поэтому —
# DNS-over-HTTPS, он идёт по 443 и не троттлится.
DOH = ['https://dns.google/resolve', 'https://cloudflare-dns.com/dns-query']
# Яндекс по UDP — best effort: DoH-эндпоинта у него нет, а geo-корректные
# ответы нужны (mos.ru отдаёт российскому резолверу 94.79.51.x, а Google —
# 212.11.151.x). Если UDP закрыт, список соберётся и без него.
RU_DNS = ['77.88.8.8', '77.88.8.1']
SAMPLES = 2                               # повторов на резолвер
RETRIES = 2


def make_resolver(server):
    r = dns.resolver.Resolver(configure=False)
    r.nameservers = [server]
    r.timeout = TIMEOUT
    r.lifetime = TIMEOUT
    return r


ru = [make_resolver(s) for s in RU_DNS]


def query_doh(url, hostname):
    request = urllib.request.Request(
        f"{url}?name={urllib.parse.quote(hostname)}&type=A",
        headers={'Accept': 'application/dns-json'})
    for _ in range(RETRIES):
        try:
            answer = json.load(urllib.request.urlopen(request, timeout=TIMEOUT))
            return {a['data'] for a in answer.get('Answer', []) if a.get('type') == 1}
        except Exception:
            continue
    return set()


def query(resolver, hostname):
    for _ in range(RETRIES):
        try:
            return {rr.address for rr in resolver.resolve(hostname, 'A')}
        except dns.resolver.NXDOMAIN:
            return set()          # домена нет — повторять незачем
        except Exception:
            continue              # таймаут или отказ — пробуем ещё раз
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
    # DoH опрашиваем ВСЕГДА, а не только когда российские резолверы молчат:
    # под троттлингом они не отвечают отказом, а висят до таймаута, и условный
    # фоллбэк срабатывал через раз. Сборка тогда потеряла 600 доменов вместе с
    # finance.ozon.ru, а с ним и три префикса Ozon Bank.
    for url in DOH:
        found |= query_doh(url, hostname)
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

# Норма — около 84% (остальное мёртвые домены апстрима). Порог ловит
# троттлинг резолверов: обеднённый список молча выкинул бы целые сети,
# как это случилось с Ozon Bank при 54%.
share = with_ips / len(result)
if share < 0.75:
    sys.exit(f"ошибка: адреса лишь у {share:.0%} доменов, ожидается ~84% — "
             f"похоже на троттлинг DNS, список публиковать нельзя")
