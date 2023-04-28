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

For efficiency it makes a copy of the specified number of email messages
under the `msg-files` directory.

Example use::

  ./ml-stat.py --db db.json --email-count 4000

Use `git log` in the ml repo to find the right number of emails to look
back at. Find the first message of interest and use::

  git rev-list --count $start-hash..master

Before generating the stats it's recommended to run the self checks,
to make sure that the email mailmap and parsing are okay.

git stats
---------

Some statistics are much quicker to get from git. They may go away
over time but for now ``git-stat.py`` loads them.

Example use::

    ./git-stat.py --linux ../linux/ \
        --start-commit 7e68dd7d07a --end-commit 5b7c4cabbb6 \
	--maintainers davem@davemloft.net \
	              edumazet@google.com \
		      kuba@kernel.org \
		      pabeni@redhat.com

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

1. Compute review coverage (% of sets which went in with no reviews
   across all posted revisions). Vs by simply counting review tags.
   (Done based on commits for now).

2. Count a series as one across versions.
   This should also let us find repost violations.

3. Calculate stats for pw checks (per check and per person?)
   Download all the data from pw and filter by delegate.

Ideas
-----

1. Find the ratio of fixes vs features, with fixes broken down to
   fixes for own bugs introduced vs others introduced.

2. Compute the generality score to find out which developers are
   silo'ed into their own drivers vs work cross-tree.

3. Find companies with large number of disconnected developers
   and no in house expertise.
