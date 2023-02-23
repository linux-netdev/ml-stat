#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0

import argparse
import email
import email.utils
import json
import filecmp
import os
import shutil
import subprocess
import sys

from email.policy import default


email_roots = dict()
email_grps = dict()


class EmailMsg:
    def __init__(self, msg):
        self.msg = msg

    def subject(self):
        return self.msg.get('subject')

    def is_patch(self):
        return self.subject()[0] == '[' and not self.is_pr()

    def is_pr(self):
        return self.subject().find('pull req') != -1

    def is_bugzilla_forward(self):
        subj = self.subject()
        return subj.find('Fw: [Bug ') != -1

    def is_discussion(self):
        subj = self.subject()
        return (not self.is_pr() and (subj.find('[') == -1 and subj.find(']') == -1)) or self.is_bugzilla_forward()

    def is_unknown(self):
        return not self.is_patch() and not self.is_discussion() and not self.is_pr()

    def is_bad(self):
        return self.is_patch() + self.is_discussion() + self.is_pr() > 1

    def get(self, key):
        return self.msg.get(key)

    def get_all(self, key):
        return self.msg.get_all(key)

    def get_from_mapped(self, mappings):
        ret = []
        for addr in self.msg.get_all('from'):
            if addr.find('<') < 0:
                addr = '<' + addr + '>'

            for mapping in mappings:
                for m in mapping:
                    if addr.find(m[0]) >= 0:
                        addr = m[1]
                        break

            ret.append(addr)
        return ret


class EmailThread:
    def __init__(self, grp):
        self.grp = grp
        self.root = grp['root']
        self.msgs = []
        self.root_msg = None

        for msg in self.grp['emails']:
            emsg = EmailMsg(msg)
            if msg is self.root:
                self.root_msg = emsg
            self.msgs.append(emsg)

    def root(self):
        return self.grp['root']

    def root_subj(self):
        return self.grp['root'].get('subject')

    def is_patch(self):
        return self.root_subj()[0] == '[' and not self.is_pr()

    def is_pr(self):
        return self.root_subj().find('pull req') != -1

    def is_bugzilla_forward(self):
        subj = self.root_subj()
        return subj.find('Fw: [Bug ') != -1

    def is_discussion(self):
        subj = self.root_subj()
        return (not self.is_pr() and (subj.find('[') == -1 and subj.find(']') == -1)) or self.is_bugzilla_forward()

    def is_unknown(self):
        return not self.is_patch() and not self.is_discussion() and not self.is_pr()

    def is_bad(self):
        return self.is_patch() + self.is_discussion() + self.is_pr() > 1

    def participants(self, mapping):
        people = dict()
        for msg in self.msgs:
            for person in msg.get_from_mapped(mapping):
                if person not in people:
                    people[person] = 0
                people[person] += 1
        remove_bots(people)
        return people

    def authors(self, mapping):
        people = dict()
        for msg in self.msgs:
            if msg is self.root_msg or msg.is_pr() or msg.is_patch():
                for person in msg.get_from_mapped(mapping):
                    if person not in people:
                        people[person] = 0
                    people[person] += 1
        remove_bots(people)
        return people


def remove_bots(people_dict):
    for bot in ['<patchwork-bot+netdevbpf@kernel.org>',
                'kernel test robot <lkp@intel.com>',
                '<pr-tracker-bot@kernel.org>',
                '<patchwork-bot+bluetooth@kernel.org>']:
        people_dict.pop(bot, 0)


def git(cmd):
    # print(' '.join(['git'] + cmd))
    with subprocess.Popen(['/usr/bin/git'] + cmd, cwd='netdev-2.git',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
        p.wait()
        # print(p.stdout.read().decode('utf-8'))
        # print(p.stderr.read().decode('utf-8'))
        # print(p.returncode)


def refset_add(refs, msg, key):
    ref = msg.get_all(key)
    if not ref:
        return
    refs.update(set(ref))


def print_top(ppl_stat, key, subkey, n):
    print(f'Top {n} {key}s ({subkey}):')
    ppl = sorted(ppl_stat.keys(), key=lambda x: ppl_stat[x][key][subkey])
    for i in range(1, n + 1):
        p = ppl[-i]
        print(f"  {i:2}. [{ppl_stat[p][key][subkey]:3}] {p}")
    print()


def print_one(ppl_stat, key, subkey, p):
    if p not in ppl_stat:
        return
    ppl = sorted(ppl_stat.keys(), key=lambda x: ppl_stat[x][key][subkey])
    i = ppl.index(p)

    print(f"{key} ({subkey}):")
    print(f"  #{i:2}. [{ppl_stat[p][key][subkey]:3}] {p}")


def prep_files(file_dir, git_dir, n):
    # os.listdir
    # os.path import isfile, join

    if not os.path.isdir(file_dir):
        os.mkdir(file_dir)

    files = set()
    for f in os.listdir(file_dir):
        if not os.path.isfile(os.path.join(file_dir, f)):
            continue
        if not f.isnumeric():
            continue
        files.add(int(f))

    # Sanity check
    if len(files):
        id_to_check = min(files)
        git(['checkout', f'master~{id_to_check}'])
        ret = filecmp.cmp(os.path.join(file_dir, str(id_to_check)), os.path.join(git_dir, 'm'))
        if not ret:
            print(f'Files look stale id: {id_to_check}: {ret}')
            sys.exit(1)

    for i in range(n):
        if i in files:
            continue

        git(['checkout', f'master~{i}'])
        shutil.copy2(os.path.join(git_dir, 'm'), os.path.join(file_dir, str(i)))

        if (i % 100) == 0:
            print(f"Checking out {i}/{n}", end='\r')

    git(['reset', '--hard', 'master'])


def name_selfcheck(ppl_stat):
    names = dict()
    pre_mapped = set()
    no_names = set()

    for p in ppl_stat:
        if p.find('<') > 0:
            continue

        plow = p.lower()
        for p2 in ppl_stat:
            if plow in p2.lower() and p != p2:
                idx = p2.find('<')
                if idx < 2:
                    continue

                name = p2[:idx]
                if name not in names:
                    names[name] = []
                names[name].append(p)
                print(f'Mapped no-name {p} to {p2}')
                pre_mapped.add(p)
                break
        else:
            # Print this later to keep the output in neat sections
            no_names.add(p)
    if len(pre_mapped) > 0:
        print()

    for p in ppl_stat:
        idx = p.find('<')
        if idx == -1:
            print("Invalid email/name:", p)
            continue
        if p in pre_mapped or p in no_names:
            continue

        name = p[:idx]
        if name not in names:
            names[name] = []
        names[name].append(p)

    for p in no_names:
        print(f'No name for {p}')
    if len(pre_mapped) > 0:
        print()

    header_printed = False
    for n in names:
        if len(names[n]) > 1:
            if not header_printed:
                print("Suggested mail map additions:")
                header_printed = True
            print(f'\t[ "{", ".join(names[n])}" ],')
    if not header_printed:
        print("No new mail map entries found")


def group_one_msg(msg, stats, force_root=False):
        refs = set()
        refset_add(refs, msg, 'references')
        refset_add(refs, msg, 'in-reply-to')

        mid = msg.get('message-id')

        if not refs or force_root:
            grp = {'root': msg, 'emails': [msg]}
            email_roots[mid] = grp
            email_grps[mid] = grp
            stats['root'] += 1
        else:
            for r in refs:
                if r in refs and r in email_grps:
                    grp = email_grps[r]
                    grp['emails'].append(msg)
                    email_grps[mid] = grp
                    stats['match'] += 1
                    return True
            else:
                return False
        return True


def main():
    parser = argparse.ArgumentParser(description='Mailing list stats')
    parser.add_argument('--corp', dest='corp', action='store_true', default=False,
                        help="Print the stats by company rather than by person")
    parser.add_argument('--db', type=str, required=True)
    parser.add_argument('--email-count', type=int, required=True,
                        help="How many emails to look back into the archive")
    # Development options
    parser.add_argument('--name-dump', dest='name_dump', action='store_true', default=False)
    parser.add_argument('--check', dest='check', action='store_true', default=False)
    parser.add_argument('--name', nargs='+', default=[])
    parser.add_argument('--proc', dest='proc', action='store_true', default=False)
    parser.add_argument('--dump-miss', dest='misses', action='store_true', default=False)
    parser.add_argument('--top-extra', type=int, required=False, default=0,
                        help="How many extra entries to add to the top n")
    args = parser.parse_args()

    with open(args.db, 'r') as f:
        db = json.load(f)

    stats = {
        'root': 0,
        'match': 0,
        'miss': 0,
        'skip-asel': 0,
    }
    misses = []

    prep_files('msg-files', 'netdev-2.git', args.email_count)

    dated = False
    for i in reversed(range(args.email_count)):
        with open(f'msg-files/{i}', 'rb') as fp:
            msg = email.message_from_binary_file(fp, policy=default)

        if not dated:
            print(msg.get('date'))
            dated = True

        if (i % 100) == 0:
            print(args.email_count - i, end='\r')

        subj = msg.get('subject')
        if not subj or subj.find('PATCH AUTOSEL') != -1:
            stats['skip-asel'] += 1
            continue

        force_root = subj.startswith('Fw: [Bug')

        if not group_one_msg(msg, stats, force_root=force_root):
            misses.append(msg)

    # Re-try misses, apparently git-send-email sends out of order
    n_misses = 0
    while n_misses != len(misses):
        n_misses = len(misses)

        i = 0
        while i < len(misses):
            if group_one_msg(misses[i], stats):
                del misses[i]
            else:
                i += 1

    stats['miss'] = len(misses)

    threads = dict()
    for mid, grp in email_roots.items():
        threads[mid] = EmailThread(grp)

    print('Unknown:')
    for mid, thr in threads.items():
        if thr.is_unknown():
            print('  ' + thr.root_subj())

    print('Bad:')
    for mid, thr in threads.items():
        if thr.is_bad():
            print('  ' + thr.root_subj())

    mailmap = db['mailmap']
    corpmap = db['corpmap']

    for m in mailmap:
        for c in corpmap:
            if m[1].find(c[0]) != -1:
                corpmap.append((m[0], c[1],))
                break

    use_map = [mailmap]
    if args.corp:
        use_map.append(corpmap)

    ppl_stat = dict()
    for mid, thr in threads.items():
        authors = thr.authors(use_map)
        parti = thr.participants(use_map)
        for p in parti:
            if p not in ppl_stat:
                ppl_stat[p] = {'author': {'thr': 0, 'msg': 0},
                               'reviewer': {'thr': 0, 'msg': 0}}
            if p in authors:
                ppl_stat[p]['author']['thr'] += 1
                ppl_stat[p]['author']['msg'] += authors[p]
            else:
                ppl_stat[p]['reviewer']['thr'] += 1
                ppl_stat[p]['reviewer']['msg'] += parti[p]

    for p in ppl_stat.keys():
        score = 10 * ppl_stat[p]['reviewer']['thr'] + 2 * (ppl_stat[p]['reviewer']['msg'] - 1) \
                - 3 * ppl_stat[p]['author']['thr'] - (ppl_stat[p]['author']['msg'] // 2)
        ppl_stat[p]['score'] = {'positive': score, 'negative': -score}

    print(stats)
    print()

    if args.proc:
        pass
    elif args.misses:
        l = []
        for m in misses:
            l.append(m.get('subject') + '\t' + m.get('date') + '\t' + m.get('message-id'))
        for m in sorted(l):
            print(m)
    elif args.check:
        name_selfcheck(ppl_stat)
    elif args.name_dump:
        print(f'Names ({len(ppl_stat)}):')
        print('  ' + '\n  '.join(sorted(ppl_stat.keys())))
    else:
        out_keys = [
            ('reviewer', 'thr', 10),
            ('reviewer', 'msg', 10),
            ('author', 'thr', 15),
            ('author', 'msg', 10),
            ('score', 'positive', 15),
            ('score', 'negative', 15)
        ]

        if args.name:
            for name in args.name:
                for ok in out_keys:
                    print_one(ppl_stat, ok[0], ok[1], name)
        else:
            for ok in out_keys:
                print_top(ppl_stat, ok[0], ok[1], ok[2] + args.top_extra)


if __name__ == "__main__":
    main()
