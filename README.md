# /skill-scout — Claude Code Skill

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill that reads your own
session transcripts, finds the investigations that were genuinely hard and the ones that keep
recurring, and **proposes** skills worth writing. It never writes one.

It sits between two existing tools. [Claudeception](https://github.com/blader/Claudeception)
notices a hard investigation *while it happens* and creates a skill on the spot.
[claude-reflect](https://github.com/BayramAnnakov/claude-reflect)'s `/reflect-skills` notices
repetition *across history*, but reads whole sessions into context and then generates files.
`skill-scout` takes the cross-session view, spends almost no context getting it, and hands back
a proposal instead of a file.

## What it does

1. Reads `~/.claude/projects/*/*.jsonl` — the transcripts Claude Code already writes for every
   session, with no hook and no instrumentation
2. Scores each session on how hard it was: failed tool calls, retry runs against a still-failing
   tool, times you redirected the work, longest unattended stretch, tool breadth
3. Groups sessions that recur, after dropping the vocabulary of your job — a term present in
   >30% of episodes glues unrelated sessions together and carries no signal
4. Prints a compact digest, or a manifest for a semantic pass, or the real transcript excerpts
   behind any set of episodes
5. Records your verdict, so a declined candidate is never raised again

## Three passes, and only the middle one costs tokens

| | does what | cost |
|---|---|---|
| **mechanical** | ranks episodes, groups by shared vocabulary, pulls evidence | 0 tokens, 0.07 s warm |
| **semantic** | regroups by *meaning* over a compact manifest — the part vocabulary cannot reach | one read |
| **judgement** | decides whether any of it should exist, and as a rule, a hook or a skill | yours |

There is no LLM call inside the script, deliberately: it would make the output
non-deterministic, and the cluster's terms are the key the ledger matches on. Output is
byte-identical across runs and hash seeds, and there is a regression check for it.

## Output format

```
SCAN  21d · 210 transcripts · 30 episodes over score 45 · 2 recurring · 17 one-off
WINDOW  99 transcript(s) reach back before the window; only in-window events were counted
AMBIENT  1 term(s) ignored as job vocabulary: deploy

── R1 RECURRING · 2 sessions over 2 day(s) · top score 88 ──────────────────
   shared terms (8): azure, endpoint, gpt-5, gpt-5-mini, key, model, responses, retry
   3ad84e 09-11 my-project     s88  err 40  retry 3  corr 2  solo 118 Bash,Write,Edit
        "speed up the feedback loop — review without waiting for Thursday"
   1386e5 09-18 my-project     s85  err 44  retry 3  corr 1  solo 92  Bash,Write,Edit
        "swap the openai key to the azure endpoint"
   existing skills on these terms: none
```

`--evidence 3ad84e,1386e5` then goes back into the transcripts for the excerpts themselves:
`✗` the call that failed and what came back, `↩` the prompt that changed course.

## Installation

```bash
git clone https://github.com/Vadim2090/skill-scout.git ~/.claude/skills/skill-scout
```

Then make it invocable-only so its description never enters the per-turn skill listing —
in `~/.claude/settings.json`:

```json
{
  "skillOverrides": { "skill-scout": "user-invocable-only" }
}
```

Restart the session. `/skill-scout` is then available and costs nothing until called.

## Configuration

| Variable | Default | What it is |
|---|---|---|
| `SKILL_SCOUT_STATE` | `~/.skill-scout` | Durable. Holds `ledger.json` — what was already proposed, accepted or declined |
| `SKILL_SCOUT_CACHE` | `$TMPDIR/skill-scout-cache` | Regenerable. Parsed transcripts; losing it costs one slow run |
| `SKILL_SCOUT_SKILL_DIRS` | — | Extra `.claude/skills` directories, colon-separated |

A sandboxed agent often cannot write to its own home. Set `SKILL_SCOUT_STATE` under `env` in
`~/.claude/settings.json` so it survives, or pass `--state DIR`. The script refuses to write a
ledger it cannot place rather than quietly relocating it.

## Usage

```bash
S=~/.claude/skills/skill-scout/scripts/scan-sessions.py

python3 $S --days 21                      # the digest
python3 $S --days 21 --manifest           # input for the semantic pass
python3 $S --days 21 --evidence a1b2c3    # real excerpts for those episodes
python3 $S --list-ledger                  # what was already decided
python3 $S --record declined --terms "looker,filter,blended,source" --note "one-off"
```

| Flag | Use |
|---|---|
| `--days N` | window, default 21. **Event time, not file time** — a session resumed today can carry prompts from months back |
| `--scope SUBSTRING` | one project directory; default all |
| `--min-score N` | episode floor, default 45 |
| `--min-sessions N` | sessions before a cluster counts as recurring, default 2 |
| `--quiet` | print nothing at all when nothing clears the bar |
| `--state DIR` · `--cache DIR` | override the two directories |
| `--rescan` | ignore the parsed-transcript cache |

### Wiring it into a session wrap-up

The cheap run is safe to put in whatever closes your sessions:

```bash
python3 $S --days 21 --min-sessions 3 --max-singles 0 --quiet
```

It prints nothing unless three or more sessions on different days share a pattern.

## Privacy

**Everything stays on your machine. Nothing is transmitted.** Two things are worth knowing
before you run it in front of someone:

- The digest, the manifest and `--evidence` all print **verbatim prompts** from your sessions,
  including whatever you consider private. Do not paste that output into a shared document.
- The cache **persists prompt text** outside the skill directory, with no expiry. Deleting a
  transcript does not remove its cached copy. The cache directory is created `0700`; delete it
  with `rm -rf "$SKILL_SCOUT_CACHE"` when you want the copies gone.

## What the numbers mean, and what they do not

`corr` is lexical detection of moments you redirected the work. Calibrated against a
hand-labelled set: 94% precision, 92% recall **on that set** — but a sample of non-matching
prompts still held real redirections, so recall across a whole corpus is roughly a third.
**Read it as a lower bound.** A high `corr` is evidence; `corr 0` is not evidence of a clean
run. That is why it carries 0.15 of the score while the counted error rate carries 0.40.

The marker set is an instrument, so it ships with a way to re-measure it. The fixture stores
SHA1 prefixes only, never prompt text — which is what makes it safe to publish, and also means
it can only run on the machine whose transcripts were labelled. Elsewhere it reports
`0 of 54 labelled prompts found` and exits 0.

## Tests

```bash
# determinism — must print 1
for sd in 0 1 2; do PYTHONHASHSEED=$sd python3 scripts/scan-sessions.py --days 21 --no-ledger | shasum; done | sort -u | wc -l

# correction-marker precision against the labelled fixture
python3 tests/correction-precision.py
```

## Requirements

- Python 3.9+, standard library only. No dependencies, no network calls
- Claude Code, with transcripts under `~/.claude/projects/`. Raise `cleanupPeriodDays` in
  `~/.claude/settings.json` if you want a long history to scan

## Contributing

Issues and pull requests welcome. Two rules: the output must stay byte-identical across
`PYTHONHASHSEED` values, and any change to the correction markers ships with a re-measurement
against the fixture.

## License

MIT
