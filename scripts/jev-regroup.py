#!/usr/bin/env python3
"""Propose semantic regroupings for a skill-scout manifest with TypeSafe Jev.

This helper is deliberately separate from scan-sessions.py. It reads a manifest,
makes one optional network request, prints proposals, and never writes the ledger.
Terms-only egress is the default; prompt text requires an explicit, narrow scope.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


API_URL = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000
SENSITIVE_SCOPE = re.compile(
    r"(?:compensation|salary|visa|venture|job[-_ ]?search|career[-_ ]?search|"
    r"employment|recruit|immcore|dreem)", re.I
)
HEADER = re.compile(
    r"^\[(?P<tag>~?(?:R\d+|--))\s*\]\s+"
    r"(?P<id>\S+)\s+(?P<date>\S+)\s+(?P<body>.*)$"
)
METRICS = re.compile(
    r"^(?P<project>.*?)\s+s(?P<score>\d+)\s+"
    r"e(?P<errors>\d+)\s*/r(?P<retries>\d+)/c(?P<corrections>\d+)\s{2,}(?P<prompt>.*)$"
)


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Episode:
    episode_id: str
    group: str
    declined: bool
    date: str
    project: str
    score: int
    errors: int
    retries: int
    corrections: int
    terms: tuple
    prompt: str

    def state(self, with_prompts=False):
        value = {
            "id": self.episode_id,
            "lexical_group": self.group,
            "date": self.date,
            "project": self.project,
            "score": self.score,
            "errors": self.errors,
            "retries": self.retries,
            "corrections": self.corrections,
            "terms": list(self.terms),
        }
        if with_prompts:
            value["opening_prompt"] = self.prompt
        return value


def parse_manifest(text):
    if "✗" in text or "↩" in text:
        raise ManifestError("evidence excerpts are not accepted; pass --manifest output only")
    lines = text.splitlines()
    episodes = []
    index = 0
    while index < len(lines):
        match = HEADER.match(lines[index])
        if not match:
            index += 1
            continue
        if index + 1 >= len(lines) or not lines[index + 1].lstrip().startswith("terms:"):
            raise ManifestError("episode %s has no following terms line" % match.group("id"))
        metrics = METRICS.match(match.group("body"))
        if not metrics:
            raise ManifestError("could not parse metrics for episode %s" % match.group("id"))
        raw_tag = match.group("tag")
        tag = raw_tag[1:] if raw_tag.startswith("~") else raw_tag
        raw_terms = lines[index + 1].split("terms:", 1)[1]
        terms = tuple(x.strip() for x in raw_terms.split(",") if x.strip())
        episodes.append(Episode(
            episode_id=match.group("id"), group=tag, declined=raw_tag.startswith("~"),
            date=match.group("date"), project=metrics.group("project").strip(),
            score=int(metrics.group("score")), errors=int(metrics.group("errors")),
            retries=int(metrics.group("retries")),
            corrections=int(metrics.group("corrections")), terms=terms,
            prompt=metrics.group("prompt").strip(),
        ))
        index += 2
    if not episodes:
        raise ManifestError("no episodes found; pass scan-sessions.py --manifest output")
    return episodes


def group_summary(episodes):
    groups = {}
    for episode in episodes:
        if episode.declined or episode.group == "--":
            continue
        groups.setdefault(episode.group, []).append(episode)
    result = {}
    for name, members in sorted(groups.items()):
        result[name] = {
            "member_ids": [x.episode_id for x in members],
            "terms": sorted({term for x in members for term in x.terms}),
        }
    return result


def build_request(episodes, with_prompts=False):
    eligible = [x for x in episodes if not x.declined]
    ungrouped = sorted((x for x in eligible if x.group == "--"), key=lambda x: x.episode_id)
    groups = group_summary(episodes)
    state = {
        "episodes": [x.state(with_prompts) for x in eligible],
        "existing_lexical_groups": groups,
    }
    questions = {}
    if groups:
        criteria = dict((name, "Same recurring underlying intent as this lexical group")
                        for name in groups)
        criteria["none"] = "No existing group expresses the same underlying intent"
        for episode in ungrouped:
            questions["attach_%s" % episode.episode_id] = {
                "type": "choice",
                "instructions": (
                    "Which existing lexical group, if any, has the same underlying reusable "
                    "intent as episode `%s`? Match the task intent, not merely shared tools, "
                    "projects, or vocabulary." % episode.episode_id
                ),
                "criteria": criteria,
            }
    for left_index, left in enumerate(ungrouped):
        for right in ungrouped[left_index + 1:]:
            questions["pair_%s_%s" % (left.episode_id, right.episode_id)] = {
                "type": "noul",
                "instructions": (
                    "Do episodes `%s` and `%s` express the same underlying reusable intent? "
                    "Answer yes only for the same goal or recurring problem, not merely the "
                    "same project, tool, or broad topic." % (left.episode_id, right.episode_id)
                ),
                "criteria": {
                    "true": "The episodes are instances of the same reusable intent",
                    "false": "The episodes are different intents",
                },
            }
    if not questions:
        raise ManifestError("no eligible ungrouped episodes to evaluate")
    return {"state": state, "model": "jev-latest", "questions": questions}


def call_api(payload, api_key, timeout=60, attempts=3):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            if exc.code not in (429, 529) or attempt + 1 == attempts:
                raise RuntimeError("TypeSafe API returned HTTP %d: %s" % (exc.code, detail))
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2 ** attempt
            time.sleep(min(delay, 10))
        except urllib.error.URLError as exc:
            if attempt + 1 == attempts:
                raise RuntimeError("TypeSafe API request failed: %s" % exc.reason)
            time.sleep(2 ** attempt)
    raise RuntimeError("TypeSafe API request failed")


def accepted_pairs(ungrouped, answers, threshold):
    probabilities = {}
    for left_index, left in enumerate(ungrouped):
        for right in ungrouped[left_index + 1:]:
            key = "pair_%s_%s" % (left.episode_id, right.episode_id)
            probabilities[(left.episode_id, right.episode_id)] = float(
                answers.get(key, {}).get("noul", 0.0)
            )
    components = [{x.episode_id} for x in ungrouped]
    ranked = sorted(probabilities.items(), key=lambda item: (-item[1], item[0]))
    for (left, right), probability in ranked:
        if probability < threshold:
            continue
        left_component = next(x for x in components if left in x)
        right_component = next(x for x in components if right in x)
        if left_component is right_component:
            continue
        cross = []
        for a in left_component:
            for b in right_component:
                cross.append(probabilities.get(tuple(sorted((a, b))), 0.0))
        if cross and min(cross) >= threshold:
            left_component.update(right_component)
            components.remove(right_component)
    groups = []
    for component in components:
        if len(component) < 2:
            continue
        members = sorted(component)
        pair_values = [probabilities[tuple(sorted((a, b)))]
                       for index, a in enumerate(members) for b in members[index + 1:]]
        groups.append({
            "members": members,
            "minimum_probability": min(pair_values),
            "mean_probability": sum(pair_values) / len(pair_values),
        })
    return sorted(groups, key=lambda x: x["members"])


def proposals(episodes, response, threshold, confidence_threshold, elapsed):
    answers = response.get("answers", {})
    ungrouped = sorted((x for x in episodes if x.group == "--" and not x.declined),
                       key=lambda x: x.episode_id)
    attachments = []
    for episode in ungrouped:
        answer = answers.get("attach_%s" % episode.episode_id, {})
        choice = answer.get("choice", "none")
        probability = float(answer.get("probabilities", {}).get(choice, 0.0))
        confidence = float(answer.get("confidence", 0.0))
        if choice != "none" and probability >= threshold and confidence >= confidence_threshold:
            attachments.append({
                "episode": episode.episode_id, "group": choice,
                "probability": probability, "confidence": confidence,
            })
    attached_ids = {item["episode"] for item in attachments}
    remaining = [x for x in ungrouped if x.episode_id not in attached_ids]
    usage = response.get("usage", {})
    input_tokens = int(usage.get("input_tokens", 0))
    return {
        "model": response.get("model", "unknown"),
        "threshold": threshold,
        "confidence_threshold": confidence_threshold,
        "attachments": attachments,
        "new_groups": accepted_pairs(remaining, answers, threshold),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": int(usage.get("output_tokens", 0)),
            "estimated_cost_usd": input_tokens * PRICE_PER_INPUT_TOKEN,
            "wall_seconds": elapsed,
        },
    }


def validate_prompt_scope(_episodes, scope):
    if not scope or scope.lower() == "all":
        raise ManifestError("--with-prompts requires a narrow --scope, never 'all'")
    if SENSITIVE_SCOPE.search(scope):
        raise ManifestError("refusing prompt egress for a sensitive scope")


def render(result, mode):
    print("JEV REGROUP · %s · model %s · threshold %.2f" %
          (mode, result["model"], result["threshold"]))
    if result["attachments"]:
        print("ATTACH TO EXISTING")
        for item in result["attachments"]:
            print("  %s -> %s  p=%.3f confidence=%.3f" %
                  (item["episode"], item["group"], item["probability"], item["confidence"]))
    if result["new_groups"]:
        print("NEW GROUPS")
        for item in result["new_groups"]:
            print("  %s  min_p=%.3f mean_p=%.3f" %
                  (",".join(item["members"]), item["minimum_probability"],
                   item["mean_probability"]))
    if not result["attachments"] and not result["new_groups"]:
        print("NO PROPOSED REGROUPING")
    usage = result["usage"]
    print("USAGE input=%d output=%d cost_usd=%.8f wall_seconds=%.3f" %
          (usage["input_tokens"], usage["output_tokens"], usage["estimated_cost_usd"],
           usage["wall_seconds"]))
    print("Proposal only. The ledger was not changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", default="-", help="manifest file, or - for stdin")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--terms-only", action="store_true", help="send terms and metadata (default)")
    mode.add_argument("--with-prompts", action="store_true", help="also send opening prompts")
    parser.add_argument("--scope", help="required narrow project substring with --with-prompts")
    parser.add_argument("--threshold", type=float, default=0.80)
    parser.add_argument("--confidence-threshold", type=float, default=0.50)
    parser.add_argument("--json", action="store_true", help="print machine-readable result")
    args = parser.parse_args()
    if not 0.0 <= args.threshold <= 1.0 or not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("thresholds must be between 0 and 1")
    try:
        text = sys.stdin.read() if args.manifest == "-" else open(
            args.manifest, "r", encoding="utf-8"
        ).read()
        episodes = parse_manifest(text)
        if args.with_prompts:
            validate_prompt_scope(episodes, args.scope)
        payload = build_request(episodes, args.with_prompts)
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise ManifestError("TYPESAFE_API_KEY is not set")
        started = time.monotonic()
        response = call_api(payload, key)
        result = proposals(episodes, response, args.threshold, args.confidence_threshold,
                           time.monotonic() - started)
    except (OSError, ValueError, RuntimeError) as exc:
        print("jev-regroup: %s" % exc, file=sys.stderr)
        return 1
    result["mode"] = "with-prompts" if args.with_prompts else "terms-only"
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        render(result, result["mode"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
