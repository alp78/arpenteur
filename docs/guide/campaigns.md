# Campaigns

Email sent in waves, each wave rendered, tested to you, approved and only then sent. This page and the wave page are the only places in Arpenteur that send mail, and a test keeps it so.

## Your campaigns

The table shows each campaign with its list, its wave strategy and how many waves have been sent.

## Starting a campaign

A campaign needs a list, so make one first. The form takes:

- a code, such as DRYMOS-A, in letters, digits and dashes, and a name
- the list it sends from
- the folder holding its messages, one file per language, or the box "Start from the example", which copies the example folder under the campaign's code
- the programme whose cards the sends move, if any
- how the waves are cut: the cantons together by language, one canton per wave, the best score within each language, or the best score overall
- the bilingual cantons, if any, where every firm reads the French and the German text in one message

## The campaign's page

**The messages**

The Messages panel lists the campaign's folder, one row per language, with the subject the file carries or the fault it has. Each row opens an editor. The message is the text between two lines of three dashes; its first line is the subject, written as the language's own label followed by a colon, such as `Objet :`. There are no placeholders: every recipient of a language receives the same words, so the test you receive is the message every firm in the wave receives. The text goes out word for word with the signature appended at sending time, so an opt-out sentence is written here, as the last paragraph before the closing. The example templates carry one in each language. The editor refuses a file that cannot be sent, names the reason, and keeps your text in the box.

**The bilingual cantons**

In Fribourg and Valais an agency may work in French or in German, and the language guessed from its name is least certain there. A campaign that names such a canton writes each of its firms one message carrying both texts: the firm's own language first, then the other, opened by a line such as "Deutsche Version". The subject joins the two subjects with a slash. Both files must exist, and the test sends one message per order, French first and German first. Bern and Grisons can be named as well, each with German first. A canton with one official language is refused.

With one canton per wave, the firms of a bilingual canton make a single wave, since they all read the same message.

**The plan**

The Plan panel cuts the list into waves: the strategy, the size of the pilot, the size of each later wave, the days between waves and the first wave's date. "Show the cut, keep nothing" is ticked by default, so the first plan is read before it is kept. Re-planning is free until the first message leaves. A wave larger than the per-run limit under Settings, Sending is refused.

**The waves**

One card per wave shows its size, its date, and the four steps with their state. After sending it shows what came of it: sent, bounced, replied, refused, enrolled.

**Pausing**

A reason and the Pause button hold the next wave. A paused campaign shows the reason at the top and asks for a reason to resume.

## The wave's page

The steps go in order, and each repeated step clears the ones after it: a new render needs a new test and a new approval.

1. Render writes every message from the templates and the list. It is refused when no mailbox is set to read the replies and the bounces, when no signature is written, or when replies would go to an address Arpenteur does not read.
2. Send the test to me sends one message per language variant to the test recipient, marked as a test. It counts against the daily quota. A variant whose exact words you already received as the test of another wave, in this campaign or an earlier one, is not sent again; the run names that campaign and wave, and the step is marked done. A changed word, or a test that failed to arrive, sends the variant again.
3. Approve records that a person received and read every test. The box saying so must be ticked. Withdraw approval undoes it.
4. Send the wave asks for the wave's code typed back, such as `DRYMOS-A/1`, and sends. The panel above the field says how many messages, how far apart, how long it will take and how much of the daily quota is left.

The panels under the steps show the messages as recipients receive them, the tests sent with their outcome, and every recipient with the address and the variant chosen.

**A bounced recipient**

Once Read delivery failures has run, a recipient whose address bounced appears in the Bounced panel. Its link opens the firm's page, where Find a replacement looks for another address and lists what it found. When you accept one there, the panel offers to send that recipient the message this wave sent, word for word, to the accepted address. It asks for the wave's code typed back, as the wave's own send does, and goes through the same send. No cooldown applies, since the first message never arrived, and a paused campaign can still send it, since the pause holds the next wave. Each message is sent again once at most.

**The gate between waves**

The next wave waits until:

- the previous wave has been sent
- the gap between waves has passed
- Read delivery failures, on Operations, has run long enough after the previous wave
- the previous wave's bounces stayed under the campaign's ceiling; above it, the campaign pauses itself until someone resumes it with a reason
- the daily quota has room

The first wave is a small pilot for that reason.

## The signature

The campaign page links to the signature, the closing block appended to every message under a thin rule. Paste it from your mail client; it is cleaned down to a short list of allowed tags before it is stored, and the page shows what a recipient sees. A campaign cannot render without one.

## The law this page is built around

Swiss law on unfair competition, LCD art. 3 al. 1 let. o, makes mass advertising by email without consent unfair, and asks for a sender who can be identified and an opt-out that works. The controls on this page keep every message reviewed, identified and slow, and a STOP reply suppresses the address on sight. The opt-out sentence that tells a recipient so is the template's, and a message without one meets only part of the act. Whether a given recipient may be written to is the operator's decision; [the legal page](../legal.md) lists the questions.

## Related pages

- [Lists](lists.md), what a campaign sends to
- [Mail](mail.md), where replies arrive
- [Programmes](programmes.md), whose ledger follows each firm after the send
- [Settings](settings.md), under Sending, for the quota and the pace
