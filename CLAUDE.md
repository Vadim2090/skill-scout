# skill-scout — agent instructions

Read this before changing anything here. `README.md` explains what the tool does for a user;
this file is what someone editing it has to know.

## Layout

| Path | What it is |
|---|---|
| `SKILL.md` | The skill itself: the judgement the model applies. Prose, no logic |
| `scripts/scan-sessions.py` | The whole mechanical half. Stdlib only, no network, no LLM call |
| `tests/correction-precision.py` | Scores the correction markers against a labelled fixture |
| `tests/correction-labels.json` | SHA1 prefixes and labels. **Never prompt text** |

If you installed this by symlinking it into `~/.claude/skills/`, **edits here are live** —
there is no separate installed copy. Commit before experimenting.

## Two invariants. Break either and the tool stops meaning what it says.

**1 · The output is byte-identical across runs.**

```bash
for sd in 0 1 2; do PYTHONHASHSEED=$sd python3 scripts/scan-sessions.py --days 21 --no-ledger | shasum; done | sort -u | wc -l   # must print 1
```

A cluster's terms are the key the ledger matches on, so unstable output means a declined
candidate comes back. This was broken once: `set` iteration order fed `Counter.most_common()`
tie-breaking and 20 runs gave 20 different term sets. Sort explicitly; break ties on the key.
This is also why there is no LLM call inside the script and should not be.

**2 · A change to the correction markers ships with a re-measurement.**

```bash
python3 tests/correction-precision.py     # floors: 90% precision, 85% recall
```

`corr` carries part of every score, so the marker set is a measurement instrument. The fixture
holds hashes only, which makes it safe to publish and also means it only runs on the machine
whose transcripts were labelled; elsewhere it reports `0 of 54 found` and exits 0. If you work
on another corpus, label your own sample rather than trusting these floors.

## Conventions

- English in every file, including comments. The one exception is the Russian stop-word list
  and marker regex, which are data the code matches against — the file declares that with the
  `lang-check: data` marker in its header.
- No host-specific paths. Directories come from `$HOME`, `tempfile.gettempdir()` or discovery.
- The cache stores parsed events; bump `CACHE_VERSION` whenever `parse()` or `features()`
  changes shape, or stale features are served until someone remembers `--rescan`.
- The script never writes a skill file, and `Write` is deliberately absent from the skill's
  `allowed-tools`. That guarantee is structural — keep it that way.

## Known open, in rough order of value

- **Clustering** is greedy leader selection over a containment metric, so a broad leader can
  join members that share nothing with each other, and the grouping depends on leader order.
- **Ambient-term subtraction** can erase the very subject that recurs when `--scope` narrows to
  one coherent project, and it switches on abruptly at the tenth episode.
- **Fingerprint dedupe** hashes the first five prompts, so forks that share an opening but
  diverge later collapse into one episode and understate recurrence.
- **`corr` recall** across a whole corpus is roughly a third. Precision was the easy half.
- Public ids are six hex characters: ~0.1% collision at 200 episodes, ~3% at 1000.
