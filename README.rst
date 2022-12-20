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

Before generating the stats it's recommended to run the self checks,
to make sure that the email mailmap and parsing are okay.

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

::

  $ git log v6.0..v6.1 --no-merges \
    --committer=kuba@kernel --committer=davem@davemloft.net \
    --committer=pabeni@redhat.com -- \
    net/ drivers/net/ include/net/ | \
      awk -f $repo_path/review_count.awk

Ideas
-----

1. Find the ratio of fixes vs features, with fixes broken down to
   fixes for own bugs introduced vs others introduced.

2. Compute the generality score to find out which developers are
   silo'ed into their own drivers vs work cross-tree.
