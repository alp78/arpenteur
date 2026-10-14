# Notes

Your own writing about a client, in Markdown, written in whatever editor is
already open.

```bash
uv run arpenteur note CLAIR-LOGIS add "C:\Users\you\notes\AUDIT-CONDITIONS.md" --pin
uv run arpenteur note CLAIR-LOGIS list
uv run arpenteur note CLAIR-LOGIS show 1        # the Markdown back out
```

A Windows path is accepted as typed. This runs under WSL as often as not, and
retyping `C:\Users\...` as `/mnt/c/Users/...` is a translation the program can
do and a person should not have to.

**The file stays the master when there is one.** The path is stored, so running
the same command again after editing updates that note rather than writing a
second one, and an unchanged file reports `unchanged` rather than claiming work
that did not happen. A note pasted into the console has no path and is edited
there. Both are the same row, and which is authoritative is shown rather than
assumed.

**Markdown is the stored form, never a rendering.** HTML is produced on the way
to a page and thrown away, because a stored rendering drifts from its source
the first time the source is edited. `/note/{id}.md` returns exactly what was
written, with `text/markdown`, so an agent has a document to fetch and a note
typed in the console can be got back out as a file.

Raw HTML inside a note is escaped rather than passed through, which CommonMark
specifies the other way. These are rendered into the console's own pages, a
note may be pasted from anywhere, and no note has ever needed a `<div>`.

**They reach the stages.** `advise` and `prep` read the notes alongside the
dossier, above the client's own documents, under a heading that says whose they
are: a document the client sent is evidence, a note is instruction, and the
model has to be able to tell them apart. Where a note states a position, a
constraint or a decision, the prompt says to follow it. `arpenteur dossier
CODE` prints them too, which is the path for an agent with no browser.

Notes are searched full-text in French, tagged, and pinned when one of them is
the standing brief rather than another meeting note.
