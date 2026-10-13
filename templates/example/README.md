# Message templates, an example

A campaign reads its messages from a folder under `templates/`, one file per language: `fr.md`, `de.md`, `it.md`. A whole-file override for one segment is named `<lang>.<segment>.md`, for example `de.mountain-transport.md`.

The message is the text between the first two lines that are exactly `---`. Anything before the first rule is notes for the person editing the file; anything after the second rule is ignored. The message's first line is the language's subject label followed by a colon, and everything after that colon is the subject:

- French: `Objet : ...`
- German: `Betreff: ...`
- Italian: `Oggetto: ...`

There are no placeholders. Every recipient of a language receives the same words, so the test message the operator receives is the message every firm in the wave receives. The opt-out sentence and the signature are appended when the message is sent; do not write them here.

A file is refused, naming the reason, when it has fewer than two rules, when its label is another language's, when the message is empty, or when it still carries an unfinished marker such as `À compléter`.

Copy this folder to `templates/<your campaign>/` and replace the three messages with yours. This folder is only an example and is never sent.
