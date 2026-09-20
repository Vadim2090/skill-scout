---
name: skill-scout
description: Scan recent sessions for repeating patterns and hard investigations, then propose skill candidates with the evidence behind each one. Proposes only — it never writes a skill file.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - AskUserQuestion
author: Vadim Smirnov
version: 1.0.0
date: 2026-09-18
---

# /skill-scout — find candidates, propose, stop

Sits between the two tools it was built from. `claudeception` notices a hard
investigation **while it happens** and writes a skill on the spot.
`claude-reflect`'s `/reflect-skills` notices repetition **across history** but
reads whole sessions into context and then generates files. This one takes the
cross-session view, spends almost no context getting it, and **hands back a
proposal instead of a file**.

**The split is the whole design.** Three passes, and only the middle one costs
tokens:

| | does what | cost |
|---|---|---|
| **1 · mechanical** | ranks episodes by how hard they were, groups by shared vocabulary, pulls evidence | 0 tokens, 0.07 s warm |
| **2 · semantic** | regroups by *meaning* over a compact manifest — the part vocabulary cannot reach | one read, full sweep only |
| **3 · judgement** | decides whether any of it should exist at all, and as what | the bar below |

Never let the script's ranking stand in for passes 2 and 3. There is no LLM call
inside the script and there should not be: it would make the output
non-deterministic, which breaks the ledger, and it would cost an API call on
every `/finish`.

> Determinism is a property the script has to earn, not one it gets by omitting
> the LLM. Until 2026-09-19 it did not have it — `set` iteration order fed
> `Counter.most_common()` tie-breaking, so 20 runs over unchanged input gave 20
> different term sets and ledger suppression was a coin flip. Ties now break on
> the term itself. Keep the regression check below green.

## Run it

```bash
python3 ~/.claude/skills/skill-scout/scripts/scan-sessions.py --days 21
```

| Flag | Use |
|---|---|
| `--days N` | window, default 21 |
| `--scope <substring>` | one project dir, e.g. `--scope Personal`; default all |
| `--min-sessions N` | sessions before a cluster counts as recurring (default 2; `/finish` uses 3) |
| `--min-score N` | episode floor, default 45 |
| `--manifest` | per episode: its lexical group, ledger status and terms — the input for the semantic pass |
| `--evidence id,id,id` | those episodes as one cluster, **with real excerpts** — every member, no truncation |
| `--quiet` | print nothing at all when nothing clears the bar (what `/finish` uses) |
| `--list-ledger` | what has already been proposed, accepted or declined |
| `--state DIR` · `--cache DIR` | override the ledger and cache locations |
| `--rescan` | ignore the parsed-transcript cache |

Cold run ≈8 s over ~200 transcripts; warm ≈0.1 s.

Two state directories, and the difference matters. The **cache** holds parsed event
streams, is regenerable, and defaults to `$TMPDIR/skill-scout-cache` — losing it costs
one slow run. The **ledger** records what was already proposed and must outlive a
reboot; it defaults to `~/.skill-scout/ledger.json`. Where that is not writable — a
sandboxed agent often cannot write to its own home — set `SKILL_SCOUT_STATE` (in
`~/.claude/settings.json` under `env`, so it survives) or pass `--state`. The script
refuses to write a ledger it cannot place, rather than quietly relocating it.

**`--days` is event time, not file time.** A session resumed today can carry prompts
from months back — measured on this corpus, 96 of 209 transcripts inside a 21-day
window reach behind it, the furthest by 101 days. Only in-window events are counted,
and the `WINDOW` line says how many transcripts were trimmed.

**Output is byte-identical across runs.** Enforced, not assumed — regression:

```bash
for sd in 0 1 2; do PYTHONHASHSEED=$sd python3 ~/.claude/skills/skill-scout/scripts/scan-sessions.py \
  --days 21 --no-ledger | shasum; done | sort -u | wc -l   # must print 1
```

## Read the digest

Each block is one cluster. `err` = failed tool calls, `retry` = longest run of
retrying the same tool while that tool is still failing, `corr` = detected
redirections, `solo` = longest stretch of tool calls with no user input. High
numbers mean the path was not obvious — that is all they mean. `existing skills
on these terms` is a hint to update rather than create.

**Read `corr` as a lower bound.** It is lexical detection, calibrated 2026-09-20
against 54 hand-labelled prompts: **94% precision, 92% recall on that set**, but a
sample of non-matching prompts still held 2–3 real redirections, so corpus recall
is roughly a third. A high `corr` is evidence; `corr 0` is not evidence of a clean
run. That is why it carries 0.15 of the score and the counted error rate carries
0.40. Re-check the instrument whenever the markers change:

```bash
python3 ~/.claude/skills/skill-scout/tests/correction-precision.py
```

The digest quotes prompts verbatim, private ones included. It stays on this machine:
never paste it into a tracker, a chat, a repo or a draft.

## The semantic pass — the one thing the script cannot do

Clustering above is **lexical**: shared words after ambient terms are removed. It
cannot see that *"find this person's email"* and *"pull contact details off the
profile"* are one intent — they share no word that survives the stop list. `/reflect-skills` is right that this is the whole game — it just
pays for it by reading whole sessions into a context window. Do it on the
manifest instead.

```bash
python3 ~/.claude/skills/skill-scout/scripts/scan-sessions.py --days 21 --manifest
```

Two lines per episode: id, date, project, score, failure counts and opening prompt,
then its terms. `[Rn]` is the lexical group it landed in, `[--]` ungrouped, a leading
`~` means the ledger already declined that cluster — **do not re-propose it**.
**Most episodes come back ungrouped, which is exactly where the misses are.**

Size is corpus-dependent; check it rather than trusting a number in this file.
At the time of writing: 62 lines, 8 KB, about 2K tokens.

Frame the task as disagreement, not as clustering from scratch: the brackets
already show what the mechanical pass decided, so only report where meaning and
vocabulary part ways.

1. **Merge** episodes that share an intent but no words. Name the intent in one
   sentence before you look at anything else.
2. **Split** a lexical group whose members share vocabulary but not a problem —
   "both mention `sheet`" is not a pattern.
3. Pull the real evidence for each surviving group:
   ```bash
   python3 ~/.claude/skills/skill-scout/scripts/scan-sessions.py --days 21 --evidence e5e0e7,c9ff22
   ```
   It renders any id set as one cluster and, unlike the digest, goes back into each
   transcript for the excerpts themselves — **`✗` the call that broke and what came
   back, `↩` the prompt that changed course** — for every member, not the first five.
   A session that shows neither says so: its score came from unattended work, not from
   getting stuck, which is usually a reason to drop it.
4. Only then apply the bar below.

Read the manifest in this window — a couple of thousand tokens is cheaper than a
hand-off. Past roughly 150 lines (a wider `--days`, or `--min-score` lowered), give it to a cold
`subagent_type` instead: it needs nothing this session knows, and it should hand
back the groupings, not the manifest.

**This pass runs on the full `/skill-scout` sweep only.** `/finish` stays lexical
— it has to be silent and free, and a semantic pass is neither.

**Known softness:** the ledger keys on a cluster's terms. A semantically merged
group can come back a different shape next run and slip past suppression. Put the
intent in the `--note` field in words, and check `--list-ledger` before proposing.

## The bar — this part is yours

Apply all five. A candidate that fails any one of them is not a proposal.

1. **Recurrence proves the subject is live, not that the method was hard.**
   Three sessions about Looker only prove Looker is in use. Ask what was
   *re-derived from scratch* each time.
2. **The reusable unit is the trap, not the task.** "Build the dashboard" is a
   task. "A blended data source silently rescopes the filter control" is a trap.
   If you cannot state the trap in one sentence, there is no skill.
3. **Escalate to the right mechanism.** Same mistake twice → a rule in `CLAUDE.md`;
   every time X → a hook; a procedure re-derived a third time → a skill. Most
   candidates are rules or checks wearing a skill costume. Say which one it is.
4. **Prefer updating an existing skill.** Open the ones the script named and
   compare their Problem and Trigger sections before proposing anything new.
5. **An unused skill is a standing tax.** Measured on one working setup, 2026-09-18:
   23 of 59 skills had never fired once, and the listing cost ~11K tokens a turn
   before the cleanup. A new one has to beat that bar, not merely be true.

## What you output

Lead with the verdict. If nothing clears the bar, say so in one line and stop —
that is the normal outcome and it carries information.

For each candidate that does clear it:

```
CANDIDATE — <kebab-case-name>
  trap        one sentence: what is non-obvious and gets re-derived
  evidence    N sessions, dates, what was ground through each time
  mechanism   rule in CLAUDE.md / hook / skill / update <existing-skill>
  description the frontmatter line it would ship with — the trigger, so it can be
              judged on whether it would actually fire before anything is written
  cost        why it beats the unused-skill tax
```

Then stop and ask which, if any, to build.

## Record the verdict

So the same candidate is not raised again every week:

```bash
python3 ~/.claude/skills/skill-scout/scripts/scan-sessions.py \
  --record declined --terms "looker,filter,blended,source" --note "one-off, fix was a data-source change"
```

`--record accepted|declined|proposed`. **Copy the cluster's `shared terms` line
verbatim** — those terms are the suppression key, matched at 60% overlap. Fewer
terms than the cluster showed still works (a 4-term key against an 8-term cluster
matches at 1.0), but a key built from different words will not. `--record` with no
terms is refused rather than written, because an entry that matches nothing would
look recorded and suppress nothing.

Clusters matching a ledger entry are suppressed on later runs and reported as a
count only.

## Hard rules

- **Never write a skill file from this skill.** The proposal is the deliverable.
  Creation is a separate, explicit act once one is picked — `claudeception` or a
  direct request. `Write` is deliberately absent from `allowed-tools`.
- Never propose from the script's ranking alone. Open at least one transcript
  excerpt's worth of evidence per candidate and say what was actually hard.
- Zero candidates is a valid and common result. Do not manufacture one.
- Anything created later is English-only, like every other file here.

## Where it fires

- **`/finish`** runs it `--quiet --min-sessions 3 --max-singles 0`: nothing is printed
  at all unless a cluster of 3+ sessions clears the bar. One-offs are excluded there on
  purpose — `/finish`'s own test requires 3+ sessions, so a lone session could only be
  raised in order to be rejected.
- **Manually**, `/skill-scout`, for a full pass with the lower default bar.

It is set to `user-invocable-only` in `~/.claude/settings.json`, so its
description never enters the per-turn skill listing and it costs nothing until
called.
