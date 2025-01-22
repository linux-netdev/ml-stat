Mailing list stats generator
============================

Scripts which uses a mail archive (in git format) to calculate development
stats.

It is review / participation focused, unlike other stats which use
the information in the git history.

Use
---

The script requires a clone of the mail archive::

  git clone https://lore.kernel.org/netdev/2/ netdev-2.git

For efficiency it makes a copy of the email messages under the `msg-files`
directory. ``--since`` is the git ref of the commit in the mailing list
repo before the commit with the first message (usually commit with PR
for the previous release), ``--until`` is the last message to use
(PR for the current release).

Example use::

  ./ml-stat.py --db ./db.json --since bb545da872c8 --until 0109af6d7037 \
		--repo netdev --linux ../linux --json-out netdev-6.4.json

Before generating the stats it's recommended to run the self checks,
to make sure that the email mailmap and parsing are okay.

git stats
---------

Some statistics are much quicker to get from git. They may go away
over time but for now ``git-stat.py`` loads them.

Example use::

    ./git-stat.py --db ./db.json --linux ../linux/ \
        --start-commit 7e68dd7d07a --end-commit 5b7c4cabbb6 \
	--json-out netdev-6.4.json \
	--maintainers davem@davemloft.net \
	              edumazet@google.com \
		      kuba@kernel.org \
		      pabeni@redhat.com \
		      anthony.l.nguyen@intel.com

Release to release comparisons and printing
-------------------------------------------

``ml-stat.py`` and ``git-stat.py`` can generate a full dump of
all statistics. The scripts update the JSON file if one already
exists (adding under their own keys, the outputs should not clash).

A separate script - ``stat-print.py`` can ingest two such
JSON files and pretty print the statistics of the second file
with annotations about how positions have changed.

Mail map db
-----------

The DB contains two sections, each section can contain any number
of A -> B mappings.

mailmap
~~~~~~~

`mailmap` allows for aliasing. First value should be just the email
address, second value is the target with a full name.

`corpmap` maps from parts of the email address to the company name.

`mailmap` is applied before the `cropmap`.

Sample email map is provided as `db.json.sample`.

Checks
------

Basic self-check::

  ./ml-stat.py --db db.json --since bb545da872c8 --until 0109af6d7037 \
               --repo netdev --linux ../linux --check

Use gitdm DB to map people to corp::

  ./corp-gitdm-resolve.py --results netdev-6.6.json \
                          --gitdm ../../gitdm-cncf/src/alldevs.txt

See domains with more than one addr which are not mapped to corpo::

   cat netdev-6.6.json | \
     jq -r '.corporate | with_entries(.value = .value.score.positive)' | \
     sed -n 's/.*@\(.*\)>": -*\(.*\),/\1 \2/p' | \
     sort | \
     datamash -t ' ' groupby 1 sum 2 count 2 | \
     awk '{if ($3 > 1) {print $2,$3,$1;}}' | \
     sort -n

See domains with a single addr which are not mapped to corpo::

   cat netdev-6.6.json | \
     jq -r '.corporate | with_entries(.value = .value.score.positive)' | \
     sed -n 's/.*@\(.*\)>": -*\(.*\),/\1 \2/p' | \
     sort | \
     datamash -t ' ' groupby 1 sum 2 count 2 | \
     awk '{if ($3 == 1) {print $2,$3,$1;}}' | \
     sort -n

See top unampped scorers from Gmail and kernel.org::

   cat netdev-6.6.json | \
      jq -r '.corporate | with_entries(.value = .value.score.positive)' | \
      sed -n 's/.*\(<.*@.*>\)": *\(.*\),/\1 \2/p' | \
      datamash -t ' ' groupby 1 sum 2 count 2 | \
      awk '{if ($2 >= 20 || $2 <= -20) {print $2,$3,$1;}}' | \
      sort -n | \
      grep -E 'kernel|gmail'

Check if anyone escaped B4 Relay remap::

   cat netdev-6.6.json | \
      jq -r '.individual | with_entries(.value = .value.score.positive)' | \
      grep "B4 Relay"

Spot-check the grouping and parsing::

    ./ml-stat.py --db db.json --since bb545da872c8 --until 0109af6d7037 \
               --repo netdev --linux ../linux --interact

Other scripts
-------------

There are also auxuliary scripts which don't do true mailing list data.
They are all deprecated now by ``git-stat.py``.

::

  $ git log v6.0..v6.1 --no-merges \
    --committer=kuba@kernel --committer=davem@davemloft.net \
    --committer=pabeni@redhat.com -- \
    net/ drivers/net/ include/net/ | \
      awk -f $repo_path/review_count.awk

TODO
----

1. How many authors have not appeared on the list.

Ideas
-----

1. Find the ratio of fixes vs features, with fixes broken down to
   fixes for own bugs introduced vs others introduced.

2. Compute the generality score to find out which developers are
   silo'ed into their own drivers vs work cross-tree.

3. Find companies with large number of disconnected developers
   and no in house expertise.

4. Split review stats between "replied to their own company"
   vs "truly cross company".
