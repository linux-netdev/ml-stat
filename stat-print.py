#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime
import json
import math
import re
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


def print_direct(args, mlA, mlB, key, top_extra, filter_fn=None):
    if filter_fn is None:
        filter_fn = lambda x: True
    out_keys = [
        ('reviewer', args.group_key, 'msg', 25),
        ('author', args.group_key, 'msg', 25),
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


def rank_move(ppl_prev, person, i, n):
    if person not in ppl_prev:
        return '***'
    prev_pos = ppl_prev.index(person) + 1
    if prev_pos == i:
        return '   '
    if prev_pos - i <= n * 2:
        return f'{prev_pos - i:+d}'
    # Treat people below 2 * n as if they weren't there
    return '***'


def print_columns(left, right):
    # The two columns use different cutoffs, so they can differ in length
    for i in range(max(len(left), len(right))):
        l = left[i] if i < len(left) else ''
        r = right[i] if i < len(right) else ''
        print(f'{l:36} {r:36}')
    print()


def posting_ranking(ppl_stat, min_commits, best):
    ranking = []
    for person, stat in ppl_stat.items():
        commits = stat['author'].get('commits', 0)
        if commits < max(min_commits, 1):
            continue
        ranking.append((stat['author']['postings'] / commits, person))
    # Break ties by name, otherwise the order of people with the same
    # average would wander between releases and show up as movement
    ranking.sort(key=lambda entry: (entry[0], entry[1]), reverse=not best)
    return ranking


def get_posting_top(ppl_stat, n, min_commits, best):
    ranking = posting_ranking(ppl_stat, min_commits, best)[:n]

    direction = 'Lowest' if best else 'Highest'
    lines = [f'{direction} postings/commit (min {min_commits}):']
    # Companies rack up a lot more commits than people do
    width = max([len(str(ppl_stat[p]['author']['commits'])) for _, p in ranking]
                + [2])
    for i, (average, p) in enumerate(ranking, 1):
        commits = ppl_stat[p]['author']['commits']
        lines.append(f"  {i:2} [{commits:{width}}@{average:5.2f}]"
                     f" {p.split(' <')[0]}")

    return lines


def print_posting_ranking(args, mlB, key, min_best, min_worst):
    n = 15 + args.top_extra
    left = get_posting_top(mlB[key], n, min_best, best=True)
    right = get_posting_top(mlB[key], n, min_worst, best=False)
    if len(left) == 1 and len(right) == 1:
        return
    print_columns(left, right)


def no_commit_ranking(ppl_stat, min_postings):
    """Posted, but not one single patch of it made it into the tree"""
    ranking = []
    for person, stat in ppl_stat.items():
        author = stat['author']
        if author.get('commits', 0) or author.get('postings', 0) < min_postings:
            continue
        ranking.append((author['postings'], person))
    ranking.sort(key=lambda entry: (-entry[0], entry[1]))
    return ranking


def get_no_commit_top(prev_stat, ppl_stat, n, min_postings, caption):
    ppl_prev = [person for _, person in no_commit_ranking(prev_stat, min_postings)]
    ranking = no_commit_ranking(ppl_stat, min_postings)

    lines = [f'{caption} (min {min_postings}):']
    for i, (postings, p) in enumerate(ranking[:n], 1):
        move = rank_move(ppl_prev, p, i, n)
        lines.append(f"  {i:2} ({move:>3}) [{postings:4}] {p.split(' <')[0]}")

    return lines


def print_no_commit_ranking(args, mlA, mlB):
    # Kept out of the ranking above, no commits means no ratio to rank on
    n = 15 + args.top_extra
    left = get_no_commit_top(mlA['individual'], mlB['individual'], n,
                             args.no_commit_min_postings,
                             'Nothing merged, people')
    right = get_no_commit_top(mlA['corporate'], mlB['corporate'], n,
                              args.no_commit_corp_min_postings,
                              'Nothing merged, companies')
    if len(left) == 1 and len(right) == 1:
        return
    print_columns(left, right)


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


def age_histogram_bucketize(months, histogram):
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


def age_histogram_bucketize_uni(months, histogram):
    left = months
    i = 12
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
            i += 12
    return histogram


def age_histogram(ml, names, args, filter_fn):
    histogram = {
        'unknown': 0,
        'no commit': 0,
    }

    ages = ml['ages']
    # Get array of ages in months
    if args.hist_fixed_time:
        now = datetime.datetime.now()
    else:
        now = parsedate_to_datetime(ml['last_date'])
        now = now.replace(tzinfo=None)
    months = []
    for name in names:
        if not filter_fn(name):
            continue
        if name not in ages:
            # print('Histogram: no commit or message from', name)
            histogram['unknown'] += 1
            continue
        start_date = ages[name]
        if not start_date:
            # print('Histogram: no commit (but msg) from ', name)
            histogram['no commit'] += 1
            continue

        start = datetime.datetime.fromisoformat(start_date)
        age = (now - start).total_seconds() / 60 / 60 / 24 / 30
        months.append(age)

    if args.hist_uniform:
        return age_histogram_bucketize_uni(months, histogram)
    else:
        return age_histogram_bucketize(months, histogram)


def age_histogram_ml(ml, role, args):
    def is_active(name):
        person = ml['individual'][name]
        return role in person and person[role]['msg']
    return role, age_histogram(ml, ml['individual'].keys(),
                               args, is_active)


def age_histogram_commits(ml, args):
    return "commits", age_histogram(ml, ml['git']['commit_authors'].keys(),
                                    args, lambda x: True)


def print_histograms(args, hist_list, hist_list_old):
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
        old_hist = [h for r, h in hist_list_old if r == role][0]
        old_total = sum(histogram.values())
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

            cur_v = v / total * per_dot
            old_v = old_hist.get(k, 0) / old_total * per_dot
            normal_v = cur_v
            minus_v = 0
            plus_v = 0
            if not args.hist_diff:
                pass
            elif old_v < cur_v:
                normal_v = old_v
                plus_v = cur_v - old_v
            else:
                minus_v = old_v - cur_v

            line  = f"{dot * int(normal_v)}"
            line += f"{'+' * int(plus_v)}"
            line += f"{'.' * int(minus_v)}"
            print(f'{t:9} | {v:3} | {line}')
        print()


def dict_sum_int(a, b):
    for k, v in b.items():
        if isinstance(v, int):
            if k == 'max':
                a[k] = max(a.get(k, 0), v)
            else:
                a[k] = a.get(k, 0) + v
        elif isinstance(v, dict):
            a[k] = a.get(k, {})
            dict_sum_int(a[k], v)
        else:
            raise Exception("Int-sum of dict, bad key", k, "value", v)


def gather_cs_sub_by(cs_stat, key):
    result = {}
    for mkey, v in cs_stat.items():
        if re.match(key, mkey):
            dict_sum_int(result, v)
    return result


def print_cs_sub_by(cs_stat, caption, key):
    result = gather_cs_sub_by(cs_stat, key)
    print(caption, result, 'avg revisions:', result['sum'] / result['cnt'])
    return result


def print_change_set_stat(mlA, mlB):
    cs_stat = mlB['change-sets']

    print("Change sets:")
    acc = mlB.get('change-sets-accepted')
    prev = mlA.get('change-sets-accepted')
    if acc:
        d_acc = d_not = ''
        if prev:
            d_acc = f" ({acc['accepted_pct'] - prev['accepted_pct']:+.2f}%)"
            d_not = f" ({acc['not_accepted_pct'] - prev['not_accepted_pct']:+.2f}%)"
        print(f"Accepted:     {acc['accepted']:5} ({acc['accepted_pct']:.2f}%){d_acc}")
        print(f"Not accepted: {acc['not_accepted']:5} ({acc['not_accepted_pct']:.2f}%){d_not}")
    reviewed = print_cs_sub_by(cs_stat, "Accepted + reviewed:", '.ra')
    not_reviewed = print_cs_sub_by(cs_stat, "Accepted - reviewed:", '.-a')
    print_cs_sub_by(cs_stat, "Accepted, single:", 's.a')
    print_cs_sub_by(cs_stat, "Accepted, set:", 'm.a')
    ratio = reviewed['cnt'] / (reviewed['cnt'] + not_reviewed['cnt'])
    print(f"Review ratio: {ratio:.4}")
    print()


def role_counts(ml):
    rc = {
        'author': 0,
        'commenter': 0,
        'both': 0,
        'none': 0,
    }
    for name in ml['individual'].keys():
        person = ml['individual'][name]
        if person['reviewer']['msg'] and person['author']['msg']:
            rc['both'] += 1
        elif person['reviewer']['msg']:
            rc['commenter'] += 1
        elif person['author']['msg']:
            rc['author'] += 1

    for name in ml['git']['commit_authors']:
        if name not in ml['individual']:
            rc['none'] += 1

    return rc


def print_general(ml, key):
    print(f'{key}: start: {ml["first_date"]}\n\tend: {ml["last_date"]}')
    days = ml_stat_days(ml)
    print(f'{key}: messages: {ml["count"]} days: {days} ({round(ml["count"] / days)} msg/day)')
    commits = ml["git"]["direct_commits"]
    print(f'{key}: direct commits: {commits} ({round(commits / days)} commits/day)')
    if "direct_test_commits" in ml["git"]:
        test_commits = ml["git"]["direct_test_commits"]
        print(f'{key}: test commits: {test_commits} ({(test_commits / commits)*100:.4}%)')
    rcnt = role_counts(ml)
    print(f'{key}: people: {len(ml["individual"])}  {rcnt}')
    rsum = sum(rcnt.values())
    for k in rcnt:
        rcnt[k] = round(rcnt[k] / rsum, 3)
    print(f'{key}: people pct: {rcnt}')
    reviews = ml["git"]["reviews"]
    print(f'{key}: review pct: {reviews["any"]["pct"]}%  x-corp pct: {reviews["x-company"]["pct"]}%')

    if ml.get('change-sets'):
        cs_stat = ml['change-sets']
        s = gather_cs_sub_by(cs_stat, 's.a')
        m = gather_cs_sub_by(cs_stat, 'm.a')
        print(f"{key}: avg revisions: single patch: {s['sum'] / s['cnt']:.2f} patch set: {m['sum'] / m['cnt']:.2f}")

    if ml.get('posting-efficiency'):
        pe = ml['posting-efficiency']
        # The merged-only counts came later, older results don't have them
        merged = pe.get('per_commit_merged')
        merged = f" (merged postings only: {merged:.2f})" if merged else ''
        print(f"{key}: postings per merged commit: {pe['per_commit']:.2f}{merged}")
        merged = pe.get('postings_merged')
        merged = f" merged: {merged}" if merged else ''
        print(f"{key}: postings: {pe['postings']}{merged}"
              f" commits: {pe['commits']}")

    print()


def print_diff(mlA, mlB):
    a = mlA["git"].get("next-size", 1) / ml_stat_days(mlA)
    b = mlB["git"].get("next-size", 1) / ml_stat_days(mlB)
    tree_delta = a / b
    print(f"Diff: {round((1/tree_delta - 1) * 100, 3):+5.1f}% linux-next size")

    a = mlA["count"] / ml_stat_days(mlA)
    b = mlB["count"] / ml_stat_days(mlB)
    print(f'Diff: {round((b/a - 1) * 100, 3):+5.1f}% ({round((b/a*tree_delta - 1) * 100, 3):+5.1f}%) msg/day')

    a = mlA["git"]["direct_commits"] / ml_stat_days(mlA)
    b = mlB["git"]["direct_commits"] / ml_stat_days(mlB)
    print(f'Diff: {round((b/a - 1) * 100, 3):+5.1f}% ({round((b/a*tree_delta - 1) * 100, 3):+5.1f}%) commits/day')

    a = len(mlA["individual"]) / ml_stat_days(mlA)
    b = len(mlB["individual"]) / ml_stat_days(mlB)
    print(f'Diff: {round((b/a - 1) * 100, 3):+5.1f}% ({round((b/a*tree_delta - 1) * 100, 3):+5.1f}%) people/day')

    reviewsA = mlA["git"]["reviews"]
    reviewsB = mlB["git"]["reviews"]
    print(f'Diff: review pct: {round(reviewsB["any"]["pct"] - reviewsA["any"]["pct"], 3):+.1f}%')
    print(f'      x-corp pct: {round(reviewsB["x-company"]["pct"] - reviewsA["x-company"]["pct"], 3):+.1f}%')

    if mlA.get('change-sets') and  mlB.get('change-sets'):
        a = gather_cs_sub_by(mlA['change-sets'], 's.a')
        aS = a['sum'] / a['cnt']
        a = gather_cs_sub_by(mlA['change-sets'], 'm.a')
        aM = a['sum'] / a['cnt']

        a = gather_cs_sub_by(mlB['change-sets'], 's.a')
        bS = a['sum'] / a['cnt']
        a = gather_cs_sub_by(mlB['change-sets'], 'm.a')
        bM = a['sum'] / a['cnt']

        print(f"Diff: avg revisions: single patch: {round((bS/aS - 1) * 100, 3):+.1f}% " + \
              f"patch set: {round((bM/aM - 1) * 100, 3):+.1f}%")

    print()


def print_test_authors(mlA, ml, top_extra):
    now_ppl = len( ml["git"]["test_commit_authors"])
    was_ppl = len(mlA["git"]["test_commit_authors"])
    print("Contributions to selftests: total individuals:",
          now_ppl, f"(was {was_ppl}, {100 * (now_ppl - was_ppl) / was_ppl:+.2f}%)")

    print("Contributions to selftests:")
    authors = ml["git"]["test_commit_authors"]
    top = sorted(authors, key=lambda x:authors[x], reverse=True)
    i = 0
    last = 0
    for person in top:
        i += 1
        name = person.split('<')[0].strip()
        score = authors[person]
        # Make sure we print all entries with the score of the last position
        if i > 10 + top_extra and score != last:
            break
        print(f"  {i:2} [{score:3}] {name}")
        last = score
    print()


def main():
    parser = argparse.ArgumentParser(description='Stats pretty printer')
    parser.add_argument('--ml-stats', type=str, nargs=2, required=True)
    parser.add_argument('--group-key', type=str, default="cs",
                        help="Group left column by change sets (cs) or threads (thr)")
    parser.add_argument('--top-extra', type=int, required=False, default=0,
                        help="How many extra entries to add to the top n")
    parser.add_argument('--posting-best-min-commits', type=int, default=5,
                        help="Min merged commits for the lowest postings/commit ranking")
    parser.add_argument('--posting-worst-min-commits', type=int, default=5,
                        help="Min merged commits for the highest postings/commit ranking")
    parser.add_argument('--posting-corp-best-min-commits', type=int, default=10,
                        help="As above, for the company ranking")
    parser.add_argument('--posting-corp-worst-min-commits', type=int, default=10,
                        help="As above, for the company ranking")
    parser.add_argument('--no-commit-min-postings', type=int, default=30,
                        help="Min postings to list someone who got nothing merged")
    parser.add_argument('--no-commit-corp-min-postings', type=int, default=30,
                        help="As above, for the company ranking")
    parser.add_argument('--filter-corp', type=str, default=None,
                        help="Show people only from a selected company")
    parser.add_argument('--filter-one', type=str)
    parser.add_argument('--hist-uniform', default=False, action="store_true",
                        help="Make all histogram buckets 1 year")
    parser.add_argument('--hist-diff', default=False, action="store_true",
                        help="Show histogram diff rather than current values")
    parser.add_argument('--hist-fixed-time', default=False, action="store_true",
                        help="Make all age calculation relative to now")
    parser.add_argument('--db', type=str)
    args = parser.parse_args()

    if args.group_key not in ['cs', 'thr']:
        raise argparse.ArgumentError('--group-key must be cs or thr')

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

        print_direct(args, mlA, mlB, f'individual', args.top_extra, filter_fn=filter_fn)
    elif args.filter_one:
        def filter_fn(x):
            return args.filter_one in x
        print_direct(args, mlA, mlB, f'individual', args.top_extra, filter_fn=filter_fn)
    else:
        print_general(mlA, 'Prev')
        print_general(mlB, 'Curr')
        print_diff(mlA, mlB)

        if mlB["git"].get("test_commit_authors"):
            print_test_authors(mlA, mlB, args.top_extra)

        print_direct(args, mlA, mlB, 'individual', args.top_extra)
        print()
        print_direct(args, mlA, mlB, 'corporate', args.top_extra)

        print_author_balance(mlB, 'corporate', args.top_extra)

        histograms_old = [age_histogram_ml(mlA, 'reviewer', args),
                          age_histogram_ml(mlA, 'author', args),
                          age_histogram_commits(mlA, args)]
        histograms = [age_histogram_ml(mlB, 'reviewer', args),
                      age_histogram_ml(mlB, 'author', args),
                      age_histogram_commits(mlB, args)]
        print_histograms(args, histograms, histograms_old)
        print_posting_ranking(args, mlB, 'individual',
                              args.posting_best_min_commits,
                              args.posting_worst_min_commits)
        print_posting_ranking(args, mlB, 'corporate',
                              args.posting_corp_best_min_commits,
                              args.posting_corp_worst_min_commits)
        print_no_commit_ranking(args, mlA, mlB)
        if mlB.get('change-sets'):
            print_change_set_stat(mlA, mlB)


if __name__ == "__main__":
    main()
