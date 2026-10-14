# Lists and campaigns

> The figures on this page were measured on the first deployment, in 2026, over a survey of five cantons of French-speaking Switzerland. Yours will differ.

A list keeps one mailbox per firm and says, for every firm it leaves out, why.
A campaign sends one message plan over a list, in waves, each wave rendered,
tested to the operator, approved and only then sent.

```bash
# a list from the survey's bands, each weighted between 0 and 1
uv run arpenteur audience new DRYMOS-1 "Tourisme" --programme DRYMOS \
    --segment tourism-office=1,nature-park=1,mountain-transport=0.7
uv run arpenteur audience refresh DRYMOS-1        # DNS again, this list only
uv run arpenteur audience build DRYMOS-1 --dry-run  # read the funnel first
uv run arpenteur audience build DRYMOS-1
uv run arpenteur audience show DRYMOS-1             # by canton, then score

# a campaign over it, waves cut by a strategy you choose
uv run arpenteur campaign strategies
uv run arpenteur campaign new DRYMOS-A "Drymos, essai" --audience DRYMOS-1 \
    --templates templates/example --programme DRYMOS --strategy canton-blocks
uv run arpenteur campaign plan DRYMOS-A --dry-run
uv run arpenteur campaign plan DRYMOS-A
uv run arpenteur campaign render  DRYMOS-A 1
uv run arpenteur campaign test    DRYMOS-A 1 --yes
uv run arpenteur campaign approve DRYMOS-A 1 --read
uv run arpenteur campaign send    DRYMOS-A 1 --yes --confirm DRYMOS-A/1
```

The same steps live in the console under Lists and Campaigns, where the send of
a wave runs as a background job with its log.

**What a list keeps.** By default, a desk mailbox read off the firm's own page,
with a DNS verdict under 30 days old, never written to in 180 days. A commune's
administration, a role one person holds, the firm's restaurant, a named person
and a consumer mailbox are each set aside with their reason. A mailbox shared
by sibling firms is written to once, for the best-scoring of them, and the
others say who covers them.

**The score** is arithmetic on stored facts, every part kept: the band's
weight, the address's quality, the verdict's freshness, and how certain the
language is. The language comes from the firm's registered name before its
canton, because 21 of the 38 reachable firms in Valais write German.

**Between waves, the gate.** The next wave waits until `arpenteur bounces` has
read the mailbox long enough after the last one, and a campaign whose wave
bounced above its ceiling pauses itself until somebody resumes it with a
reason. The first wave is a small pilot for that reason.

**Limits the host imposes.** `[smtp] daily_quota` caps what Arpenteur hands
the server in any 24 hours, tests and hand letters included, and
`pause_seconds` spaces the messages of a run. Set the quota from your own
plan: Infomaniak's published figures disagree between 100 and 1'440 a day.
