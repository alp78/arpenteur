# The console

> The figures on this page were measured on the first deployment, in 2026, over a survey of five cantons of French-speaking Switzerland. Yours will differ.

```bash
uv run arpenteur console          # http://127.0.0.1:8000
```

Filter the survey by canton, sector, segment, fit band and pipeline state; open a
company to see its register entry, officers, web research, fit reasoning and
suggested opening; correct a label, set a state and an owner, log a call, or
suppress an address.

A correction made here goes through the same helpers the stages use, so it is
the same row a re-run has to respect. Labels set by hand are marked and the
classifier will not overwrite them.

**It sends from one place only.** Two routes transmit, the test and the send
of a campaign wave, and a test asserts no third can appear. A wave goes only
after it was rendered, sent to the operator as a test, approved by a person who
read the tests, and confirmed by typing its code. Swiss law (UWG art. 3 al. 1
let. o) makes mass advertising by email without consent unfair; the campaign
controls keep every message reviewed, identified, refusable and slow, and the
consent question stays the operator's.

**It runs the stages, and shows the work.** The header follows the work in five
groups, from the survey to the invoices, and [the guide](../index.md) has a page
per entry. Operations is the page every stage is run from, with how much it has
done, how much is queued for it, and the control to advance it; History is what
has been run.

Operations is one page rather than a form page and a counts page, because the
number that makes a button meaningful is how much work is waiting for it.
"3 done, 41'238 waiting" says what to press next; a bare button says nothing.
Each card states what the stage does, what must have happened first, what it
produces, what it costs, and exactly what pressing the button will do. Any
field with a knowable set of values is a list to pick from; anything that must
be typed carries an example and says where to get it.

## Running a stage from a page

street and the house number are in the answer too. A marker placed on a town is
drawn hollow and says so when clicked.

Across 500 Geneva companies: 469 were placed on a door, 15 on a street, 9 on a town
only. Five got no point at all, and four of those are companies whose
registered address reads `ohne Domizil-sans domicile`, which is the register
saying they have none. That string is recognised, so the stage reports what the
register said rather than what this tool failed to do, and spends no request
finding out.

Both the base map and the geocoder are swisstopo, published as open government
data on the single condition that the source is named, which the page does.
Nothing is sent to a commercial geocoder: putting every client's address
through a third party to learn what the Confederation publishes would be a
data-protection decision taken for no gain.

A company page carries the same actions for that one company, since having
opened it and decided it is interesting, the alternative is going back and
constructing a filter that selects exactly it. Naming a company is itself a
declaration of size, which is why the paid stages accept it without a limit.

Starting a stage drops you on its log, which fills in as the stage talks.
Every stage already took an injectable `log` callable, so this swapped the sink
rather than touching a single stage's internals: lines go to a `run_line`
table, which means the log survives a reload, a run started from the command
line is watchable in the same view, and a stage that emits sixty thousand lines
is polled for only what the page has not seen.

The objection was never to the button but to spending real money from a page
that looks like a filter, so the guards that made the command line safe are
enforced on the server, in `console/runner.py`:

- **One at a time.** Two `enrich` runs would spend twice and interleave their
  writes.
- **A declared size.** `enrich` and `match` will not start without a limit, a
  named list of UIDs, or a dry run. Checked in the handler, because a
  `required` attribute is a convenience for the person, not a constraint on
  the request.
- **A dry run offered first**, and ticked by default. Note that `profile` is
  the exception and the form says so: its dry run still calls the model for
  every batch and skips only the write.
- **Every stage's own refusal still applies.** This adds guards, it does not
  replace them: a wide `enrich` is still refused by `enrich`.

A run is a thread inside the console, so stopping the console kills it. Those
runs are settled as interrupted at the next startup rather than left claiming
to be running; a run started from the command line is left alone, since it may
belong to a process still working in another terminal.

It binds to localhost and has no authentication, because there is no intent
that it be reachable. The database holds contact details for thousands of
companies and, where sole proprietorships are included, for identifiable
people. Exposing it wants a reverse proxy with auth in front.
