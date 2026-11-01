# memo-scoped

A permanent memory for coding agents. It is one Python file with no dependencies.

The agent writes one-line memories as it works (`memo note "..."`). At the start of
every session it reads back as many of them as fit in a fixed byte budget
(`memo wake`). When there are too many to fit, the agent itself compresses old
runs of memories into one-line summaries (`memo nap`). The originals are never
deleted, so any summary can be opened back into what it compressed
(`memo zoom`), and every memory and summary can be searched (`memo recall`).

Memories live in **scopes**: `core` for the user and anything that is always
relevant, plus one scope per project (`webapp`, `thesis`, `infra/backup`). A session
wakes `core`, plus the scopes linked to the folder it was started in. Every
other scope shows up as a single summary line, which the agent can wake on
demand. This keeps the context a session starts with about the work at hand,
even after months of notes across dozens of projects.

```
== core (2 memories)
#0 2026-11-01 User prefers short answers with numbers attached.
#1 2026-11-01 User works on a Linux desktop and a laptop, reachable from each other over ssh.
== webapp (2 memories)
#2 2026-11-01 webapp: Django + Postgres; run migrations with 'make migrate', never by hand.
#3 2026-11-01 webapp: the flaky CI login test is a race in the session fixture; rerun before debugging.
-- other scopes:
thesis (1): (no summary yet)
pending naps: 0 | bytes: 490 | You are awake.
```

It started as a fork of [OptMem](https://github.com/VictorTaelin/OptMem) by
Victor Taelin, and its interface and spirit come from there. The store
underneath is a rewrite: OptMem keeps one fixed-width log compressed as a
binary tree, while memo-scoped keeps a journal of events and builds one tree
per scope from it, so memories can be edited, moved between scopes and hidden.

## Install

```sh
git clone https://github.com/Ufuk-a/memo-scoped ~/memo-scoped
ln -s ~/memo-scoped/memo ~/.local/bin/memo
memo init
```

Python 3.8+, stdlib only. The memory lives in `~/.optmem/memory2`, or in
`$MEMORY_DIR` if set. `init` exists so that a mistyped `MEMORY_DIR` gives an
error instead of quietly starting an empty second memory.

### Claude Code

Run `wake` from a SessionStart hook. Claude Code moves hook output over about
10 kB into a file and shows only a short preview, so split the wake into parts
that each stay under that limit:

```json
"hooks": {
  "SessionStart": [{ "hooks": [
    { "type": "command", "command": "$HOME/.local/bin/memo wake --part 1/3" },
    { "type": "command", "command": "$HOME/.local/bin/memo wake --part 2/3" },
    { "type": "command", "command": "$HOME/.local/bin/memo wake --part 3/3" }
  ]}]
}
```

The parts break at line boundaries and come out close to equal in size. With
the default 19 kB budget, three parts is enough.

Then tell the agent what the tool is for, in its system prompt or `CLAUDE.md`.
Something like:

```
You have a permanent memory, printed at session start. Record with
`memo note "..." [--scope S]`: one line, at most 280 bytes. Note what will
still matter later: facts about the user, project decisions, lessons. Not
progress logs, and nothing this prompt already says.

Scopes: `core` is always woken; others are woken by the folder you're in
(`memo map S` links the current folder to S) or by `memo wake S`. Start a new
scope when a long-running project starts.

- `memo nap`: do a pending compression when asked; do it before your next action.
- `memo recall <regex>`: search every memory and summary, all scopes.
- `memo zoom <#node|id>`: open a summary into what it compresses.
- `memo edit <id> "..."`: rewrite an outdated memory instead of noting a fix.
- `memo move <ids|--match RE> --to S`: move memories to another scope.
- `memo drop <ids>`: hide one. `memo forget <#node>`: redo a bad summary.

Summaries replace their memories only in what wake prints: the originals stay
in the journal and can still be zoomed and recalled, so compress hard.
```

Subagents should not run `memo`. They don't know what is already in memory,
so their notes come out duplicated.

## Commands

```
memo init                          create the memory
memo wake [scope ...] [--part K/N] print the memory (core + folder scopes + args)
memo note "..." [--scope S]        record one memory
memo nap                           show the next pending compression
memo nap <node> "..."              answer it
memo nap --json [--all]            pending naps as JSON, for scripts
memo edit <id> "..."               rewrite one memory in place
memo move <ids|--match RE> --to S [--from S] [--yes]
                                   move memories to another scope
memo drop <ids>                    hide memories (recall --all still sees them)
memo recall <regex> [--scope S] [--all]
                                   search every memory and summary
memo zoom <node|id>                one tree node's children
memo scopes                        every scope: memories, pending naps, summary
memo map [SCOPE [PATH]]            list folder->scope links, or link PATH (default: here)
memo map --rm [PATH]               unlink
memo forget <node>                 drop a summary; nap asks for it again
memo config [NAME=N]               show or change the sizes below
memo check                         replay the journal and report its health
memo import-v1 <dir> <tsv>         one-time import of an OptMem v1 memory
```

ids look like `12`, `#12`, or `300-303,306`. Tree nodes are hash prefixes as
wake prints them (`#a1b2c3`, with or without the `#`). `note --ts
YYYY-MM-DD[THH:MM:SS]` backdates a memory.

"Here" means the current folder, or `$MEMO_CWD` if set. `MEMO_CWD` is for a
wrapper that forwards `memo` from another machine over ssh, so folder
matching still works there.

Sizes (`memo config`):

| knob | default | meaning |
|---|---|---|
| `WAKE_CHARS` | 19000 | bytes `wake` prints, at most |
| `CORE_CHARS` | 5000 | of those, reserved for `core` |
| `ENTRY_CHARS` | 280 | longest memory or summary, in bytes |
| `FANOUT` | 4 | average children per tree node |
| `DORMANT_LINES` | 20 | non-woken scopes listed, one line each |
| `ROOT_STALE` | 6 | new memories in a scope before its one-line summary is re-asked |

## How it works

### The store

```
LOG.jsonl     append-only journal: the only source of truth
scopes.map    "path  scope" per line: which folders wake which scopes
config        the knobs above
cache/        summaries: derived from the journal, safe to delete
.lock         every write takes this lock
```

The journal has four kinds of record: `note` (id, timestamp, scope, text),
`edit` (id, new text), `move` (ids, new scope) and `drop` (ids). Replaying
it in order gives the current set of memories. Ids only ever go up and are
never reused. Every write is an append made under the lock and flushed to
disk. If a crash leaves a half-written last line, replay ignores it, `check`
reports it, and the next write seals it off with a newline so it can never
merge with a later record.

`scopes.map`: a session wakes every scope whose folder contains the current
folder, and `note` files to the deepest such match, else `core`. Scope names
are `[a-z0-9_-]+` parts joined by `/`. The slash is only for naming:
`infra/backup` has its own tree, and `infra` doesn't include it.

### The tree

Each scope's memories, sorted by time, are grouped into a tree. Whether an
item ends its group depends only on the item's own hash: it ends the group
if `sha1(hash) mod FANOUT == 0`. Groups become the nodes of the next level
up, and this repeats until a single root is left. Because nothing else goes
into the decision, the tree is a pure function of the memories:

- Editing, moving or hiding a memory changes only the nodes above it, and
  occasionally the neighbouring group. Every other summary stays valid.
- The same run of memories produces the same subtree in any scope, so
  summaries survive a move.
- The tree isn't stored anywhere; it is rebuilt from the journal on every
  run. That takes well under a second for 20k memories.

A node is **complete** once the hash rule has closed it and its last child
is complete too. Only complete nodes get summaries. The newest memories are
always in groups that haven't closed yet, so they always print raw. A
**pending nap** is a complete node with no summary whose children are all
memories or summarised nodes. Naps are asked bottom-up, oldest first. Once a
scope has none left, an explicit `memo nap` also asks for a one-line summary
of the whole scope, which is what the `-- other scopes:` list shows. It is
asked again after `ROOT_STALE` new memories, and until then the line shows
`(+N)`.

### wake

For each woken scope, wake starts at the root and keeps opening the newest
summarised node whose children still fit in the scope's budget. So old eras
print as summaries and recent ones as raw memories, and how far back the
detail reaches depends on the budget. Summaries print as
`#<hash6> text`, memories as `#<id> <date> text`. If a complete node is still
missing its summary, its children print in its place and the scope header
says `(stale)`.

Budget: the `-- other scopes:` list and the last line are set aside first.
`core` gets `CORE_CHARS` (or everything, if it is the only scope woken), and
the rest is split evenly among the other woken scopes. Whatever a scope
doesn't use passes to the next one. If the lines that must print (the
unclosed groups) still don't fit, the oldest of them are replaced by one
`… N older memories omitted (pending naps: M)` line. Output never exceeds
`WAKE_CHARS`.

### Batch naps

`nap_batch.py` answers pending naps through the Anthropic API, level by
level, using `memo nap --json --all`. It is useful after a big import. Leave
the top levels to the agent: those summaries are the ones every session
reads first.

## Migrating from OptMem v1

1. `memo init` (the new memory must be empty, so that v1 ids carry over unchanged).
2. Write a TSV of `id<TAB>scope` lines. Ids you leave out go to `core`.
3. `memo import-v1 ~/.optmem/memory scopes.tsv`. v1 dates are kept. v1's
   summaries are saved to `cache/v1_hints.txt`, and `nap --json` offers
   them as hints for the matching periods.
4. Answer the naps, by hand with `memo nap` or with `nap_batch.py`. Until
   they are answered, wake shows only the newest memories that fit.

## Tests

`python3 -m unittest -v test_memo` covers the journal and crash recovery,
how the tree is built and how it changes on edits and moves, wake budgets,
nap ordering, recall, map and v1 import.

## Credits

Forked from [OptMem](https://github.com/VictorTaelin/OptMem) by Victor Taelin.

MIT for this repository's own code (see `LICENSE`). The parts that come from
OptMem stay under its author's terms.
