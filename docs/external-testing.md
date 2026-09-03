# Testing auditk on your own traces

Thanks for being willing to try this out. This page is everything you need
to get `auditk` running against a real agent session on your own machine,
without asking anything else of you first.

**Please only run this against your own agent sessions with my permission
for that specific round of testing.** A quick email or message before each
time works fine, this isn't a one-off blanket approval. If you're reading
this because we've already agreed on a test, go ahead; if not, drop me a
line first.

## Install

`auditk` is a Python package, developed and tested with [uv](https://docs.astral.sh/uv/).
From a fresh clone:

```bash
git clone https://github.com/auditk/auditk
cd auditk
uv sync --extra dev
uv run auditk version
```

That last command should print `auditk` and `auditk-spec` version numbers.
If it does, the install worked. Requires Python 3.11 or later (uv will sort
this out for you).

Everything below assumes you're running commands as `uv run auditk ...`
from inside the cloned repo. If you've installed auditk some other way
(`pip install auditk`), drop the `uv run` prefix.

## Point at your traces

`auditk` reads a session via an adapter, one per agent harness. Pick
whichever matches what you're running:

| Your harness | Adapter name | What `--in` expects |
|---|---|---|
| Claude Code | `claude-code` | a session `.jsonl` file, e.g. from `~/.claude/projects/<project>/<session>.jsonl` |
| Hermes | `hermes` | a JSON array of `messages`-table rows (`SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp` against `~/.hermes/state.db`, exported as JSON) |
| LangGraph | `langgraph` | a JSON array of checkpoint dicts from your LangGraph checkpointer |
| Anything exporting OpenTelemetry/OpenInference | `generic-otel` | a JSON array of exported spans |
| Pi | `pi` | not yet, see below |

The exact shape each adapter expects is documented in its own module
(`src/auditk/adapters/<name>.py`) and in `docs/adapters.md`, which is the
full contract for how a native trace maps onto auditk's internal `Trace`
model. If you're not sure your export matches, that's fine, the next step
(`doctor` or `report`) will tell you rather than silently producing a
number you can't trust.

If you're running Pi: there's no adapter for it yet. I haven't seen a real
Pi session trace, and I don't want to guess at a parser from public docs
alone (formats drift, and a wrong guess would produce confident-looking
numbers over a mis-parsed session, which is worse than no numbers at all).
`--adapter pi` exists and will tell you exactly this rather than crashing
or silently doing nothing. See `docs/pi-format-notes.md` for what I'd need
from you to build a real one, if you're willing to share a sample session
under the same per-instance permission as above.

## Run `auditk doctor` first

Before looking at any findings, check whether the adapter actually parsed
your session in a way you can trust. This is the honest-instrument step:
a confident-looking report over a session the adapter mostly failed to
read is worse than an explicit refusal.

For Claude Code, `doctor` walks your on-disk session corpus directly (it
never writes to anything under the paths it walks):

```bash
uv run auditk doctor --root ~/.claude/projects
```

It prints a plan-anchor tool-call histogram and an overall health verdict.
If it says `BREACH`, read the reasons it prints before doing anything else,
`report` (below) will refuse to run against the same session until either
the underlying issue is fixed or you pass `--force` and accept you're
looking at a report over a session the adapter may have mis-parsed.

`doctor` only knows how to walk a Claude Code corpus layout today. For the
other adapters, there's no separate `doctor` command, the same health
check runs automatically as the first step of `report` itself (see below):
it refuses the same way, for the same reason, you just don't get a
standalone corpus-wide pass over it first.

## Then run `auditk report`

```bash
uv run auditk report --adapter claude-code --in path/to/your-session.jsonl
```

(swap `--adapter` and `--in` for whichever harness you're using; add
`--format json` if you want machine-readable output instead of the default
markdown).

This gives you a single-session post-mortem: a condensed timeline, what the
agent did in response to each thing you asked it, and a set of structural
findings, scope escapes beyond your allowed write roots, churn bursts,
commits without a preceding test run, error clusters, unobserved
delegation, and command tripwires (destructive `rm`, force-push, DB
migrations, `.env` writes). All of this is deterministic and rule-based,
there's no model call anywhere in this path, so the same session always
produces the same report.

## What you get, and what it doesn't claim

`report` on its own doesn't produce a drift score, only structural
findings as described above. Scoring intent-enactment drift (whether the
agent's declared plan matches what it actually did, as a number) is a
separate command, `auditk attest`, and it needs either a local NLI model
(`pip install auditk[nli]`, a real download) or an LLM judge behind an API
key (a paid network call). Neither is needed for the walkthrough above, and
I'd rather you not spend money on my behalf without asking first, so
`attest` isn't part of this quickstart. If you want to try it, the DOI
below explains what the scoring protocol does and doesn't establish.

Either way, the framing to keep in mind: this is a fixed **scoring
protocol** applied consistently, not a trained detector and not a claim
that it catches every kind of drift or deception. It measures the gap
between what an agent said it would do and what it did, nothing more.
No human-agreement study has been run against these findings or against
the drift score. If you use the drift scoring, it's citable as:

> Dawson, M. (2026). auditk: an open, attested standard for measuring
> intent-enactment fidelity in agentic AI. Technical report v0.2. Zenodo.
> https://doi.org/10.5281/zenodo.22045799

## What leaves your machine

Nothing, by default. Everything above (`doctor`, `report`) runs entirely
offline: no network call, no telemetry, no phone-home of any kind. Your
session data stays on your machine unless you choose to send me a copy of
something yourself.

If you'd rather I (or the report file itself) never see the raw content of
your tool calls and results, pass `--strip-payloads` to `report` (or
`ingest`, if you're producing a standalone trace file): names, tool kinds,
ids, and timing all survive, but the actual command text, file contents,
and tool output get replaced with a redaction marker plus a size. Worth
knowing: some structural findings (the command tripwires in particular)
read the actual payload content to fire, so a redacted report will
legitimately find less than an unredacted one. That trade-off is
documented on the flag itself (`auditk report --help`).

One more thing worth knowing before you share a report with me: unless you
pass `--no-policy-context`, `report` walks up from your current directory
looking for `CLAUDE.md` files to summarise as "policy context" in the
output. On my own machine this genuinely picked up my personal
`~/.claude/CLAUDE.md` when I ran `report` from an unrelated directory. If
you'd rather that not happen (or you're just not sure what it might pick
up), add `--no-policy-context` to the command.

## Licence

Apache-2.0. See `LICENSE` in the repo. You're free to read, modify, and
share the code under those terms; nothing about running this against your
own sessions requires anything further from you.

## One more time, because it matters

Please treat each round of testing as needing my go-ahead first, a short
email or message is all it takes. Thanks again for helping test this.
