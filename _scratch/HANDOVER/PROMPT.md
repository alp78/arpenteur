# Paste this to open the next session

Run Claude Code from `/home/alex/arpenteur`, then paste the block below.

---

I'm continuing work on arpenteur, a market surveying pipeline for Swiss SMEs.

Read `_scratch/HANDOVER.md` first — it has the full context, the design
decisions already taken, the legal constraints the design rests on, and the
open questions. Then read `README.md` and `arpenteur.toml` to see the shape of
the survey.

State: the foundation is built and tested (19 passing tests, no network
needed) but **nothing is committed yet** — every file is staged and `main` has
no commits. Verify with `uv run pytest` and `git status`.

Start by making that first commit. Then tell me what you'd do next, given the
blocker is that Zefix API credentials haven't been requested yet.

Conventions that matter: work on `main` (never branches or PRs), never add
AI co-author trailers to commits, comments explain why rather than what.

---

## Shorter variant, if you want to jump straight to work

I'm continuing arpenteur (Swiss SME market surveying pipeline) in this repo.
Read `_scratch/HANDOVER.md` for context, run `uv run pytest` to confirm the
foundation is green, commit the staged foundation to `main`, then <TASK>.

Replace `<TASK>` with whichever of these you want:

- "design the people stage: how to get company officers from cantonal registry
  excerpts, canton by canton, and what that costs"
- "add a --fixture mode to the source stage so the whole pipeline can run
  end-to-end without live Zefix credentials"
- "implement the sogc delta refresh so a survey can be kept current without
  re-sweeping"
- "wire the Infomaniak provider in llm.py alongside anthropic"
- "review the foundation critically and tell me what you'd change before it
  grows"
