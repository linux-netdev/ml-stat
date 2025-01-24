#!/usr/bin/env python3

# Have a person in mind, analyze git logs to extract areas where they
# could be listed as a maintainer.
#
# Workflow:
#
# 1. Extract the commit info:
#
#  $path/what_should_maint.py --who $name --paths "net/" "include/net/" "include/linux/" "include/uapi/linux/" \
#     --save data.json
#
# 2. Show the stats
#
#  $path/what_should_maint.py --who $name --load data.json --top 25
#
# 3. Double check entry
#
#  $path/what_should_maint.py --who $name --load data.json \
#     --entry net/core/net_namespace.c include/net/net_namespace.h  'include/net/netns/*'

import argparse
import fnmatch
import json
import os
import subprocess


args = None


def commitify(haystack):
    commits = []
    commit = None

    for line in haystack.split('\n'):
        if line.startswith("commit"):
            commit = {
                "hash": line.split()[1],
                "author": None,
                "reviewers": [],
            }
            commits.append(commit)

        if line.startswith("Author"):
            commit["author"] = line[8:]

        line = line.strip()
        if line.startswith("Reviewed-by:"):
            who = " ".join(line.split()[1:])
            commit["reviewers"].append(who)
        if line.startswith("Acked-by:"):
            who = " ".join(line.split()[1:])
            commit["reviewers"].append(who)

    return commits


def is_excluded(path):
    global args
    for excluded in args.exclude:
        if path.startswith(excluded):
            return True
    return False


def analyze():
    global args

    cmd = subprocess.run(["find"] + args.paths + ['-type', 'f'],
                         stdout=subprocess.PIPE)
    files = cmd.stdout.decode("utf-8").strip().split()

    stats = []

    seq = 0
    for path in files:
        seq += 1
        print(f"Analyze {seq}/{len(files)} ({path})" + " " * max(58 - len(path), 0),
              end="\r")

        if is_excluded(path):
            continue

        cmd = subprocess.run(["git", "log", "--no-merges", "--since=" + args.since, "--", path],
                             stdout=subprocess.PIPE)
        git_log = cmd.stdout.decode("utf-8").strip()

        commits = commitify(git_log)

        auths = 0
        revs = 0
        for c in commits:
            if args.who in c["author"]:
                auths += 1
                continue

            for r in c["reviewers"]:
                if args.who in r:
                    revs += 1
                    break

        stat = {
            "path": path,
            "commits": commits,
        }
        stats.append(stat)
    print()

    return stats


def fnmatch_any(path, globs):
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
    return False


def entry_mode(stats, entry):
    files = 0
    commits = {}
    authors = {}
    reviewers = {}

    for stat in stats:
        if not fnmatch_any(stat["path"], entry):
            continue
        files += 1

        for c in stat["commits"]:
            if c["hash"] in commits:
                continue

            for r in c["reviewers"]:
                if r not in reviewers:
                    reviewers[r] = []
                reviewers[r].append(c)

            if c["author"] not in authors:
                authors[c["author"]] = []
            authors[c["author"]].append(c)
            commits[c["hash"]] = c

    top_a = sorted(authors.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    top_r = sorted(reviewers.items(), key=lambda x: len(x[1]), reverse=True)[:10]

    print("Files:", files)
    print("Commits:", len(commits))
    print("Authors:", len(authors))
    for name, l in top_a:
        print(f"  {name} {len(l)}")
    print("Reviewers:", len(reviewers))
    for name, l in top_r:
        print(f"  {name} {len(l)}")


def commit_cnt(d):
    if d["commits"]:
        return len(d["commits"])
    return 1


def of_reviewed_pct(d):
    global args

    rev = 0
    all_rev = 0
    for c in d["commits"]:
        if args.who in c["author"]:
            continue

        for r in c["reviewers"]:
            if args.who in r:
                rev += 1
                break

        if c["reviewers"]:
            all_rev += 1

    return rev / max(all_rev, 1)


def pr_header():
    print("{:33} {:>6}  {:<12} {:<12} {:<8}".format("", "total", "author", "review", "metric"))


def pr_stat(i, s, extra=0):
    if extra:
        extra = "{:5}%".format(round(extra * 100, 2))
    else:
        extra = ""
    tot = len(s["commits"])
    print("{:2} {:30} {:6} {:3} ({:5}%) {:3} ({:5}%)  {:2}".
          format(i, s["path"], tot,
                 s["author"],
                 round(s["author"] / tot * 100, 2),
                 s["reviewer"],
                 round(s["reviewer"] / tot * 100, 2),
                 extra))


def main():
    parser = argparse.ArgumentParser(description='Extract reviewer metrics')
    parser.add_argument('--linux', type=str, default=os.getcwd(),
                        help="Path to the Linux kernel git tree")
    parser.add_argument('--since', type=str, help="Lookback period / argument to pass to --since for git log",
                        default="3 years ago")
    parser.add_argument('--paths', type=str, nargs='*', default=[],
                        help="Directories to narrow the search down to")
    parser.add_argument('--exclude', type=str, nargs='*', default=[],
                        help="Directories (prefixes of files) to skip")
    parser.add_argument('--who', type=str, required=True,
                        help='Author of interest (needle, as in "John" will match all "Johns")')
    parser.add_argument('--save', type=str,
                        help='Save full analysis to a JSON file for faster querying')
    parser.add_argument('--load', type=str,
                        help='Load analysis from previously saved JSON file')
    parser.add_argument('--top', type=int, default=15,
                        help='How many top files to display')
    parser.add_argument('--entry', type=str, nargs='*',
                        help='Inverse mode, list maintainers for given files')
    global args
    args = parser.parse_args()

    if args.load:
        with open(args.load, "r") as fp:
            stats = json.load(fp)
    else:
        stats = analyze()
        if args.save:
            with open(args.save, 'w') as fp:
                json.dump(stats, fp)

    for stat in stats:
        auths = 0
        revs = 0
        for c in stat["commits"]:
            if args.who in c["author"]:
                auths += 1
                continue

            for r in c["reviewers"]:
                if args.who in r:
                    revs += 1
                    break
        stat["reviewer"] = revs
        stat["author"] = auths

    if args.entry:
        entry_mode(stats, args.entry)
        return

    pr_header()
    print("Top reviewer pct (not counting authored by self)")
    stats = sorted(stats, key=lambda d: d['reviewer'] / max(commit_cnt(d) - d["author"], 1))
    for i in range(1, 1 + args.top):
        d = stats[-i]
        pr_stat(i, d, d['reviewer'] / max(commit_cnt(d) - d["author"], 1))
    print()

    print("Top reviewer pct (reviewed patches only, not counting authored by self)")
    stats = sorted(stats, key=lambda d: of_reviewed_pct(d))
    for i in range(1, 1 + args.top):
        d = stats[-i]
        pr_stat(i, d, of_reviewed_pct(d))
    print()

    pr_header()
    print("Top review pct")
    stats = sorted(stats, key=lambda d: d['reviewer'] / commit_cnt(d))
    for i in range(1, 1 + args.top):
        pr_stat(i, stats[-i])
    print()

    print("Top author pct")
    stats = sorted(stats, key=lambda d: d['author'] / commit_cnt(d))
    for i in range(1, 1 + args.top):
        pr_stat(i, stats[-i])
    print()

    pr_header()
    print("Top review abs")
    stats = sorted(stats, key=lambda d: d['reviewer'])
    for i in range(1, 1 + args.top):
        pr_stat(i, stats[-i])
    print()

    print("Top author abs")
    stats = sorted(stats, key=lambda d: d['author'])
    for i in range(1, 1 + args.top):
        pr_stat(i, stats[-i])
    print()


if __name__ == "__main__":
    main()
