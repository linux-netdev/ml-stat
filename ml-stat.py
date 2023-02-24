#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import email
import email.utils
import fcntl
import json
import filecmp
import os
import shutil
import subprocess
import sys
import termios

from email.policy import default


email_roots = dict()
email_grps = dict()
git_repo = ""


def getch():
    fd = sys.stdin.fileno()

    old_attr = termios.tcgetattr(fd)
    new_attr = termios.tcgetattr(fd)
    new_attr[3] = new_attr[3] & ~termios.ICANON & ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, new_attr)

    try:
        while True:
            try:
                c = sys.stdin.read(1)
                break
            except IOError:
                pass
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, old_attr)
    return c


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
                'syzbot <syzbot@syzkaller.appspotmail.com>',
                '<patchwork-bot+bluetooth@kernel.org>']:
        people_dict.pop(bot, 0)


def git(cmd):
    # print(' '.join(['git'] + cmd))
    with subprocess.Popen(['/usr/bin/git'] + cmd, cwd=git_repo,
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


def name_check_sort(sequences, mailmap, result):
    print("NOTE: press a - accept; r - rotate; i - ignore; s - strip names")
    for s in sequences:
        idents = list(s)
        # Try to pre-sort based on mail map
        targets = []
        weak_targets = []
        for ident in idents:
            for m in mailmap:
                if m[0] in ident:
                    print(f"ERROR: {ident} should have already been mapped!")
                if ident in m[0]:
                    print(f"WARN: {ident} would have matched {m[0]}!")
                if ident in m[1]:
                    targets.append(ident)
                else:
                    idx = ident.find('<')
                    name = ident[:idx].lower()
                    addr = ident[idx:].lower()
                    mt = m[1].lower()
                    if name in mt or addr in mt:
                        weak_targets.append(ident)
        if len(targets) == 0:
            targets += weak_targets
        if len(targets) > 1:
            print(f"ERROR: multiple map targets for {idents}!")
        elif len(targets) == 1:
            print(f"INFO: target identity {targets[0]} set based on existing entry!")
            idents.remove(targets[0])
            idents.append(targets[0])
        # Ask user for their preference
        done = False
        while not done:
            print("   ", idents)
            k = getch()
            if k == 'a':
                # Produce pairs, mapping everything onto the last entry
                for name in idents[:-1]:
                    result.append([name, idents[-1]])
                done = True
            elif k == 'r':
                if len(targets):
                    print(f"WARN: rotating when target was pre-mapped!")
                idents = [idents[-1]] + idents[:-1]
            elif k == 's':
                stripped = []
                seen = set()
                for name in idents[:-1]:
                    idx = name.find('<')
                    addr = name[idx:]
                    if addr not in seen:
                        stripped.append(addr)
                        seen.add(addr)
                stripped.append(idents[-1])
                idents = stripped
            elif k == 'i':
                print("Okay, skipping")
                done = True
            else:
                print("Unknown key:", k)
                print("a - accept; r - rotate; i - ignore; s - strip names")


def name_selfcheck(ppl_stat, mailmap):
    ident_collisions = {'kernel test robot '}
    names = dict()
    low_names = dict()
    emails = dict()
    no_names = set()

    # Create map of email -> set(identities)
    for p in ppl_stat:
        idx = p.find('<')
        if idx == -1:
            print("Invalid email/name:", p)
            continue

        addr = p[idx:].lower()
        if addr not in emails:
            emails[addr] = set()
        emails[addr].add(p)

        if idx == 0:
            no_names.add(addr)

    # Create map of name -> list(identities)
    for p in ppl_stat:
        idx = p.find('<')
        if idx == -1:
            print("Invalid email/name:", p)
            continue
        if p.lower() in no_names:
            continue

        name = p[:idx]
        # Some people have the same name, use the full addr for name
        if name in ident_collisions:
            name = p
        if name not in names:
            names[name] = []
        names[name].append(p)

        lname = name.lower()
        if lname not in low_names:
            low_names[lname] = []
        low_names[lname].append(p)

    #
    # Results, we got all the maps now
    #
    result = []
    print(f"emails: {len(emails)}  no-names: {len(no_names)}  persons: {len(names)}  persons.lower(): {len(low_names)}")
    if len(emails) != len(no_names) + len(names):
        print("WARN: more emails than identities")
    if len(names) != len(low_names):
        print("WARN: unmapped identities with different case")
    print()

    # Complain about emails with multiple identities
    bad_emails = []
    for addr in emails:
        if len(emails[addr]) > 1:
            bad_emails.append(emails[addr])
    if len(bad_emails) > 0:
        print("Emails with multiple identities")
        name_check_sort(bad_emails, mailmap, result)

    bad_names = []
    for n in names:
        if len(names[n]) > 1:
            bad_names.append(names[n])
    if len(bad_names) > 0:
        print("Names with multiple identities")
        name_check_sort(bad_names, mailmap, result)

    bad_names = dict()
    for n in names:
        n_lower = n.lower()
        if len(names[n]) != len(low_names[n_lower]):
            if n_lower not in bad_names:
                bad_names[n_lower] = []
            bad_names[n_lower] += names[n]
    if len(bad_names) > 0:
        print("Names which differ only on case")
        name_check_sort(bad_names.values(), mailmap, result)

    print()
    if len(result) > 0:
        print("Suggested mail map additions:")
    else:
        print("No new mail map entries found")
    for entry in result:
        a = entry[0].replace('"', '\\"')
        b = entry[1].replace('"', '\\"')
        print(f'\t[ "{a}", "{b}" ],')

    # Complain about email-only identities
    nn_list = [x for x in no_names]
    for p in nn_list:
        if len(emails[p]) > 1:
            no_names.remove(p)

    if len(no_names) > 0:
        print()
    for p in no_names:
        print(f'No name for {p}')


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
    parser.add_argument('--repo', dest='repo', default='netdev-2.git')
    # Development options
    parser.add_argument('--name-dump', dest='name_dump', action='store_true', default=False)
    parser.add_argument('--check', dest='check', action='store_true', default=False)
    parser.add_argument('--name', nargs='+', default=[])
    parser.add_argument('--proc', dest='proc', action='store_true', default=False)
    parser.add_argument('--dump-miss', dest='misses', action='store_true', default=False)
    parser.add_argument('--top-extra', type=int, required=False, default=0,
                        help="How many extra entries to add to the top n")
    args = parser.parse_args()

    global git_repo
    git_repo = args.repo

    with open(args.db, 'r') as f:
        db = json.load(f)

    stats = {
        'root': 0,
        'match': 0,
        'miss': 0,
        'skip-asel': 0,
    }
    misses = []

    prep_files('msg-files', git_repo, args.email_count)

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

    # Check the DBs only have two entries in the arrays
    for e in mailmap:
        if len(e) != 2:
            raise Exception("Entry must have 2 values: " + repr(e))
    for e in corpmap:
        if len(e) != 2:
            raise Exception("Entry must have 2 values: " + repr(e))

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
        name_selfcheck(ppl_stat, mailmap)
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
