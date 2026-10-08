# The Limen approach, one template per language

Three files, one per language the survey writes in. Copy the block between the
two rules into a mail client, delete the band variant that does not apply, and
send it. `arpenteur send` can post one instead, and it refuses any address that
asked to be left alone.

## Region is language, and the code already decides it

`stages.draft.language_of` maps a canton to one of the three, so the region a
firm sits in picks its template with no further judgement.

| Template | Cantons |
|---|---|
| [fr.md](fr.md) | GE, VD, NE, JU, FR, VS |
| [it.md](it.md) | TI |
| [de.md](de.md) | every other canton |

## Two variants per template, one per band

`limen.md` argues the distinction and the templates carry it. A firm whose
registered purpose names managing property for third parties conducts a handover
at every tenancy, so the message may say the tool touches its weekly work. A
firm that only lets property sees handovers occasionally, and the same sentence
addressed to it is false. Keep the variant that matches the band
`intel.segment` holds and delete the other.

## Where the product sentences come from

The application's own pages, which the owner wrote and which are live:

- `src/content/apps/limen/fr.mdx` in the PERINGER repository
- the same directory's `de.mdx` and `it.mdx`

Every product sentence in these templates restates one of those pages in that
page's own language. No template claims a feature its page does not, and the
German and Italian are the owner's own wording rather than a translation of the
French.

## The enrolment route is the website form

Both platforms go through one form, which already asks for the platform and the
account address and already explains why Google needs one.

- French: https://peringer.ch/applications/limen/#testeurs
- German: https://peringer.ch/applications/limen/de/#testeurs
- Italian: https://peringer.ch/applications/limen/it/#testeurs

The `#testeurs` anchor is hardcoded in `AppPage.astro` for every language, so
the German and Italian links keep the French word. That is correct rather than
an oversight.

Routing through the form instead of asking for a reply means the four facts
`limen.md` wants arrive in one structured place, and it means the message does
not have to carry a store link at all.

## The TestFlight link stays out, and here is the condition for adding it

As recorded on 2026-10-01 in the vault's `apple/limen.md`, the iOS build sits at
`READY_FOR_BETA_SUBMISSION`: attaching it to an external group over the API
wrote the relationship without creating a Beta App Review submission. The public
join link works anyway, against a build Apple has not approved.

The application's own page withholds that link for this reason and promises it
by mail once Apple has released the test version. The templates say the same.

Add the link to all three templates only once a submission exists and Apple has
approved it. Sending it to hundreds of agencies before then would enrol people
against a build that may yet be refused, and the first impression of Limen would
be a store error.

## The closing offer, and why it is written as a fact

`peringer/prospect-email-style.md` settles the register: a differentiator
presented as a structural characteristic is verifiable, and the same thing
presented as a claim about character reads as a pitch. So the closing sentence
names what is built and the condition under which it applies. It promises no
availability, no rate and no timescale, which none of these messages can know.

## What no template states

**No price**

The test costs nothing, which each message says. The published version's price
turns on an agreement that is not yet active, so no figure appears anywhere.

**No saving in francs or hours**

No measurement over real handovers exists. Producing one is what the test is
for, and `limen.md` forbids the claim until it does.

**No other agency**

Neither as a reference nor as an example, which `limen.md` also forbids.
