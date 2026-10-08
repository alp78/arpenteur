# The Limen invitation

Three templates introduce Alexis, explain why he is building Limen and invite
a reply from someone who conducts property handovers.

Copy the block between the two rules into a mail client. Keep one band
paragraph, remove its label and add your signature. If using `arpenteur send`,
the signature comes from `signature.html`. Address a person by name only when
their identity and role have been verified; the neutral greeting also works
for an agency's shared mailbox.

## Language and recipient

`stages.draft.language_of` supplies these canton-based defaults:

| Template | Cantons |
|---|---|
| [fr.md](fr.md) | GE, VD, NE, JU, FR, VS |
| [it.md](it.md) | TI |
| [de.md](de.md) | every other canton |

Use the recipient's known correspondence language when adapting a message
manually. German uses Swiss Standard German spelling.

For `regie-core`, invite someone from the agency's team to try Limen. For
`regie-letting`, ask conditionally whether the recipient conducts its own
handovers: a letting company may delegate them. The registered purpose helps
choose the recipient and the variant. It does not establish their handover
frequency, current tools or difficulties, and it does not belong in the
opening sentence. A verified detail about a recipient can replace the band
paragraph if it explains the relevance of the invitation.

## The reason for the project

Alexis explained the motivation on 2026-10-08: handovers follow similar steps,
yet forms, detail and participants' experience vary. An omission or an unclear
description can later become a point of disagreement. Limen aims to give the
tenant and the landlord or agency a common process and the same clear record.

Alexis also confirmed use on phones and tablets, signatures on screen and
digital sharing of the signed document. The other product facts are recorded
in [limen.md](../../limen.md), originally drawn from the application pages at
`src/content/apps/limen/{fr,de,it}.mdx` in the PERINGER repository. These emails
are new adaptations in each language, not quotations of those pages.

Present the concern as the founder's motivation. Do not invent a personal
dispute or suggest that the recipient mishandles handovers. A shared record
does not imply remote collaboration between devices, automatic distribution,
an official standard or a guarantee against disputes or abuse of rights.

## The invitation and the reply

The first message has one next step: reply to receive the details for trying
Limen. The trial is free. Feedback goes to the developer and helps him choose
improvements. Ask whether the workflow fits real visits and which details are
missing. Do not promise payment, free access after release, implementation of
every request or a particular amount of the tester's time.

After an interested reply, explain platform availability, installation and
the actual participation requirements before asking the person to commit. The
website form is the enrolment route at that point:

- French: https://peringer.ch/applications/limen/#testeurs
- German: https://peringer.ch/applications/limen/de/#testeurs
- Italian: https://peringer.ch/applications/limen/it/#testeurs

The `#testeurs` anchor is the same in each language. Confirm current build
availability before sending installation details. The previous brief recorded
an iOS build awaiting Apple's external beta review; it is not evidence of the
current release status. No public TestFlight link belongs in these invitations.

## Keep generated drafts consistent

`arpenteur draft` reads [limen.md](../../limen.md), not these template files.
The brief follows the same approach. In `arpenteur.toml`,
`[enrol] include_in_draft = false` keeps the fixed installation block out of
the initial invitation. The opt-out is still appended by the draft stage.
Review existing drafts against the new brief before using them.

The first message carries no second offer for custom software, feature list,
store quota, account request, measured savings claim or customer reference.
The sender's broader work is already described in the signature.
