#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime
import json
import subprocess
import os
import pprint
import re


args = None


def git(cmd):
    if isinstance(cmd, str):
        cmd = [cmd]
    p = subprocess.run(['git'] + cmd, cwd=args.linux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        print(p.stderr.decode('utf-8'))
        p.check_returncode()
    return p.stdout.decode('utf-8', errors="ignore")


def get_author_history(mailmap):
    hist = {
        'mail': dict(),
        'name': dict(),
    }

    regex = re.compile(r'(.*) <(.*)>')

    author_history = git('log --encoding=utf-8 --reverse --format=format:%at;%an;%ae'.split(' '))
    lines = author_history.split('\n')
    for line in lines:
        data = line.split(";")
        date = datetime.datetime.fromtimestamp(int(data[0]))
        name = data[1]
        mail = data[2]

        for m in mailmap:
            if m[0] in name or m[0] in mail:
                full_name = m[1]
                match = regex.match(full_name)
                name = match.group(0)
                mail = match.group(1)
                break

        # If it's one-sided alias try to use the old entry
        if name in hist['name']:
            if mail not in hist['mail']:
                hist['mail'][mail] = hist['name'][name]
        elif mail in hist['mail']:
            if name not in hist['name']:
                hist['name'][name] = hist['mail'][mail]
        # no hits, new entry
        else:
            hist['name'][name] = date
            hist['mail'][mail] = date

    return hist


def get_ages(names, author_history):
    ages = {}

    regex = re.compile(r'(.*) <(.*)>')
    for full_name in names:
        match = regex.match(full_name)
        if not match:
            continue

        name = match.group(1)
        mail = match.group(2)
        when = None
        if name in author_history['name']:
            when = author_history['name'][name]
        if mail in author_history['mail']:
            mail_when = author_history['mail'][mail]
            when = mail_when if when is None else min(when, mail_when)
        ages[full_name] = when

    return ages


def get_commit_cnt(log):
    cnt = 0
    for line in log:
        if line.startswith('commit '):
            cnt += 1
    return cnt


def get_review_cnt(log, maintainers):
    active = False
    cnt = 0
    sobs = 0
    reviewed = 0
    review_cnt = 0
    x_reviewed = 0
    x_company_review_cnt = 0
    author_domain = 'xyz'
    for line in log:
        if line.startswith('commit '):
            active = True
            cnt += 1
            review_cnt = 0
            x_company_review_cnt = 0
        if line.startswith('Author'):
            author_domain = line.split('@')[1]

        if line.find('Acked-by:') != -1 or line.find('Reviewed-by:') != -1:
            review_cnt += 1
            if author_domain not in line:
                x_company_review_cnt += 1

        if 'Signed-off-by:' in line:
            for m in maintainers:
                if m in line:
                    if active:
                        sobs += 1
                        if review_cnt:
                            reviewed += 1
                        if x_company_review_cnt:
                            x_reviewed += 1
                    active = False
                    break

    return {'commits': cnt, 'any': {'reviewed': reviewed, 'pct': round(reviewed * 100 / sobs, 2)},
            'x-company': {'reviewed': x_reviewed, 'pct': round(x_reviewed * 100 / sobs, 2)}}


def get_commit_stats(log, mailmap):
    authors = {}
    for line in log:
        if not line.startswith('Author: '):
            continue

        name = line[8:]

        for m in mailmap:
            if m[0] in name:
                name = m[1]
                break

        if name not in authors:
            authors[name] = 1
        else:
            authors[name] += 1
    return authors


def main():
    parser = argparse.ArgumentParser(description='Stats pretty printer')
    parser.add_argument('--linux', type=str, required=True, help="Path to the Linux kernel git tree")
    parser.add_argument('--start-commit', type=str, required=True,
                        help="First commit to consider, usually the (previous) merge commit of -next into downstream")
    parser.add_argument('--end-commit', type=str, default='',
                        help="Last commit to consider, usually empty or HEAD")
    parser.add_argument('--db', type=str, required=True)
    parser.add_argument('--maintainers', type=str, nargs='*', required=True,
                        help="Count only patches applied directly by given people")
    parser.add_argument('--json-out', dest='json_out', default='',
                        help="Instead of printing results add them into a JSON file")
    parser.add_argument('--no-ages', dest='ages', action='store_false', default=True,
                        help="Do not print member tenure stats")
    global args
    args = parser.parse_args()

    with open(args.db, 'r') as f:
        db = json.load(f)

    result = {}

    end_commit = args.end_commit
    if not end_commit:
        end_commit = git(['rev-parse', 'HEAD'])

    commits = git(['log', args.start_commit + '..' + args.end_commit, '--no-merges'] + \
                  ['--committer=' + x for x in args.maintainers]).split('\n')
    commits_ksft = git(['log', args.start_commit + '..' + args.end_commit, '--no-merges'] + \
                       ['--committer=' + x for x in args.maintainers] + \
                       ['--', 'tools/testing/selftests/']).split('\n')

    result['direct_commits'] = get_commit_cnt(commits)
    result['direct_test_commits'] = get_commit_cnt(commits_ksft)
    result['reviews'] = get_review_cnt(commits, args.maintainers)
    result['commit_authors'] = get_commit_stats(commits, db['mailmap'])
    result['test_commit_authors'] = get_commit_stats(commits_ksft, db['mailmap'])

    ages_str = {}
    if args.ages:
        author_history = get_author_history(db['mailmap'])
        ages = get_ages(result['commit_authors'], author_history)
        ages_str = {}
        for x, y in ages.items():
            if y:
                y = y.isoformat()
            ages_str[x] = y

    if args.json_out:
        if os.path.exists(args.json_out):
            with open(args.json_out, "r") as fp:
                data = json.load(fp)
        else:
            data = {}

        result["start_commit"] = args.start_commit
        result["end_commit"] = end_commit
        data["git"] = result
        data["ages"] |= ages_str

        with open(args.json_out, "w") as fp:
            json.dump(data, fp)
    else:
        pprint.pprint(result)


if __name__ == "__main__":
    main()
