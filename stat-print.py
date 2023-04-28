#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime
import json
import math
from email.utils import parsedate_to_datetime


def ml_stat_days(ml):
    start = parsedate_to_datetime(ml['first_date'])
    end = parsedate_to_datetime(ml['last_date'])

    return round((end - start).total_seconds() / 60 / 60 / 24)


def ml_stat_weeks(ml):
    start = parsedate_to_datetime(ml['first_date'])
    end = parsedate_to_datetime(ml['last_date'])

    return round((end - start).total_seconds() / 60 / 60 / 24 / 7)


def get_top(prev_stat, ppl_stat, key, subkey, n, div):
    lines = [f'Top {n} {key}s ({subkey}):']
    ppl_prev = sorted(prev_stat.keys(), key=lambda x: prev_stat[x][key][subkey])
    ppl = sorted(ppl_stat.keys(), key=lambda x: ppl_stat[x][key][subkey])
    width = 0
    for i in range(1, n + 1):
        p = ppl[-i]
        if p not in ppl_prev:
            move = '**'
        else:
            prev_pos = len(ppl_prev) - ppl_prev.index(p)
            if prev_pos == i:
                move = '=='
            else:
                move = f'{prev_pos - i:+d}'
        name = p.split(' <')[0]
        score = round(ppl_stat[p][key][subkey] / div)
        width = max(width, int(math.log10(score)) + 1)
        lines.append(f"  {i:2} ({move}) [{score:{width}}] {name}")
    return lines


def print_direct(mlA, mlB, key, top_extra):
    out_keys = [
        ('reviewer', 'thr', 'msg', 15),
        ('author', 'thr', 'msg', 15),
        ('score', 'positive', 'negative', 15)
    ]
    grpA = mlA[key]
    grpB = mlB[key]
    divB = ml_stat_weeks(mlB)

    for ok in out_keys:
        left = get_top(grpA, grpB, ok[0], ok[1], ok[3] + top_extra, divB)
        right = get_top(grpA, grpB, ok[0], ok[2], ok[3] + top_extra, divB)

        for i in range(len(left)):
            print(f'{left[i]:36} {right[i]:36}')
        print()


def age_histogram(ml, role, per_dot=None):
    print('Tenure histogram for', role)
    histogram = {
        'unknown': 0,
        'no commit': 0,
    }

    ages = ml['ages']
    # Get array of ages in months
    now = datetime.datetime.now()
    months = []
    for name in ml['individual'].keys():
        person = ml['individual'][name]
        if role not in person or not person[role]['msg']:
            continue
        if name not in ages:
            histogram['unknown'] += 1
            continue
        start_date = ages[name]
        if not start_date:
            histogram['no commit'] += 1
            continue

        start = datetime.datetime.fromisoformat(start_date)
        age = (now - start).total_seconds() / 60 / 60 / 24 / 30
        months.append(age)

    left = months
    i = 3
    while len(left):
        months = left
        left = []
        histogram[i] = 0
        for m in months:
            if m < i:
                histogram[i] += 1
            else:
                left.append(m)
        if i < 24:
            i *= 2
        else:
            i += 24
    max_cnt = max(histogram.values())
    if per_dot is None:
        per_dot = 50.0 / max_cnt
    for k, v in histogram.items():
        dot = '*'
        if isinstance(k, str):
            t = k
        elif k < 12:
            if k > 3:
                t = f'{k // 2:2}-{k:2}mo'
            else:
                t = f' 0-{k:2}mo'
        else:
            if k > 12:
                dot = '*' if k < 24 else '#'
                t = f'{prev_k // 12:2}-{k // 12:2}yr'
            else:
                t = f'{k // 2}mo-{k // 12}yr'
        prev_k = k
        print(f'{t:9} | {v:3} | {dot * int(v * per_dot)}')
    print()
    return per_dot  # Try to keep the scale across histograms


def role_counts(ml):
    rc = {
        'author': 0,
        'commenter': 0,
        'both': 0,
    }
    for name in ml['individual'].keys():
        person = ml['individual'][name]
        if person['reviewer']['msg'] and person['author']['msg']:
            rc['both'] += 1
        elif person['reviewer']['msg']:
            rc['commenter'] += 1
        elif person['author']['msg']:
            rc['author'] += 1

    return rc


def print_general(ml, key):
    print(f'{key}: start: {ml["first_date"]}\n\tend: {ml["last_date"]}')
    days = ml_stat_days(ml)
    print(f'{key}: messages: {ml["count"]} days: {days} ({round(ml["count"] / days)} msg/day)')
    commits = ml["git"]["direct_commits"]
    print(f'{key}: direct commits: {commits} ({round(commits / days)} commits/day)')
    rcnt = role_counts(ml)
    print(f'{key}: people/aliases: {len(ml["individual"])}  {rcnt}')
    reviews = ml["git"]["reviews"]
    print(f'{key}: review pct: {reviews["any"]["pct"]}%  x-corp pct: {reviews["x-company"]["pct"]}%')
    print()


def main():
    parser = argparse.ArgumentParser(description='Stats pretty printer')
    parser.add_argument('--ml-stats', type=str, nargs=2, required=True)
    parser.add_argument('--top-extra', type=int, required=False, default=0,
                        help="How many extra entries to add to the top n")
    args = parser.parse_args()

    with open(args.ml_stats[0]) as fp:
        mlA = json.load(fp)
    with open(args.ml_stats[0]) as fp:
        mlB = json.load(fp)

    print_general(mlA, 'Prev')
    print_general(mlB, 'Curr')

    print_direct(mlA, mlB, 'individual', args.top_extra)
    print()
    print_direct(mlA, mlB, 'corporate', args.top_extra)

    scale = age_histogram(mlA, 'reviewer')
    age_histogram(mlA, 'author', scale)


if __name__ == "__main__":
    main()
