# Settings as an overlay

```bash
uv run arpenteur console      # then Settings
```

The territory, the sector and segment vocabularies, what the research looks for, the campaign
brief and the rate card decide what every stage below them does. They used to
live only in `arpenteur.toml` and `campaign.md`, which made the most
consequential decisions in the tool the ones you had to leave the tool to make.

It also hid them. A survey that contained no foundations at all, because the
code `0110` was absent from a list in a file, is not a discoverable state: the
canton was in scope, the sweep ran, and nothing anywhere said that an entire
class of entity had never been asked for. The legal form picker is now the
register's own catalogue, fetched rather than typed from memory.

**Overlay, not replacement.** `arpenteur.toml` keeps its comments and its
defaults and stays the reference for anyone reading the repository; a setting
changed in the console is stored in the database and applied over it. Rewriting
the TOML from a web form was the obvious alternative and would have destroyed
sixty lines of explanation that are the reason the file is readable. The
campaign brief is the exception and is written back to `campaign.md`, because a
surprising run should be answerable with a diff. The rate card and the catalogue live in the database alone, with the identity, the
sending limits and the model settings: the same prices apply across campaigns, and
none of it is anyone's business but the operator's, so none of it wants to be in a
versioned file that gets shown to people.

Every change records when it happened and by whom. Widening the territory
changes what every later stage will spend money on, and that is a question
asked after the bill rather than before it.
