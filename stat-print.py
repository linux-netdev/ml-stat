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


if __name__ == "__main__":
    main()
