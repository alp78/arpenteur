# Limen · Swiss rental agencies

One folder per language region, then one per canton, then one sheet per firm. Every sheet is generated from the register and rewritten on each run, so corrections belong upstream rather than in these files.

- firms: **12,608**
- with an address found: **1,951**
- still owed a tier of the search (`?`): **3,746**

## Bands

- **regie-core** (7,380) — manages property for third parties, so runs inspections routinely
- **regie-letting** (5,228) — lets property; the administration may sit elsewhere

## What a dash means, and what a question mark means

A sheet showing `-` had every tier of the address search run against it and publishes nothing findable. That is a settled result, not a gap: it stops the paid tier being spent on the same firm twice.

A `?` is the opposite claim. Some tier did not run — usually the web search, which needs API credit this machine does not have — so the firm is unfinished rather than unreachable, and it is the first place another pass should go. A blank means nothing has looked at all.

The distinction is the reason these are three symbols and not one empty cell. A dash that quietly covered unfinished work would be worse than no dash: it reads as a settled negative, and every later run skips what it covers.

## The Romansh gap

Grisons sits under `de`, its majority language. Romansh is spoken by a minority of one canton in named valleys, so no canton list selects it. The register stores each firm's commune in `legal_seat`, which is the column a real Romansh list would be built from.

