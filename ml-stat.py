#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime
import email
import email.utils
import fcntl
import itertools
import json
import filecmp
import os
import shutil
import subprocess
import sys
import termios
import time
import random
import re

from email.policy import default
from email.utils import parsedate_to_datetime


args = None


class ParsingState:
    def __init__(self):
        self.email_roots = dict()
        self.email_grps = dict()
        self.threads = None  # set by parser
        self.change_sets = dict()
        self.cs_stat = None  # set by parser, change set stats

        # These will be replaced by EmailMsg() instances
        self.first_msg = dict()
        self.last_msg = dict()


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


class EmailPost:
    def __init__(self, subject):
        self._subject = subject

    def subject(self):
        return self._subject

    def _is_discussion(self):
        for tag in ['[syzbot] ', '[ANN]', 'Fw: [Bug ']:
            if tag in self._subject:
                return True
        return False

    def is_patch(self):
        return self._subject[0] == '[' and not self.is_pr() and not self._is_discussion()

    def is_pr(self):
        return self._subject.find('pull req') != -1

    def is_bugzilla_forward(self):
        return self._subject.find('Fw: [Bug ') != -1

    def is_discussion(self):
        subj = self._subject
        return (not self.is_pr() and (subj.find('[') == -1 and subj.find(']') == -1)) or \
               self._is_discussion()

    def is_unknown(self):
        return not self.is_patch() and not self.is_discussion() and not self.is_pr()

    def is_bad(self):
        return self.is_patch() + self.is_discussion() + self.is_pr() > 1


class EmailMsg(EmailPost):
    def __init__(self, msg):
        super().__init__(msg.get('subject'))

        self.msg = msg

        self._review = None
        self._accept = None

    def _is_review_tag(self):
        # TODO: also match RE?
        if not self.subject().startswith('Re: '):
            return False

        body = self.msg.get_body(preferencelist=('plain',))
        if body is None:
            return False
        try:
            body_str = body.as_string()
        except LookupError:
            return False

        lines = body_str.split()
        for l in lines:
            if l.startswith('Reviewed-') or l.startswith('Acked-'):
                return True
        return False

    def is_review_tag(self):
        if self._review is None:
            self._review = self._is_review_tag()
        return self._review

    def is_pwbot_accept(self):
        if self._accept is None:
            from_hdr = self.msg.get('From')
            self._accept = from_hdr.startswith('patchwork-bot+') and '@kernel.org' in from_hdr
        return self._accept

    def get(self, key):
        return self.msg.get(key)

    def get_all(self, key):
        return self.msg.get_all(key)

    def get_from_mapped(self, mappings):
        ret = []
        from_list = self.msg.get_all('from')
        for addr in from_list:
            b4_start = addr.find("via B4 Relay")
            if b4_start != -1:
                from_list = self.msg.get_all('X-Original-From')
                break

        for addr in from_list:
            if addr.find('<') < 0:
                addr = '<' + addr + '>'

            addr = addr.replace('"', "")

            for mapping in mappings:
                for m in mapping:
                    if addr.find(m[0]) >= 0:
                        addr = m[1]
                        break

            ret.append(addr)
        return ret


class EmailThread(EmailPost):
    def __init__(self, grp):
        super().__init__(grp['root'].get('subject'))

        self.grp = grp
        self.root = grp['root']
        self.msgs = []
        self.root_msg = None

        self.has_review_tags = False
        self.has_pwbot_accept = False

        is_patch = self.is_patch()

        for msg in self.grp['emails']:
            emsg = EmailMsg(msg)
            if msg is self.root:
                self.root_msg = emsg
            self.msgs.append(emsg)

            if is_patch and not self.has_review_tags:
                self.has_review_tags |= emsg.is_review_tag()
            if is_patch and not self.has_pwbot_accept:
                self.has_pwbot_accept |= emsg.is_pwbot_accept()

    def root(self):
        return self.grp['root']

    def root_subj(self):
        return self._subject

    def patch_count(self):
        cnt = 0
        for msg in self.msgs:
            subj = msg.subject()
            if subj[0] == '[' and ' 0/' not in subj:
                cnt += 1
        return cnt

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


class ChangeSet:
    def __init__(self, thr):
        self.threads = []
        self.has_pwbot_accept = False
        self.has_review_tags = False

        self.add_thread(thr)

    def add_thread(self, thr):
        self.threads.append(thr)
        if not self.has_review_tags:
            self.has_review_tags |= thr.has_review_tags
        if not self.has_pwbot_accept:
            self.has_pwbot_accept |= thr.has_pwbot_accept

    def participants(self, mapping):
        people = dict()
        for thr in self.threads:
            people |= thr.participants(mapping)
        return people

    def authors(self, mapping):
        people = dict()
        for thr in self.threads:
            people |= thr.authors(mapping)
        return people

    @staticmethod
    def get_key(subject):
        idx = subject.rfind(']')
        if idx == -1:
            print('ChangeSet subject has no ]:', subject)
            return subject
        return subject[idx + 1:]


def remove_bots(people_dict):
    for bot in ['<patchwork-bot+netdevbpf@kernel.org>',
                'kernel test robot <lkp@intel.com>',
                '<pr-tracker-bot@kernel.org>',
                'syzbot <syzbot@syzkaller.appspotmail.com>',
                '<bot+bpf-ci@kernel.org>',
                '<patchwork-bot+bluetooth@kernel.org>']:
        people_dict.pop(bot, 0)


# TODO: this is just parsedate_to_datetime from
def email_str_date(m):
    dt = m.get('date')
    parsed = email.utils.parsedate(dt)
    timed = time.mktime(parsed)
    dt = datetime.datetime.fromtimestamp(timed)
    return f"{dt:%Y-%m-%d}"


def git(tree, cmd, silent=None):
    if isinstance(cmd, str):
        cmd = [cmd]
    p = subprocess.run(['/usr/bin/git'] + cmd, cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        if not silent:
            print(p.stderr.decode('utf-8'))
        p.check_returncode()
    return p.stdout.decode('utf-8', errors='ignore')


def get_author_history(mailmap):
    hist = {
        'mail': dict(),
        'name': dict(),
    }

    regex = re.compile(r'(.*) <(.*)>')

    author_history = git(args.linux, 'log --encoding=utf-8 --reverse --format=format:%at;%an;%ae'.split(' '))
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


def refset_add(refs, msg, key):
    ref = msg.get_all(key)
    if not ref:
        return
    ref = [r.strip() for r in ref]
    if len(ref) == 1 and ref[0].count('<') > 1:
        ref = ref[0].split()
        ref = [r.strip() for r in ref]
        ref = filter(lambda r: len(r) and r[0] == '<' and r[-1] == '>', ref)
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


def checkout_files(file_dir, git_dir, files, until, start, n):
    print(f"Preparing files from {git_dir}, id range {start}..{n - 1}")

    git(git_dir, ['checkout', '-q', until])
    for i in range(start, n):
        if i in files:
            continue

        try:
            shutil.copy2(os.path.join(git_dir, 'm'), os.path.join(file_dir, str(i)))
        except FileNotFoundError:
            # Spam messages are apparently sometimes removed and called 'd' rather than 'm'
            shutil.copy2(os.path.join(git_dir, 'd'), os.path.join(file_dir, str(i)))

        if (i % 100) == 0:
            print(f"Checking out {i}/{n}", end='\r')

        git(git_dir, ['checkout', '-q', f'HEAD~'])

    git(git_dir, ['reset', '--hard', until])


def prep_files(file_dir, since, until, offset=0):
    # os.listdir
    # os.path import isfile, join

    if not os.path.isdir(file_dir):
        os.mkdir(file_dir)

    # Start with the oldest dir that exists
    git_dir = git_dir_idx = -1
    git_dir_old = None
    for i in reversed(range(9)):
        name = f'{args.repo}-{i}.git'
        if not os.path.isdir(name):
            continue

        # Check if the target rev exists in this repo
        try:
            git(name, ['rev-parse', until], silent=True)
        except subprocess.CalledProcessError:
            continue

        git_dir_idx = i
        git_dir = name
        if i > 0:
            git_dir_old = f'{args.repo}-{i - 1}.git'
        break
    if git_dir_idx == -1:
        print(f'git dir not found: {args.repo}-$i.git')
        sys.exit(1)

    files = set()
    for f in os.listdir(file_dir):
        if not os.path.isfile(os.path.join(file_dir, f)):
            continue
        if not f.isnumeric():
            continue
        files.add(int(f))

    try:
        n_old = 0
        n_new = int(git(git_dir, ['rev-list', '--count', f'{since}..{until}'], silent=True))
    except subprocess.CalledProcessError:
        # Probably spanning different repos, sigh
        try:
            git(git_dir_old, ['rev-parse', since])

            n_old = int(git(git_dir_old, ['rev-list', '--count', f'{since}..origin/master']))
            n_new = int(git(git_dir, ['rev-list', '--count', until])) - 1
        except subprocess.CalledProcessError:
            print(f"Can't find the hashes in ML repos")
            sys.exit(1)

    # Sanity check
    if len(files):
        id_to_check = min(files)
        git(git_dir, ['checkout', f'{until}~{id_to_check}'])
        ret = filecmp.cmp(os.path.join(file_dir, str(id_to_check)), os.path.join(git_dir, 'm'))
        if not ret:
            print(f'Files look stale id: {id_to_check}: {ret}')
            sys.exit(1)

    if n_old:
        checkout_files(file_dir, git_dir_old, files, 'origin/master', n_new, n_old + n_new)
    checkout_files(file_dir, git_dir, files, until, 0, n_new)

    return n_new + n_old


def name_check_sort_heuristics(idents):
    # One is <dude@email>, the other one is Dude <dude@email>
    if len(idents) == 2:
        if idents[1] in idents[0]:
            idents.reverse()
        if idents[0] in idents[1]:
            print(f"INFO: target identity set as a subset!")
            return

    # Sort anything that contains " first
    idents.sort(key=lambda v: -v.find('"'))


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
                    if (name and name in mt) or (addr and addr in mt):
                        weak_targets.append(ident)
        if len(targets) == 0:
            targets += weak_targets
        if len(targets) == 0:
            name_check_sort_heuristics(idents)
        uniq_targets = set(targets)
        if len(uniq_targets) > 1:
            print(f"ERROR: multiple map targets for {idents}!")
        elif len(uniq_targets) == 1:
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


def get_mail_map(db, corp):
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
    if corp:
        use_map.append(corpmap)
    return use_map


def parsed_interact(threads, db):
    indmap = get_mail_map(db, False)
    corpmap = get_mail_map(db, True)

    while True:
        mid = input("msgid (or quit, random): ")
        if mid == 'quit' or mid == 'q':
            return
        if mid == 'random' or mid == 'r':
            mid = random.choice(list(threads.keys()))
        if mid[0] != '<':
            mid = '<' + mid + '>'
        print("mid:", mid, "found:", mid in threads)
        if mid not in threads:
            continue

        thr = threads[mid]
        print(f"Link: https://lore.kernel.org/all/{mid[1:-1]}/#r")
        print(f"Subject: {thr.root_subj():.70}")
        for msg in thr.msgs:
            print(f'    {msg.get("From"):.30}  {msg.subject():.40}')
        print("Authors:", thr.authors(indmap))
        print("Participants:", thr.participants(indmap))
        print("C Authors:", thr.authors(corpmap))
        print("C Participants:", thr.participants(corpmap))
        print("ChangeSet key:", ChangeSet.get_key(thr.root_subj()))
        print()


def group_one_msg(ps, msg, stats, force_root=False):
    refs = set()
    refset_add(refs, msg, 'references')
    refset_add(refs, msg, 'in-reply-to')

    mid = msg.mid

    is_root = not refs or force_root

    if not is_root:
        for r in refs:
            if r in refs and r in ps.email_grps:
                grp = ps.email_grps[r]
                grp['emails'].append(msg)
                ps.email_grps[mid] = grp
                stats['match'] += 1
                return True

        subj = msg.get('subject')
        from_hdr = msg.get('from')
        if subj.startswith('Re: [syzbot]') and '@syzkaller.appspotmail.com>' in from_hdr:
            stats['syz-root'] += 1
            is_root = True

    if is_root:
        grp = {'root': msg, 'emails': [msg]}
        ps.email_roots[mid] = grp
        ps.email_grps[mid] = grp
        stats['root'] += 1
        return True

    return False


def load_threads(full_misses):
    ps = ParsingState()

    stats = {
        'root': 0,
        'match': 0,
        'miss': 0,
        'skip': 0,
        'skip-stable': 0,
        'syz-root': 0,
    }
    misses = []

    email_count = prep_files('msg-files', args.since, args.until)

    dated = False
    stable_mids = set()
    for i in reversed(range(email_count)):
        with open(f'msg-files/{i}', 'rb') as fp:
            msg = email.message_from_binary_file(fp, policy=default)

        # Attach pre-parsed attrs to the msg object
        msg.mid = msg.get('message-id').strip()
        msg.rid = msg.mid[1:-1]

        if not dated:
            ps.first_msg = msg
            print(msg.get('date'))
            dated = True

        if (email_count - i) % 100 == 0 or i == 0:
            print(email_count - i, end='\r')

        subj = msg.get('subject')
        if not subj:
            stats['skip'] += 1
            continue

        force_root = subj.startswith('Fw: [Bug')
        if subj.find('PATCH AUTOSEL') != -1 or msg.get('X-stable') == 'review':
            stable_mids.add(msg.mid)
            force_root |= True
            stats['skip-stable'] += 1

        if not group_one_msg(ps, msg, stats, force_root=force_root):
            misses.append(msg)
    if dated:
        ps.last_msg = msg

    # Re-try misses, apparently git-send-email sends out of order
    n_misses = 0
    while n_misses != len(misses):
        n_misses = len(misses)

        i = 0
        while i < len(misses):
            if group_one_msg(ps, misses[i], stats):
                del misses[i]
            else:
                i += 1

    stats['miss'] = len(misses)

    for k in stable_mids:
        ps.email_grps.pop(k, None)
        ps.email_roots.pop(k, None)

    threads = dict()
    for mid, grp in ps.email_roots.items():
        threads[mid] = EmailThread(grp)

    print('Missed thread grouping (no root):')
    l = []
    for m in misses:
        l.append((email_str_date(m), m.get('subject'), m.rid))
    if full_misses:
        l.sort()
    else:
        l = l[-10:]
        print(' ', '...')
    for line in l:
        print(line[0], f'{line[1]:.77}', 'https://patch.msgid.link/' + line[2], sep='\n   ')
    print()

    print('Unknown msg type:')
    for mid, thr in threads.items():
        if thr.is_unknown():
            print('  ' + thr.root_subj())

    print('Bad msg type:')
    for mid, thr in threads.items():
        if thr.is_bad():
            print('  ' + thr.root_subj())

    for thr in threads.values():
        if not thr.is_patch():
            continue

        cs_key = ChangeSet.get_key(thr.root_subj())
        if cs_key in ps.change_sets:
            ps.change_sets[cs_key].add_thread(thr)
        else:
            ps.change_sets[cs_key] = ChangeSet(thr)

    cs_stat = {}
    zero_len_cs = []
    for i in itertools.product(['s', 'm'], ['r', '-'], ['a', '-']):
        cs_stat[''.join(i)] = {'cnt': 0, 'max': 0, 'sum': 0, 'hist': {}}
    for cs in ps.change_sets.values():
        if cs.threads[0].patch_count() == 0:
            zero_len_cs.append(cs)
            continue

        mkey = ('m' if cs.threads[0].patch_count() > 1 else 's') + \
               ('r' if cs.has_review_tags else '-') + \
               ('a' if cs.has_pwbot_accept else '-')

        cs_stat[mkey]['cnt'] += 1
        post_cnt = len(cs.threads)
        cs_stat[mkey]['sum'] += post_cnt
        cs_stat[mkey]['max'] = max(cs_stat[mkey]['max'], post_cnt)

        while len(cs_stat[mkey]['hist']) < post_cnt:
            cs_stat[mkey]['hist'][len(cs_stat[mkey]['hist']) + 1] = 0
        cs_stat[mkey]['hist'][post_cnt] += 1
    if zero_len_cs:
        subjects = [cs.threads[0].subject() for cs in zero_len_cs]
        print('Zero-length change sets:\n ', '\n  '.join(subjects))
    ps.cs_stat = cs_stat
    print()

    print('Parsing done:')
    print(stats)
    print()

    ps.threads = threads
    return email_count, ps


def calc_ppl_stat(args, ps, db, corp):
    print("Calculating stats for ", "corps" if corp else "individuals", "...", sep='')

    threads = ps.threads
    ppl_stat = dict()
    use_map = get_mail_map(db, corp)

    for mid, thr in threads.items():
        authors = thr.authors(use_map)
        parti = thr.participants(use_map)
        for p in parti:
            if p not in ppl_stat:
                ppl_stat[p] = {'author': {'cs': 0, 'thr': 0, 'msg': 0},
                               'reviewer': {'cs': 0, 'thr': 0, 'msg': 0}}
            if p in authors:
                ppl_stat[p]['author']['thr'] += 1
                ppl_stat[p]['author']['msg'] += authors[p]
            else:
                ppl_stat[p]['reviewer']['thr'] += 1
                ppl_stat[p]['reviewer']['msg'] += parti[p]

    for cs in ps.change_sets.values():
        authors = cs.authors(use_map)
        parti = cs.participants(use_map)

        for p in parti:
            if p in authors:
                ppl_stat[p]['author']['cs'] += 1
            else:
                ppl_stat[p]['reviewer']['cs'] += 1

    for p in ppl_stat.keys():
        score = 0
        score += 2 * ppl_stat[p]['reviewer']['cs']
        score += 8 * ppl_stat[p]['reviewer']['thr']
        score += 2 * (ppl_stat[p]['reviewer']['msg'] - 1)
        score -= 4 * ppl_stat[p]['author']['msg']
        ppl_stat[p]['score'] = {'positive': score, 'negative': -score}

    if args.proc:
        pass
    elif args.interact:
        parsed_interact(threads, db)
    elif args.json_out:
        return ppl_stat
    elif args.check:
        name_selfcheck(ppl_stat, db['mailmap'])
    elif args.name_dump:
        print(f'Names ({len(ppl_stat)}):')
        print('  ' + '\n  '.join(sorted(ppl_stat.keys())))
    else:
        out_keys = [
            ('reviewer', 'cs', 10),
            ('reviewer', 'thr', 10),
            ('reviewer', 'msg', 10),
            ('author', 'cs', 15),
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
        return ppl_stat


def print_change_set_stat(ps):
    print("Change sets (m/s: patch vs series; r/-: review tag on list; a/-: pw-bot accept):")
    for mkey, v in ps.cs_stat.items():
        print('  ', mkey, v, 'avg revisions:', v['sum'] / v['cnt'])
    print()


def main():
    parser = argparse.ArgumentParser(description='Mailing list stats')
    parser.add_argument('--until', type=str, default='master',
                        help="ref of the newest message in the mailing list repo")
    parser.add_argument('--since', type=str, required=True,
                        help="ref of the oldest message in the mailing list repo")
    parser.add_argument('--linux', type=str, required=True, help="Path to the Linux kernel git tree")
    parser.add_argument('--no-individual', dest='individual', action='store_false', default=True,
                        help="Do not print the stats by individual people")
    parser.add_argument('--no-corp', dest='corp', action='store_false', default=True,
                        help="Do not print the stats by company")
    parser.add_argument('--no-ages', dest='ages', action='store_false', default=True,
                        help="Do not print member tenure stats")
    parser.add_argument('--db', type=str, required=True)
    parser.add_argument('--repo', dest='repo', default='netdev',
                        help="Name of the lore archive (without the number and .git suffix)")
    parser.add_argument('--json-out', dest='json_out', default='',
                        help="Instead of printing results dump them into a JSON file")
    # Development options
    parser.add_argument('--name-dump', dest='name_dump', action='store_true', default=False)
    parser.add_argument('--check', dest='check', action='store_true', default=False)
    parser.add_argument('--name', nargs='+', default=[])
    parser.add_argument('--proc', dest='proc', action='store_true', default=False)
    parser.add_argument('--interact', dest='interact', action='store_true', default=False)
    parser.add_argument('--dump-miss', dest='misses', action='store_true', default=False)
    parser.add_argument('--top-extra', type=int, required=False, default=0,
                        help="How many extra entries to add to the top n")
    global args
    args = parser.parse_args()

    if args.json_out and not (args.individual and args.corp and args.ages):
        parser.error("--json-out requires both crop and individual stats")

    with open(args.db, 'r') as f:
        db = json.load(f)

    ages_str = ind_out = corp_out = None
    parsed = dict()
    if args.individual or args.corp or args.check:
        email_count, parsed = load_threads(args.misses)
    if args.individual:
        ind_out = calc_ppl_stat(args, parsed, db, corp=False)
    if args.individual and args.ages and not args.check:
        print("Calculating author ages from git...")
        author_history = get_author_history(db['mailmap'])
        ages = get_ages(ind_out.keys(), author_history)
        ages_str = {}
        for x, y in ages.items():
            if y:
                y = y.isoformat()
            ages_str[x] = y
    if args.corp and not args.check:
        corp_out = calc_ppl_stat(args, parsed, db, corp=True)

    if args.json_out:
        if os.path.exists(args.json_out):
            with open(args.json_out, "r") as fp:
                data = json.load(fp)
        else:
            data = {}

        data |= {
            "count": email_count,

            "first_date": parsed.first_msg.get('date'),
            "last_date": parsed.last_msg.get('date'),
            "first_msg_id": parsed.first_msg.mid,
            "last_msg_id": parsed.last_msg.mid,

            "change-sets": parsed.cs_stat,

            "ages": ages_str,
            "individual": ind_out,
            "corporate": corp_out,
        }

        with open(args.json_out, "w") as fp:
            json.dump(data, fp)
    elif parsed:
        print_change_set_stat(parsed)

if __name__ == "__main__":
    main()
