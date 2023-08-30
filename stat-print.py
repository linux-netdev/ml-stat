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


def get_top(prev_stat, ppl_stat, key, subkey, n, div, filter_fn):
    ppl_prev = sorted(prev_stat.keys(), key=lambda x: prev_stat[x][key][subkey])
    ppl = sorted(ppl_stat.keys(), key=lambda x: ppl_stat[x][key][subkey])
    lines = []
    width = 0
    i = 0
    while len(lines) < n:
        i += 1
        if i >= len(ppl):
            break
        p = ppl[-i]
        if not filter_fn(p):
            continue
        if p not in ppl_prev:
            move = '***'
        else:
            prev_pos = len(ppl_prev) - ppl_prev.index(p)
            if prev_pos == i:
                move = '   '
            else:
                if prev_pos - i <= n * 2:
                    move = f'{prev_pos - i:+d}'
                else:
                    # Treat people below 2 * n as if they weren't there
                    move = '***'
        name = p.split(' <')[0]
        score = round(ppl_stat[p][key][subkey] / div)
        if score > 0:
            width = max(width, int(math.log10(score)) + 1)
        else:
            width = 1
        lines.append(f"  {i:2} ({move:>3}) [{score:{width}}] {name}")

    return [f'Top {key}s ({subkey}):'] + lines


def print_direct(mlA, mlB, key, top_extra, filter_fn=None):
    if filter_fn is None:
        filter_fn = lambda x: True
    out_keys = [
        ('reviewer', 'thr', 'msg', 25),
        ('author', 'thr', 'msg', 25),
        ('score', 'positive', 'negative', 25)
    ]
    grpA = mlA[key]
    grpB = mlB[key]
    divB = ml_stat_weeks(mlB)

    for ok in out_keys:
        left = get_top(grpA, grpB, ok[0], ok[1], ok[3] + top_extra, divB, filter_fn)
        right = get_top(grpA, grpB, ok[0], ok[2], ok[3] + top_extra, divB, filter_fn)

        for i in range(len(left)):
            print(f'{left[i]:36} {right[i]:36}')
        print()


def print_author_balance(mlB, key, top_extra):
    ppl_stat = mlB[key]
    div = ml_stat_weeks(mlB)

    ppl = sorted(ppl_stat.keys(), key=lambda x: ppl_stat[x]['author']['msg'])
    score_rank = sorted(ppl_stat.keys(), key=lambda x: -ppl_stat[x]['score']['positive'])
    ppl = list(reversed(ppl[-(15 + top_extra):]))

    print("How top authors rank in scores:")
    for i in range(len(ppl)):
        who = ppl[i]
        score = ppl_stat[who]["score"]["positive"] // div
        srank = score_rank.index(who)
        spct = srank * 100 // len(ppl_stat)
        print(f' {i+1:2}  {"p" + str(spct):>3} [{score:3}]  {who}')
    print()


def age_histogram(ml, names, filter_fn):
    histogram = {
        'unknown': 0,
        'no commit': 0,
    }

    ages = ml['ages']
    # Get array of ages in months
    now = datetime.datetime.now()
    months = []
    for name in names:
        if not filter_fn(name):
            continue
        if name not in ages:
            print('Histogram: no commit or message from', name)
            histogram['unknown'] += 1
            continue
        start_date = ages[name]
        if not start_date:
            print('Histogram: no commit (but msg) from ', name)
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
    return histogram


def age_histogram_ml(ml, role):
    def is_active(name):
        person = ml['individual'][name]
        return role in person and person[role]['msg']
    return role, age_histogram(ml, ml['individual'].keys(), is_active)


def age_histogram_commits(ml):
    return "commits", age_histogram(ml, ml['git']['commit_authors'].keys(), lambda x: True)


def print_histograms(hist_list):
    max_line = 0
    for _, histogram in hist_list:
        total = sum(histogram.values())
        max_val = max(histogram.values())
        if max_val / total > max_line:
            max_line = max_val / total

    per_dot = 50.0 / max_line

    for role, histogram in hist_list:
        print("Tenure for", role)
        total = sum(histogram.values())
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
                    dot = '*' if k <= 24 else '#'
                    t = f'{prev_k // 12:2}-{k // 12:2}yr'
                else:
                    t = f'{k // 2}mo-{k // 12}yr'
            prev_k = k
            print(f'{t:9} | {v:3} | {dot * int(v / total * per_dot)}')
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


def print_diff(mlA, mlB):
    a = mlA["count"] / ml_stat_days(mlA)
    b = mlB["count"] / ml_stat_days(mlB)
    print(f'Diff: {round((b/a - 1) * 100, 3):+.1f}% msg/day')

    a = mlA["git"]["direct_commits"] / ml_stat_days(mlA)
    b = mlB["git"]["direct_commits"] / ml_stat_days(mlB)
    print(f'Diff: {round((b/a - 1) * 100, 3):+.1f}% commits/day')

    a = len(mlA["individual"]) / ml_stat_days(mlA)
    b = len(mlB["individual"]) / ml_stat_days(mlB)
    print(f'Diff: {round((b/a - 1) * 100, 3):+.1f}% people/day')

    reviewsA = mlA["git"]["reviews"]
    reviewsB = mlB["git"]["reviews"]
    print(f'Diff: review pct: {round(reviewsB["any"]["pct"] - reviewsA["any"]["pct"], 3):+.1f}%')
    print(f'      x-corp pct: {round(reviewsB["x-company"]["pct"] - reviewsA["x-company"]["pct"], 3):+.1f}%')

    print()


def main():
    parser = argparse.ArgumentParser(description='Stats pretty printer')
    parser.add_argument('--ml-stats', type=str, nargs=2, required=True)
    parser.add_argument('--top-extra', type=int, required=False, default=0,
                        help="How many extra entries to add to the top n")
    parser.add_argument('--filter-corp', type=str, default=None,
                        help="Show people only from a selected company")
    parser.add_argument('--filter-one', type=str)
    parser.add_argument('--db', type=str)
    args = parser.parse_args()

    with open(args.ml_stats[0]) as fp:
        mlA = json.load(fp)
    with open(args.ml_stats[1]) as fp:
        mlB = json.load(fp)

    if args.filter_corp:
        if not args.db:
            parser.error('--db is required for --filter-corp')
            return

        with open(args.db, 'r') as f:
            db = json.load(f)

        filters = []
        for entry in db['corpmap']:
            if entry[1] == args.filter_corp:
                filters.append(entry[0])
        if not filters:
            print("No mappings found for company:", args.filter_corp)
            return

        def filter_fn(x):
            for fen in filters:
                if fen in x:
                    return True
            return False

        print_direct(mlA, mlB, f'individual', args.top_extra, filter_fn=filter_fn)
    elif args.filter_one:
        def filter_fn(x):
            return args.filter_one in x
        print_direct(mlA, mlB, f'individual', args.top_extra, filter_fn=filter_fn)
    else:
        print_general(mlA, 'Prev')
        print_general(mlB, 'Curr')
        print_diff(mlA, mlB)

        print_direct(mlA, mlB, 'individual', args.top_extra)
        print()
        print_direct(mlA, mlB, 'corporate', args.top_extra)

        print_author_balance(mlB, 'corporate', args.top_extra)

        histograms = [age_histogram_ml(mlB, 'reviewer'), age_histogram_ml(mlB, 'author'),
                      age_histogram_commits(mlB)]
        print_histograms(histograms)


if __name__ == "__main__":
    main()
