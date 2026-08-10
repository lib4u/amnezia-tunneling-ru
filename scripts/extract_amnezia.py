#!/usr/bin/env python3
import json, sys, os

DATA = sys.argv[1]
CATEGORIES = sys.argv[2].split()
OUT = sys.argv[3]

seen_files = set()
hostnames = set()

def process(name):
    if name in seen_files: return
    seen_files.add(name)
    path = os.path.join(DATA, name)
    if not os.path.isfile(path):
        print(f"warning: category '{name}' not found", file=sys.stderr)
        return
    for line in open(path, encoding='utf-8'):
        line = line.split('#', 1)[0].strip()
        if not line: continue
        # отрезаем атрибуты (@ads, @cn и т.п.)
        value = line.split()[0]
        if value.startswith('include:'):
            process(value[len('include:'):])
        elif value.startswith('full:'):
            hostnames.add(value[len('full:'):])
        elif value.startswith(('regexp:', 'keyword:')):
            continue  # в Amnezia-формат не переносятся
        elif value.startswith('domain:'):
            hostnames.add(value[len('domain:'):])
        else:
            hostnames.add(value)  # голая строка = domain-суффикс

for cat in CATEGORIES:
    process(cat)

result = [{"hostname": h, "ip": ""} for h in sorted(hostnames)]
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"{len(result)} hostnames from {len(seen_files)} category files")
