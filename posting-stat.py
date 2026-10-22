#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

# Posting efficiency - how many patch postings a developer needs per merged
# commit. Run after ml-stat.py, it reuses the msg-files/ cache ml-stat left
# behind and adds its results to the same JSON.
#
# We deliberately ignore the patchwork bot here and look for the patches in
# the git tree instead. Plenty of netdev patches get merged via other trees
# (bpf, wireless, netfilter...) which never produces a bot reply.
#
# While at it count the replies from the review and testing bots, per
# author of the patch they replied to.

import argparse
import collections
import datetime
import email
import email.utils
import json
import os
import re
import subprocess
import sys

from email.policy import default


# Projects which post to the list but never land in the kernel tree
NOT_KERNEL = ['iproute2', 'ethtool']

# Tagged like a patch, but isn't one (same list as ml-stat.py)
NOT_A_PATCH = ['[syzbot] ', '[ANN]', 'Fw: [Bug ']

BOTS = ['patchwork-bot+', 'lkp@intel.com', 'pr-tracker-bot@kernel.org',
        'syzbot@syzkaller.appspotmail.com', 'bot+bpf-ci@kernel.org']

# Bots whose replies tell us something about the patch. Patchwork and
# the pr-tracker only report that something got merged.
REPLY_BOTS = ['netdev-bot+sashiko@kernel.org', 'netdev-bot+sinfo@kernel.org',
              'bot+bpf-ci@kernel.org', 'lkp@intel.com',
              'syzbot@syzkaller.appspotmail.com']

# Maintainers link the posting they applied, both forms are in use
MSGID_RE = re.compile(r'(?:patch\.msgid\.link/|lore\.kernel\.org/r/|'
                      r'Message-I[dD]:\s*<)([^\s>]+)')
COVER_RE = re.compile(r'\b0+/\d')


def git(tree, cmd):
    p = subprocess.run(['/usr/bin/git'] + cmd, cwd=tree,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        print(p.stderr.decode('utf-8'))
        p.check_returncode()
    return p.stdout.decode('utf-8', errors='ignore')


def get_mail_map(db, corp):
    mailmap = db['mailmap']
    corpmap = db['corpmap']

    for m in mailmap:
        for c in corpmap:
            if c[0] in m[1]:
                corpmap.append((m[0], c[1],))
                break

    use_map = [mailmap]
    if corp:
        use_map.append(corpmap)
    return use_map


def get_from_mapped(msg, mappings):
    ret = []
    from_list = msg.get_all('from')
    if not from_list:
        return ret
    for addr in from_list:
        if 'via B4 Relay' in addr:
            from_list = msg.get_all('X-Original-From') or from_list
            break

    for addr in from_list:
        if '<' not in addr:
            addr = '<' + addr + '>'

        addr = addr.replace('"', "")

        for mapping in mappings:
            for m in mapping:
                if m[0] in addr:
                    addr = m[1]
                    break

        ret.append(addr)
    return ret


def is_kernel_project(tag):
    for word in re.split(r'[\s,]+', tag.lower()):
        if word.removesuffix('-next') in NOT_KERNEL:
            return False
    return True


def bot_address(msg):
    """Address of the sender if it's one of REPLY_BOTS, None otherwise"""
    _, addr = email.utils.parseaddr(str(msg.get('from') or ''))
    # syzbot sends from a separate address for every bug
    addr = re.sub(r'^syzbot\+[^@]*@', 'syzbot@', addr.lower())
    return addr if addr in REPLY_BOTS else None


def load_postings(msg_dir, count, maps):
    """Every patch posting in the window, cover letters kept separately,
    and the bot replies, as (bot, message-id it replied to)"""
    postings = []
    covers = {}
    replies = []
    skipped = collections.Counter()

    for i in range(count):
        try:
            with open(os.path.join(msg_dir, str(i)), 'rb') as fp:
                msg = email.message_from_binary_file(fp, policy=default)
        except FileNotFoundError:
            print(f'{msg_dir}/{i} missing, run ml-stat.py first')
            sys.exit(1)

        subj = msg.get('subject')
        mid = msg.get('message-id')
        if not subj or not mid:
            continue
        subj = ' '.join(subj.split())

        bot = bot_address(msg)
        irt = (msg.get('in-reply-to') or '').split()
        if bot and irt:
            replies.append((bot, irt[0].strip('<>')))
            continue

        # Skip replies, and anything which isn't tagged like a patch
        if subj[:4].lower() == 're: ' or not subj.startswith('['):
            continue
        end = subj.rfind(']')
        if end == -1 or 'pull req' in subj or 'pull-req' in subj:
            continue
        if any(t in subj for t in NOT_A_PATCH):
            continue
        frm = msg.get('from') or ''
        if any(b in frm for b in BOTS):
            skipped['bot'] += 1
            continue
        tag = subj[1:subj.find(']')]
        # Stable backports are re-postings of merged work, not new development
        if 'AUTOSEL' in tag or msg.get('X-stable') == 'review':
            skipped['stable'] += 1
            continue
        if not is_kernel_project(tag):
            skipped['not-kernel'] += 1
            continue

        mid = mid.strip().strip('<>')
        people = [get_from_mapped(msg, m) for m in maps]
        # Bots do reply to cover letters, the author is all we need there
        if COVER_RE.search(tag):
            covers[mid] = people
            continue

        postings.append({
            'mid': mid,
            'subject': subj[end + 1:].strip(),
            'people': people,
        })

        if (i % 500) == 0:
            print(f'Reading messages {i}/{count}', end='\r')

    print(f'Patch postings: {len(postings)}  skipped: {dict(skipped)}')
    return postings, covers, replies


def index_tree(linux, ref, since):
    """Map both message-id and subject onto the commit which carries them"""
    log = git(linux, ['log', '--no-merges', f'--since={since}', ref,
                      '--format=%H%x01%s%x01%b%x02'])
    by_mid = {}
    by_subject = {}
    commits = 0
    for record in log.split('\x02'):
        if not record.strip():
            continue
        fields = record.strip().split('\x01')
        commit = fields[0]
        subject = fields[1].strip() if len(fields) > 1 else ''
        body = fields[2] if len(fields) > 2 else ''
        commits += 1

        # First commit wins, a later one with the same subject is a different
        # patch which happens to be named the same
        by_subject.setdefault(subject, commit)
        for mid in MSGID_RE.findall(body):
            by_mid.setdefault(mid.strip().rstrip('>'), commit)

    print(f'Commits in {ref} since {since}: {commits} '
          f'({len(by_mid)} with a message-id link)')
    return by_mid, by_subject


def match_postings(postings, by_mid, by_subject, n_maps):
    """Count postings and the distinct commits they resulted in"""
    stats = [collections.defaultdict(lambda: {'postings': 0, 'commits': set(),
                                              'bot-replies': collections.Counter()})
             for _ in range(n_maps)]
    matched = collections.Counter()
    all_commits = set()

    for posting in postings:
        commit = by_mid.get(posting['mid'])
        if commit:
            matched['msgid'] += 1
        else:
            # Earlier revisions carry no link, but keep the same subject
            commit = by_subject.get(posting['subject'])
            matched['subject' if commit else 'unmatched'] += 1
        if commit:
            all_commits.add(commit)

        for i, people in enumerate(posting['people']):
            for person in people:
                stats[i][person]['postings'] += 1
                if commit:
                    stats[i][person]['commits'].add(commit)

    total = len(postings)
    for key, caption in (('msgid', 'matched by msgid  '),
                         ('subject', 'matched by subject'),
                         ('unmatched', 'not in the tree   ')):
        pct = f' ({100 * matched[key] / total:.1f}%)' if total else ''
        print(f'  {caption}: {matched[key]:6}{pct}')

    # Totals don't depend on how the people were mapped, so there is
    # only one summary, no matter how many maps we counted for.
    # Counting only the postings we did find gives a number comparable
    # to the average revision count of accepted change sets, the two are
    # derived completely separately. The gap to per_commit is the traffic
    # which never made it in.
    merged = matched['msgid'] + matched['subject']
    commits = len(all_commits)
    summary = {'postings': total, 'postings_merged': merged, 'commits': commits,
               'per_commit': round(total / commits, 4) if commits else 0,
               'per_commit_merged': round(merged / commits, 4) if commits else 0}
    return stats, summary


def match_bot_replies(postings, covers, replies, stats):
    """Credit bot replies to the author of the patch they replied to"""
    people = dict(covers)
    for posting in postings:
        people[posting['mid']] = posting['people']

    summary = collections.Counter()
    unmatched = 0
    for bot, mid in replies:
        # Replies to replies, or to postings outside of the window
        if mid not in people:
            unmatched += 1
            continue
        summary[bot] += 1
        for i, mapped in enumerate(people[mid]):
            for person in mapped:
                stats[i][person]['bot-replies'][bot] += 1

    print(f'Bot replies: {dict(summary)}  unmatched: {unmatched}')
    return dict(summary)


def apply_stats(results, key, stats):
    """Fold the counts into the people already in the JSON"""
    unknown = 0
    for person, counts in stats.items():
        if person not in results[key]:
            unknown += 1
            continue
        results[key][person]['author']['postings'] = counts['postings']
        results[key][person]['author']['commits'] = len(counts['commits'])
        results[key][person]['author']['bot-replies'] = dict(counts['bot-replies'])

    # Everyone else posted nothing, be explicit so consumers can rely on it
    for person in results[key]:
        results[key][person]['author'].setdefault('postings', 0)
        results[key][person]['author'].setdefault('commits', 0)
        results[key][person]['author'].setdefault('bot-replies', {})

    if unknown:
        print(f'  {key}: {unknown} posters not in the ml-stat results')


def main():
    parser = argparse.ArgumentParser(description='Patch posting efficiency')
    parser.add_argument('--results', type=str, required=True,
                        help="Result json (from ml-stat --json-out)")
    parser.add_argument('--db', type=str, required=True)
    parser.add_argument('--linux', type=str, required=True,
                        help="Path to the Linux kernel git tree")
    parser.add_argument('--tree-ref', dest='tree_ref', default='linux-next/master',
                        help="Ref to look for the patches in")
    parser.add_argument('--since', type=str, default=None,
                        help="How far back to index the tree "
                             "(default: first message in the results)")
    parser.add_argument('--msg-files', dest='msg_files', default='msg-files',
                        help="Directory with the messages ml-stat.py checked out")
    args = parser.parse_args()

    with open(args.results) as fp:
        results = json.load(fp)
    with open(args.db) as fp:
        db = json.load(fp)

    since = args.since
    if not since:
        # Patches can only be merged after they were posted
        first = email.utils.parsedate_to_datetime(results['first_date'])
        since = str((first - datetime.timedelta(days=7)).date())

    # Same order as the stats in the JSON. One call, get_mail_map() extends
    # the corp map in place and would do it twice over.
    keys = ['individual', 'corporate']
    corp_map = get_mail_map(db, corp=True)
    maps = [corp_map[:1], corp_map]

    postings, covers, replies = load_postings(args.msg_files, results['count'],
                                              maps)
    by_mid, by_subject = index_tree(args.linux, args.tree_ref, since)
    stats, summary = match_postings(postings, by_mid, by_subject, len(keys))
    bot_summary = match_bot_replies(postings, covers, replies, stats)

    for i, key in enumerate(keys):
        apply_stats(results, key, stats[i])
    results['posting-efficiency'] = summary
    results['bot-replies'] = bot_summary
    print('Posting efficiency:', summary['per_commit'],
          'postings per merged commit')

    with open(args.results, 'w') as fp:
        json.dump(results, fp)


if __name__ == "__main__":
    main()
