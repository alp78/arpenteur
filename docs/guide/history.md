# History

Every run, from the console or from a terminal, with its log. The list refreshes itself while a run is going and stops when none is.

## The list

Each row shows when the run started, which stage, its state, who started it and the result. Forty rows per page, with links to older and newer runs. A dry run is listed like any other, marked as dry.

The states:

- running
- done
- error, with the message
- cancelled

## The run's page

The page shows the stage, when it started and by whom, the arguments it was given as a list of fields, the state and the result, and the log. The log updates itself while the run is live and announces when it has finished, failed or been cancelled. A run started from a terminal is watchable here too, since the log is written to the database as the stage talks.

**Stopping a run**

Cancel the run asks the stage to stop at its next log line. What it has already written stays, since every stage commits as it goes and picks up where it left off. The stages that cannot be stopped part way, the suggested approach, the interview preparation and a wave's test and send, say so.

**A run nothing is running**

A run started in a terminal that was closed, or in a console that was stopped, can be left open. The console settles its own runs as interrupted at the next start. For a run from a terminal, the page offers Mark as interrupted once it is clear nothing is still running it.

## Related pages

- [Operations](operations.md), where runs are started
