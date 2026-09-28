# Jev semantic regrouping pilot

## Decision

Drop the integration for now. Keep the branch as a reproducible pilot, but do not add
Jev to the skill's semantic-pass instructions until an in-scope corpus contains enough
ungrouped episodes to demonstrate useful merges.

## Method

The pilot used Jev 1.13 through TypeSafe's HTTP API. It sent one batched Choice question
per eligible ungrouped episode when lexical groups existed and one batched Noul question
per ungrouped pair. The acceptance gate was probability at least 0.80; Choice attachments
also required confidence at least 0.50. Complete-link grouping prevented a strong A-B and
B-C chain from merging when A-C was weak.

The machine-wide manifest could not be used because it included a work project outside
this repository's permitted personal scope. The recorded pilot therefore used the 21-day
`Personal-Projects` scope. No prompt or evidence text is included in this document.

## Results

Source: `scan-sessions.py --days 21 --scope Personal-Projects --manifest`, piped to
`jev-regroup.py --terms-only --json` on 2026-09-28.

| Measure | Result |
|---|---:|
| Qualifying episodes in the manifest | 5 |
| Attachments to existing lexical groups at 0.80 | 0 |
| New groups at 0.80 | 0 |
| Input tokens reported by the API | 1,763 |
| Output tokens reported by the API | 284 |
| Estimated cost at $0.042 per million input tokens | $0.00007405 |
| API wall time measured by the helper | 0.352 seconds |

A sensitivity run at a 0.50 probability threshold also produced zero attachments and
zero new groups. Source: the same manifest piped to `jev-regroup.py --terms-only
--threshold 0.50 --json`; the API reported 1,763 input tokens and 284 output tokens.

There were no proposed merges, so a ten-merge spot-check was not possible: 0 proposals
were available, with 0 marked correct and 0 marked wrong. Prompt-mode agreement was not
measured. The only individually verified non-confidential project scope had 1 qualifying
episode, which cannot form a pair; broader personal scopes included confidential material
and were not sent in prompt mode.

## Interpretation

The helper met the egress and structured-output goals, but this permitted sample supplied
no evidence that Jev recovers intents missed by lexical clustering. The result is a data
limitation rather than an accuracy finding. Reconsider the pilot when a non-confidential
scope has at least 10 human-reviewable proposed pairs; keep terms-only as the default and
repeat the prompt-agreement comparison only inside that scope.
