#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import json
import subprocess
import os
import pprint


args = None


def git(cmd):
    if isinstance(cmd, str):
        cmd = [cmd]
    p = subprocess.run(['git'] + cmd, cwd=args.linux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        print(p.stderr.decode('utf-8'))
        p.check_returncode()
    return p.stdout.decode('utf-8')


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


def main():
    parser = argparse.ArgumentParser(description='Stats pretty printer')
    parser.add_argument('--linux', type=str, required=True, help="Path to the Linux kernel git tree")
    parser.add_argument('--start-commit', type=str, required=True,
                        help="First commit to consider, usually the (previous) merge commit of -next into downstream")
    parser.add_argument('--end-commit', type=str, default='',
                        help="Last commit to consider, usually empty or HEAD")
    parser.add_argument('--maintainers', type=str, nargs='*', required=True,
                        help="Count only patches applied directly by given people")
    parser.add_argument('--json-out', dest='json_out', default='',
                        help="Instead of printing results add them into a JSON file")
    global args
    args = parser.parse_args()

    result = {}

    end_commit = args.end_commit
    if not end_commit:
        end_commit = git(['rev-parse', 'HEAD'])

    commits = git(['log', args.start_commit + '..' + args.end_commit, '--no-merges'] + \
                  ['--committer=' + x for x in args.maintainers]).split('\n')
    result['direct_commits'] = get_commit_cnt(commits)
    result['reviews'] = get_review_cnt(commits, args.maintainers)

    if args.json_out:
        if os.path.exists(args.json_out):
            with open(args.json_out, "r") as fp:
                data = json.load(fp)
        else:
            data = {}

        result["start_commit"] = args.start_commit
        result["end_commit"] = end_commit
        data["git"] = result

        with open(args.json_out, "w") as fp:
            json.dump(data, fp)
    else:
        pprint.pprint(result)


if __name__ == "__main__":
    main()
