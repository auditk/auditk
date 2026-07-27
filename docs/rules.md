# Rules and rulesets

`auditk report` runs a **structural findings engine** over an ingested session
trace. The engine is deterministic, offline, and model-free: every rule is a
pure predicate over the shape of the trace (file paths, tool names, shell
command text, error flags, step metadata). No LLM judge and no drift score are
involved. The intent-enactment drift score (`auditk.analysis.drift`) is a
separate, weaker, complementary signal, not a replacement for these checks.

This document is the catalogue of what the engine checks, how to configure it,
and how it relates to your `CLAUDE.md`.

## What is NOT a goal

auditk ships **generic** checks. It is not a coverage instrument, and it is not
an encoding of any one person's development rules. Three things follow from that:

1. The shipped default ruleset (`src/auditk/analysis/rules.default.yaml`)
   contains nothing specific to any user, machine, or project. A test enforces
   this.
2. Your own rules live in a local ruleset file that is never committed to this
   repository (the paths below are gitignored). Your constitution stays yours.
3. `auditk` reads whatever `CLAUDE.md` is present on the machine it runs on. It
   does not carry a baked-in copy of anyone's policy, so the same binary behaves
   correctly on a different machine with different preferences.

## The predicate catalogue

| rule_id | severity | catches | config knobs |
|---|---|---|---|
| `scope-escape` | high | a write outside the allowed roots (and outside a scratch prefix) | `roots`, `scratch_prefixes` |
| `churn-burst` | medium | a run of repeated same-file edits with no verify between them | `churn_threshold` |
| `commit-without-tests` | medium | a `git commit` with no test or lint run since the previous commit (inline `test && commit` counts as verified) | (verify patterns are built in) |
| `tripwire:<name>` | high | a shell command matching a tripwire pattern | `tripwire_patterns` |
| `error-cluster` | medium | several tool errors close together (a lone error does not fire) | `error_cluster_k`, `error_cluster_window` |
| `delegation-unobserved` | info | a `Task`/`Agent` delegation whose child steps the trace does not contain | (none) |
| `abandoned-artifact` | low | a file written and then never referenced again | (none) |

Each finding carries a `rule_id`, `severity`, `title`, the `step_ids` it points
at, an `evidence` dict, and an `explanation`. The report also lists, under "Not
checked", any rule that could not run at all (for example `scope-escape` when no
roots and no working directory are known). That is deliberate: a rule that could
not run is reported as such rather than passed over in silence.

### Default tripwires

The shipped defaults are universal, destructive-command patterns:
`destructive-rm`, `force-push`, `db-migration`, `env-write`,
`docker-compose-down`, `kubectl-delete`, `drop-table`. They are matched
case-insensitively against a Bash step's command text. Tripwire matching is
lexical: a command that merely mentions a pattern (for example a `grep` that
searches for `rm -rf`) will match. Tripwires are intentionally high-sensitivity,
so treat a match as "look at this", not "this definitely ran".

## Rulesets

A ruleset is a YAML file whose keys are the `FindingsConfig` fields:

```yaml
roots: auto            # "auto" => derive the allow-list from the session's git root;
                       # or an explicit list of allowed write roots
scratch_prefixes:      # writes here are always benign
  - /tmp
churn_threshold: 4
error_cluster_k: 3
error_cluster_window: 5
tripwire_patterns:     # name -> regex; merged key-wise over the defaults
  my-rule: "\\bmy dangerous command\\b"
```

### The cascade

At report time the engine builds its config by layering rulesets, from lowest to
highest precedence:

1. the shipped `rules.default.yaml` (the generic base),
2. `~/.claude/auditk.rules.yaml` (your user-wide, machine-local ruleset),
3. the nearest `.auditk/rules.yaml`, found by walking up from the session's
   working directory (a per-project ruleset),
4. `$AUDITK_RULES` (a path in the environment),
5. `--rules <path>` on the command line.

Each higher layer overlays the ones below it. Scalars and list fields (`roots`,
`scratch_prefixes`) replace; `tripwire_patterns` merges key by key, so a local
ruleset can add a tripwire without dropping the defaults. On a machine with none
of layers 2 to 5, you get the shipped defaults.

### Where your ruleset lives

Put your own rules in `~/.claude/auditk.rules.yaml` (applies to all your
projects) or a repo's `.auditk/rules.yaml` (that project only). Both paths are
gitignored by auditk, and neither is ever committed to this repository.

### Roots auto-discovery

With `roots: auto` (the default), `scope-escape` derives its allow-list from the
git root containing the session's working directory, falling back to the working
directory itself outside a git repo. A session that starts inside the repository
it works on needs no configuration. A session that starts elsewhere and changes
into a sub-repository is the one case that still wants an explicit `--root`,
because the recorded working directory is the starting one.

## Scaffolding a ruleset from your CLAUDE.md

`auditk rules init` reads the `CLAUDE.md` files in effect (see below) and emits a
**starter** ruleset: the generic default, prefixed with a comment block that maps
the hard-rule phrases it recognised to the rules that already cover them, and
notes anything it cannot express as a shipped rule (infrastructure paths, for
example). It is a starting point to review and edit, never a live rule source.

```
auditk rules init                      # print a scaffold to stdout
auditk rules init --from path/to/repo  # scan that directory's CLAUDE.md cascade
auditk rules init --out ~/.claude/auditk.rules.yaml   # write it (refuses to overwrite without --force)
```

The keyword mapping is generic (delete, tests-before-commit, `.env`, migrations,
docker-compose, infrastructure, kubectl). It does not encode any one person's
wording, and it works, with a note, when no `CLAUDE.md` is present.

## Policy context

Every report opens with a **Policy context** section listing the `CLAUDE.md`
files that governed the session: the global `~/.claude/CLAUDE.md`, and the
`CLAUDE.md`, `.claude/CLAUDE.md`, and `.claude/rules/*.md` files found by walking
up from the session's working directory. This is read from the machine running
the report, so it reflects that machine's policy, not a baked-in copy. Pass
`--no-policy-context` to skip the lookup.

## Everything runs locally

The findings engine, the ruleset cascade, policy-context discovery, and the
scaffold all run on the local filesystem with no network calls. The only part of
auditk that sends session content to a third party is the optional
`llm-judge` drift scorer, which is gated behind an environment variable and is
not part of `auditk report`.
