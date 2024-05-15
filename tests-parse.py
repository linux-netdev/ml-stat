#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import json


with open("all-results.json") as fp:
    db = json.load(fp)

rmap = {}

has_retry = {}
for run in db:
    if not run.get('results'):
        continue

    for t in run['results']:
        rname = run['executor'] + '/' + t['group'] + '/' + t['test']
        robj = rmap.get(rname, {})
        result = t['result']
        if t.get('retry'):
            has_retry[t.get('retry')] = has_retry.get(t['retry'], 0) + 1
        if result == 'fail' and t.get('retry') == "pass":
            result = 'flake'
        robj[result] = robj.get(result, 0) + 1
        rmap[rname] = robj

all_pass = set()
all_same = set()
for k, v in rmap.items():
    if len(v) == 1:
        all_same.add(k)
        if 'pass' in v:
            all_pass.add(k)

print("Total tests:", len(rmap))
print("All same:", len(all_same))
print("All pass:", len(all_pass))
print("Has retry:", has_retry)

for k in all_same:
    del rmap[k]

no_pass = set()
for k, v in rmap.items():
    if 'pass' not in v:
        no_pass.add(k)

print("No pass:", len(no_pass))

for k in no_pass:
    del rmap[k]

print("Left tests:", len(rmap))

solid = []
for k, v in rmap.items():
    cnt = 0
    for res in v:
        cnt += v[res]
    v["cnt"] = cnt
    v["name"] = k

    v["pass-rate"] = v["pass"] / v["cnt"]

    if v["pass-rate"] > 0.97 and 'flake' not in v:
        solid.append(v)

print()
print("Solid (pass > 98%) tests:", len(solid))
solid = sorted(solid, key=lambda x: x.get("fail", 0), reverse=True)
for i in range(15):
    v = solid[i]

    name = v["name"].split('/')
    grp = name[0]
    grp.replace('vmksft-', '')
    test = name[2]

    print(f"{i+1:2} | {grp:22} {test:26} {v.get('fail')}")
