#!/usr/bin/env python3
"""Precision/recall of the correction markers against a hand-labelled set.

`corr` carries part of every episode score, so its marker set is a measurement
instrument and has to be calibrated like one. This scores the CURRENT markers in
scan-sessions.py against labels fixed on 2026-09-20.

The fixture stores SHA1 prefixes only, never prompt text, so it is safe to publish.
The flip side: it can only run on the machine whose transcripts were labelled.
Elsewhere it reports "0 of 54 labelled prompts found" and exits 0.

    python3 tests/correction-precision.py [--min-precision 90] [--min-recall 85]
"""
import argparse, hashlib, importlib.util, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "scan-sessions.py"
LABELS = HERE / "correction-labels.json"


def load_scanner():
    spec = importlib.util.spec_from_file_location("scan_sessions", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["scan-sessions"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


def corpus(m, days):
    cut = time.time() - days * 86400
    seen, out = set(), []
    for pd in sorted(m.PROJECTS.iterdir()):
        if not pd.is_dir() or m.EXCLUDE_DIR.search(pd.name):
            continue
        for jl in sorted(pd.glob("*.jsonl")):
            try:
                if jl.stat().st_mtime < cut:
                    continue
            except OSError:
                continue
            for e in m.cached(jl):
                if e[0] >= cut and e[1] == "p":
                    k = e[2][:120]
                    if k not in seen:
                        seen.add(k)
                        out.append(e[2])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--min-precision", type=float, default=90.0)
    ap.add_argument("--min-recall", type=float, default=85.0)
    a = ap.parse_args()

    m = load_scanner()
    lab = json.loads(LABELS.read_text())
    T, F = set(lab["true"]), set(lab["false"])
    found = {hashlib.sha1(p.encode()).hexdigest()[:8]: p for p in corpus(m, a.days)}
    have = (T | F) & set(found)
    if not have:
        print("0 of %d labelled prompts found in this corpus — not this machine, skipping."
              % len(T | F))
        return 0

    tp = sum(1 for h in T if h in found and m.is_correction(found[h]))
    fp = sum(1 for h in F if h in found and m.is_correction(found[h]))
    seen_t = sum(1 for h in T if h in found)
    precision = 100.0 * tp / (tp + fp) if tp + fp else 100.0
    recall = 100.0 * tp / seen_t if seen_t else 100.0
    print("labelled prompts present: %d of %d (%d true, %d false)"
          % (len(have), len(T | F), seen_t, sum(1 for h in F if h in found)))
    print("precision %.0f%% (floor %.0f)   recall %.0f%% (floor %.0f)"
          % (precision, a.min_precision, recall, a.min_recall))

    bad = 0
    for h in sorted(F):
        if h in found and m.is_correction(found[h]):
            print("  FP %s  %s" % (h, found[h][:90].replace("\n", " ")))
    for h in sorted(T):
        if h in found and not m.is_correction(found[h]):
            print("  FN %s  %s" % (h, found[h][:90].replace("\n", " ")))
    if precision < a.min_precision:
        print("FAIL precision"); bad = 1
    if recall < a.min_recall:
        print("FAIL recall"); bad = 1
    print("OK" if not bad else "FAILED")
    return bad


if __name__ == "__main__":
    sys.exit(main())
