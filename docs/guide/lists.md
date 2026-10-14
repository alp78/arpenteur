# Lists

A list keeps one mailbox per firm, the right one, and says for every firm it leaves out why it was left out. A campaign sends from a list and from nothing else.

## Your lists

The table shows each list with its name, how many firms it keeps and drops, how many campaigns use it, and whether it is frozen. A list freezes the moment a campaign sends from it, so that what was sent can always be read back against the list it was sent to.

## Starting a list

The form under the table takes:

- a code, such as DRYMOS-1, and a name
- the segments it draws from, each with a weight between 0 and 1, written as `tourism-office=1, mountain-transport=0.7`
- the cantons it covers, or none for every canton
- the programme it feeds, if any, and whether to keep only firms already on that programme
- whether to accept unclear mailboxes, and addresses that only a web search reported

A list needs at least one segment, and every weight must be between 0 and 1. Once created, the page says what to do next: refresh its addresses, then build it.

## The list's page

The heading carries the code and name; the line under it restates the rule and says when the list was last built.

**The actions**

- Refresh the addresses asks the domain name system again about every address the list could use, as a run with its own log. Free, one lookup per domain.
- Build chooses one mailbox per firm and reports how many were kept and dropped.
- CSV downloads the kept rows.

**The kept rows**

One table per canton, best score first, with the language the message will be written in, the address, and why this address was chosen. The score is arithmetic on stored facts, each part kept: the segment's weight, the address's quality, how fresh the DNS verdict is, and how certain the language is. The language comes from the firm's registered name before its canton.

**The dropped rows**

The chips above the table name each drop reason with its count. Clicking one shows the firms dropped for it. A commune's administration, a role one person holds, the firm's restaurant, a named person and a consumer mailbox are each set aside with their reason. A mailbox shared by sibling firms is written to once, for the best-scoring of them, and the others say who covers them.

**Pins**

Each row has a reason field, a language select and a button to keep or drop the firm against what the rules decided. A pin needs its reason: a pin nobody can explain is undone. Unpin lets the rules decide again. A pin takes effect at the next build.

## The limits

- A frozen list refuses a build and a pin. Copy it to start again.
- A list keeps only addresses with a DNS verdict younger than thirty days and not written to in the last 180 days, unless the list says otherwise.

## Related pages

- [Campaigns](campaigns.md), which send from a list
- [Companies](companies.md), where an address is suppressed for good
- [Operations](operations.md), where Find an address and Check the addresses bring the addresses in
