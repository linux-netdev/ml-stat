#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime
import json
import math
from email.utils import parsedate_to_datetime


def main():
    parser = argparse.ArgumentParser(description='Resolve unknown identities using gitdm DB')
    parser.add_argument('--results', type=str, required=True, help="Result json (from ml-stat --json-out)")
    parser.add_argument('--gitdm', type=str, required=True,
                        help="Path to gitdm DB (from CNCF, src/alldevs.txt)")
    args = parser.parse_args()

    with open(args.results) as fp:
        results = json.load(fp)

    dm_map = {}
    dm_map_useless = {}
    with open(args.gitdm, errors='replace') as fp:
        lines = fp.readlines()
        for line in lines:
            data = line.split('\t')

            if data[0] in {'(Unknown)', 'Independent', "NotFound"}:
                tgt = dm_map_useless
            else:
                tgt = dm_map

            tgt[data[1]] = {
                "corp": data[0],
                "email": data[1],
                "name": data[2],
                "stat": data[3],
            }

    for someone, _ in results['corporate'].items():
        # We assume mapped addresses (company names) don't contain @
        if '@' not in someone:
            continue

        idx = someone.find('<')
        email = someone[idx+1:-1]
        dm_email = email.replace('@', '!')
        if idx > 0:
            name = someone[:idx-1]
        else:
            name = "noname"

        if dm_email in dm_map:
            corp = dm_map[dm_email]["corp"]
            print(f'["<{email}>", "{corp}"],')


if __name__ == "__main__":
    main()
