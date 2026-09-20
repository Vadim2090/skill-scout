#!/usr/bin/env python3
"""skill-scout scanner — the mechanical half of /skill-scout.

Finds session episodes that LOOK like non-trivial investigations, groups the
ones that recur across sessions, and prints a compact digest of evidence.

It deliberately does NOT decide whether anything deserves to be a skill, does
not name patterns, and never writes a skill file. That judgement is semantic
and belongs to the model reading this output.

Reads   ~/.claude/projects/*/*.jsonl        Claude Code transcripts
Cache   $SKILL_SCOUT_CACHE                  regenerable per-transcript features
Ledger  $SKILL_SCOUT_STATE/skill-scout-ledger.json   what was already proposed
"""
import argparse, calendar, hashlib, json, os, re, sys, tempfile, time
from collections import Counter
from pathlib import Path

CACHE_VERSION = 4          # bump whenever parse() or features() changes shape

HOME = Path.home()
PROJECTS = HOME / ".claude" / "projects"

# Durable: what was already proposed. Must outlive a reboot, so never a temp dir.
# Override with --state or SKILL_SCOUT_STATE when the default is not writable —
# a sandboxed agent often cannot write to its own home.
STATE = Path(os.environ.get("SKILL_SCOUT_STATE", str(HOME / ".skill-scout")))
LEDGER = STATE / "ledger.json"

# Regenerable: parsed transcripts. A temp dir is the right home; losing it costs
# one slow run. Override with --cache or SKILL_SCOUT_CACHE.
CACHE = Path(os.environ.get("SKILL_SCOUT_CACHE",
                            str(Path(tempfile.gettempdir()) / "skill-scout-cache")))

# Transcript dirs that are not real work: eval harnesses, scratchpads, temp checkouts.
EXCLUDE_DIR = re.compile(r"(aios-eval|-scratchpad|^-private-var-folders|^-private-tmp|^-tmp)")

def skill_dirs():
    """User scope, plus every `.claude/skills` in the working directory, its
    ancestors up to $HOME, and those ancestors' immediate children — which is how a
    sibling project's skills get indexed from inside another project. Add more with
    SKILL_SCOUT_SKILL_DIRS (colon-separated).

    This only feeds the "existing skills on these terms" hint. Under-reporting it
    pushes the reader toward creating a duplicate, which is the one thing this skill
    exists to prevent, so it is worth a few dozen stat calls.
    """
    found, seen = [], set()

    def add(d):
        if d.is_dir() and str(d) not in seen:
            seen.add(str(d))
            found.append(d)

    add(HOME / ".claude" / "skills")
    here = Path.cwd().resolve()
    chain = [here] + [p for p in here.parents if str(p).startswith(str(HOME))]
    for d in chain:
        add(d / ".claude" / "skills")
        try:
            for child in sorted(d.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    add(child / ".claude" / "skills")
        except OSError:
            pass
    for extra in os.environ.get("SKILL_SCOUT_SKILL_DIRS", "").split(":"):
        if extra.strip():
            add(Path(extra.strip()).expanduser())
    return found


# Correction markers. H1, recalibrated 2026-09-20 against 54 hand-labelled hits over
# the deduplicated in-window corpus (1036 prompts). The old single pattern scored 67%
# precision; these two score 94% at 92% recall ON THAT SET.
#
# Two groups, because a global position rule was measured and rejected: only 6 of 57
# hits opened a prompt or followed sentence-final punctuation, so anchoring everything
# would have destroyed recall. Anchoring helps only for short ambiguous tokens —
# "нет," is a correction at the start of a line and a noun in "2й карточки нет,".
#
# `это не` was dropped as a general marker: 9 of its 14 hits were ordinary prose or
# dictated copy, and it matched inside "неприемлемо", "неудобно", "ненужные". It
# survives only in front of a rejection verb.
#
# Read `corr` as a LOWER BOUND on explicit redirections. A sample of 30 non-matching
# prompts still contained 2-3 real ones, so corpus recall is roughly a third. Evidence
# when present; never an absence proof when zero — which is why its weight is 0.15,
# below the directly measured error count.
CORRECTION_ANY = re.compile(
    r"(?:^|\W)(?:"
    r"не надо\b|не нужно\b|неверн[оыаи]|неправильн|"
    r"почему ты\b|я не вижу\b|вместо этого\b|а не то что\b|"
    r"нет[\s,]+(?:это|так|не)\b|"
    r"это не (?:несет|несёт|даёт|дает|работает|правда|верно|нужно|единственн)|"
    r"(?:это|всё|все|тут|там)\s+(?:точно\s+)?не так\b|"
    r"actually\b|i meant\b|revert\b|undo that\b|you (?:were|are) wrong|"
    r"that'?s (?:not|wrong)|"
    r"don'?t (?:use|do|add|change|touch|write|include|mention|send|make|put|say|remove)\b"
    r")", re.I)

# Short tokens that only mean a correction when they open a sentence.
CORRECTION_ANCHORED = re.compile(r"(?:^|[.!?\n]\s*)(?:нет,|no,|стоп\b|не то\b)", re.I)


def is_correction(text):
    return bool(CORRECTION_ANY.search(text) or CORRECTION_ANCHORED.search(text))


STOP = set("""
the a an and or but if then than that this these those with without for from into onto over under
is are was were be been being do does did done doing have has had having can could should would will
shall may might must not no yes it its as at by of on to in out up down off about above below
what which who whom whose when where why how all any both each few more most other some such only
own same so too very just also then once here there our your their his her them they we you i me my
please make made need needs want use used using get got give gives take takes put puts let lets
file files folder line lines code run running ran now new old next last first second good ok okay
add added check checked fix fixed try tried see look show tell say write wrote read done help
didn don doesn isn wasn won can. etc via per one two three yes. no. thing things way ways
вариант варианта варианты будем будет буду убери уберем оставь оставим возьми смотри думаю
знаю знаешь помнишь кажется вроде похоже значит именно точно конечно наверное вообще вполне
и или но если то чем что это эти тот те для из в на по со от до над под при без через про
есть был была было были быть будет буду может можно нужно надо нельзя давай давайте сделай сделать
делать сделал сделала делаю делает почему зачем когда где как какой какая какие который которая
все всё весь вся уже еще ещё тут там вот так такой такая тоже также просто очень только же ли бы
не да нет мне меня мой моя мои тебя твой ты вы мы они он она оно себя свой своя свои чтобы потому
файл файлы файла папка папке строка строки код запусти проверь посмотри дай покажи скажи объясни
теперь давай ладно хорошо ок окей понял понятно верно точно кстати btw итак затем после перед
напиши написать пиши сообщение задача задачу задачи заведем добавь добавить добавляй удали удалить
нужный нужная новый новая новые старый разбери разберем обсудим объясни поясни вопрос ответ
claude code session sessions skill skills tool tools task tasks
""".split())

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}|[А-Яа-яЁё]{4,}|\b[0-9]{3}\b")
URL = re.compile(r"https?://\S+|\b[\w.-]+\.(?:com|org|net|io|ru|dev|app|so)\b\S*")

# User-role messages the harness writes, not the user: compaction preambles, local
# command output, interrupt notices. They are identical across sessions, so leaving
# them in manufactures clusters of "sessions that happened to compact".
SYNTHETIC = re.compile(
    r"^(?:this session is being continued|caveat: the messages below|"
    r"\[request interrupted|your task is to create a detailed summary|"
    r"api error|the user (?:opened|sent) the file|command output|"
    r"analysis:|<)", re.I)


def short_project(name):
    """Transcript dirs are the absolute path with separators replaced. Strip this
    machine's home prefix rather than a hardcoded username."""
    for pre in (re.sub(r"[/ ]", "-", str(HOME)) + "-", "-"):
        if name.startswith(pre):
            return name[len(pre):] or "root"
    return name or "root"


def ranked(counter, n=None):
    """Counter.most_common() breaks ties by insertion order, which follows set
    iteration order, which follows the process hash seed. Ties break on the key."""
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return items[:n] if n is not None else items


def parse(path):
    """-> [[ts, kind, *payload], ...] in file order.

    kinds: 'p' prompt (text) · 't' tool call (name, id) · 'r' tool result (id, is_error)
    Timestamps are per record, so any window can be applied later without re-reading.
    """
    events, ts = [], 0.0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            raw = d.get("timestamp")
            if raw:
                try:
                    ts = calendar.timegm(time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    pass
            t = d.get("type")
            if t == "assistant":
                for b in ((d.get("message") or {}).get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        events.append([ts, "t", b.get("name") or "?", b.get("id") or ""])
            elif t == "user":
                if d.get("isMeta"):
                    continue
                c = (d.get("message") or {}).get("content")
                cands = []
                if isinstance(c, str):
                    cands = [c]
                elif isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_result":
                            events.append([ts, "r", b.get("tool_use_id") or "",
                                           1 if b.get("is_error") else 0])
                        elif b.get("type") == "text":
                            cands.append(b.get("text") or "")
                for raw_text in cands:
                    txt = (raw_text or "").strip()
                    if not txt or SYNTHETIC.match(txt):
                        continue
                    if "Base directory for this skill" in txt:
                        continue
                    events.append([ts, "p", txt[:400]])
    return events


def features(events, since=0.0):
    """Every metric is computed over the window, not over the whole file."""
    ev = [e for e in events if e[0] >= since]
    prompts, tools = [], Counter()
    errors = max_retry = solo = solo_max = 0
    run_tool, run_len = None, 0
    of_id, failing = {}, set()          # H2: error state per TOOL, resolved by a success
    last_ts = 0.0
    for e in ev:
        kind = e[1]
        if kind == "p":
            prompts.append(e[2])
            solo, run_tool, run_len = 0, None, 0
            failing.clear()             # a new instruction ends any retry run
            last_ts = e[0]
        elif kind == "t":
            name, tid = e[2], e[3] if len(e) > 3 else ""
            tools[name] += 1
            of_id[tid] = name
            solo += 1                   # M3: tool CALLS, which is what SKILL.md claims
            solo_max = max(solo_max, solo)
            if name in failing and name == run_tool:
                run_len += 1
            else:
                run_tool, run_len = name, 1
            max_retry = max(max_retry, run_len)
            last_ts = max(last_ts, e[0])
        elif kind == "r":
            tid = e[2]
            name = of_id.get(tid)
            if len(e) > 3 and e[3]:
                errors += 1
                if name:
                    failing.add(name)
            elif name:
                failing.discard(name)   # it worked — whatever came before is not a retry
    prompts = prompts[:60]
    text = URL.sub(" ", "\n".join(prompts))
    raw = Counter(w.lower() for w in TOKEN.findall(text) if w.lower() not in STOP)
    # A term the user used once is vocabulary; a term used twice is the subject.
    terms = dict(ranked(Counter({w: c for w, c in raw.items() if c >= 2}) or raw, 45))
    topic = next((p for p in prompts[:6] if len(p) >= 25), prompts[0] if prompts else "")
    return {
        "n_prompts": len(prompts),
        "topic": topic[:200],
        "fp": hashlib.sha1("\n".join(prompts[:5]).encode()).hexdigest()[:16],
        "last_ts": last_ts,
        "terms": terms,
        "tools": dict(ranked(tools, 8)),
        "n_distinct_tools": len(tools),
        "errors": errors,
        "max_retry": max_retry if max_retry > 1 else 0,   # H2 floor, partial
        "solo_max": solo_max,
        "corrections": sum(1 for p in prompts if is_correction(p)),
    }


def score(f):
    E = min(1.0, f["errors"] / 8.0)
    R = min(1.0, f["max_retry"] / 4.0)
    C = min(1.0, f["corrections"] / 3.0)
    S = min(1.0, f["solo_max"] / 60.0)
    D = min(1.0, f["n_distinct_tools"] / 12.0)
    # Weights, revised 2026-09-20: errors are counted, corrections are inferred at
    # roughly a third recall, so the measured signal carries more than the inferred one.
    return round(100 * (0.40 * E + 0.20 * R + 0.15 * C + 0.15 * S + 0.10 * D))


def cached(path, rescan=False):
    """Cache the parsed EVENT STREAM, not the features. Features depend on the
    window; events do not, so one parse serves any --days."""
    st = path.stat()
    cf = CACHE / (hashlib.sha1(str(path).encode()).hexdigest()[:20] + ".json")
    if not rescan and cf.exists():
        try:
            c = json.loads(cf.read_text())
            if (c.get("v") == CACHE_VERSION and c.get("size") == st.st_size
                    and c.get("mtime") == int(st.st_mtime)):
                return c["events"]
        except Exception:
            pass
    events = parse(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(CACHE), 0o700)        # it holds prompt text
    except OSError:
        pass
    tmp = cf.with_suffix(".tmp")
    tmp.write_text(json.dumps({"v": CACHE_VERSION, "size": st.st_size,
                               "mtime": int(st.st_mtime), "events": events}))
    os.replace(str(tmp), str(cf))
    return events


MIN_TERMS = 6      # a session with a thinner vocabulary cannot anchor a cluster
MIN_SHARED = 4     # ... and a cluster needs this many shared terms, not just a ratio


def overlap(a, b):
    if len(a) < MIN_TERMS or len(b) < MIN_TERMS:
        return 0.0
    shared = len(a & b)
    if shared < MIN_SHARED:
        return 0.0
    return shared / min(len(a), len(b))


def ledger_overlap(a, b):
    """C3: overlap() refuses sets under MIN_TERMS, which silently made every ledger
    entry with fewer than 6 terms unmatchable — including the 4-term example the
    documentation gave. Ledger matching has no such floor."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def load_ledger():
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text())
        except Exception:
            # M4: never silently start a fresh ledger over a corrupt one — a declined
            # candidate would come back with no trace of why it vanished.
            spoiled = LEDGER.with_name(LEDGER.name + ".corrupt-" + time.strftime("%Y%m%d%H%M%S"))
            LEDGER.rename(spoiled)
            print("warn: ledger unreadable, moved to %s" % spoiled, file=sys.stderr)
    return {"entries": []}


def record(status, terms, note):
    clean = sorted(set(t.strip().lower() for t in terms if t.strip()))[:12]
    if not clean:
        # M5: an entry with no terms matches nothing. Writing it and reporting success
        # is worse than refusing — it looks recorded and suppresses nothing.
        print("refused: --record needs --terms, and they are the suppression key.\n"
              "         Copy the cluster's `shared terms` line verbatim.", file=sys.stderr)
        return 1
    L = load_ledger()
    L["entries"].append({
        "date": time.strftime("%Y-%m-%d"),
        "status": status,
        "terms": clean,
        "note": note[:300],
    })
    try:
        STATE.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print("cannot write the ledger at %s (%s).\n"
              "Pass --state DIR or set SKILL_SCOUT_STATE to a writable directory."
              % (STATE, exc), file=sys.stderr)
        return 1
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(L, indent=1, ensure_ascii=False))
    os.replace(str(tmp), str(LEDGER))
    print("ledger: recorded %s with %d term(s) (%d entries) -> %s"
          % (status, len(clean), len(L["entries"]), LEDGER))
    return 0


def skill_index():
    """name -> searchable text (name + frontmatter description only, not the body)."""
    idx = {}
    for d in skill_dirs():
        for s in sorted(d.iterdir()):
            md = s / "SKILL.md"
            if md.is_file() and not s.name.startswith("."):
                try:
                    head = md.read_text(errors="replace")[:3000]
                except Exception:
                    continue
                parts = head.split("---")
                fm = parts[1] if len(parts) > 2 else head[:800]
                idx[s.name] = (s.name + " " + fm).lower()
    return idx


def excerpts(path, since, limit=3):
    """M1: the digest's counters say a session was hard; this says HOW.

    Re-reads the transcript instead of caching error text — evidence is asked for a
    handful of episodes at a time, and the failure bodies would multiply the cache.
    Returns (failures, redirections): each failure is the call that broke and what
    came back, each redirection is the prompt that changed course.
    """
    calls, fails, redirs = {}, [], []
    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        return [], []
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            raw = d.get("timestamp")
            ts = 0.0
            if raw:
                try:
                    ts = calendar.timegm(time.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    ts = 0.0
            if ts and ts < since:
                continue
            t = d.get("type")
            if t == "assistant":
                for b in ((d.get("message") or {}).get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        what = (inp.get("command") or inp.get("file_path")
                                or inp.get("pattern") or inp.get("url") or "")
                        calls[b.get("id") or ""] = (b.get("name") or "?", str(what))
            elif t == "user" and not d.get("isMeta"):
                c = (d.get("message") or {}).get("content")
                if isinstance(c, str):
                    if c.strip() and not SYNTHETIC.match(c.strip()) and is_correction(c):
                        redirs.append(c.strip())
                elif isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_result" and b.get("is_error"):
                            name, what = calls.get(b.get("tool_use_id") or "", ("?", ""))
                            body = b.get("content")
                            if isinstance(body, list):
                                body = " ".join(x.get("text", "") for x in body
                                                if isinstance(x, dict))
                            fails.append((name, str(what), str(body or "")))
                        elif b.get("type") == "text":
                            txt = (b.get("text") or "").strip()
                            if txt and not SYNTHETIC.match(txt) and is_correction(txt):
                                redirs.append(txt)
    # One line per distinct failing call: repeats of the same command teach nothing new.
    seen, uniq = set(), []
    for name, what, body in fails:
        k = (name, what[:80])
        if k in seen:
            continue
        seen.add(k)
        uniq.append((name, what, body))
    return uniq[:limit], redirs[:limit]


def one_line(x, n):
    return " ".join(str(x).split())[:n]


def show_block(members, top, days, tag, idx, since=None, deep=False):
    print("\n── %s · %d sessions over %d day(s) · top score %d %s"
          % (tag, len(members), days, members[0]["score"], "─" * 18))
    print("   shared terms (%d): %s" % (len(top), ", ".join(top)))
    shown = members if deep else members[:5]
    for m in shown:
        f = m["f"]
        print("   %s %s %-24s s%-3d err %-3d retry %-2d corr %-2d solo %-3d %s"
              % (m["id"], m["date"], m["proj"][:24], m["score"], f["errors"], f["max_retry"],
                 f["corrections"], f["solo_max"], ",".join(list(f["tools"])[:3])))
        print("        \u201c%s\u201d" % one_line(f["topic"], 130))
        if deep and m.get("path"):
            fails, redirs = excerpts(m["path"], since if since is not None else 0.0)
            for name, what, body in fails:
                print("        ✗ %s %s" % (name, one_line(what, 96)))
                print("          → %s" % one_line(body, 150))
            for r in redirs:
                print("        ↩ \u201c%s\u201d" % one_line(r, 150))
            if not fails and not redirs:
                print("        (no failures or redirections in window — score came from "
                      "unattended work, not from getting stuck)")
    if not deep and len(members) > 5:
        print("   … +%d more session(s) — `--evidence %s` prints all of them with excerpts"
              % (len(members) - 5, ",".join(m["id"] for m in members)))
    hits = sorted(((sum(1 for t in top if t in body), n) for n, body in idx.items()), reverse=True)
    hits = [n for c, n in hits if c >= 3][:5]
    print("   existing skills on these terms: " + (", ".join(hits) if hits else "none"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--min-score", type=int, default=45)
    ap.add_argument("--min-sessions", type=int, default=2)
    ap.add_argument("--sim", type=float, default=0.32)
    ap.add_argument("--ambient", type=float, default=0.30,
                    help="drop terms present in more than this share of episodes — they are "
                         "the vocabulary of the job, not of a pattern")
    ap.add_argument("--scope", default="all", help="all | substring of the project dir name")
    ap.add_argument("--max-clusters", type=int, default=5)
    ap.add_argument("--max-singles", type=int, default=3)
    ap.add_argument("--single-score", type=int, default=70,
                    help="a lone session must clear this, and have real failures, to count "
                         "as a deep one-off investigation")
    ap.add_argument("--rescan", action="store_true")
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing at all when no cluster clears the bar. For /finish, "
                         "where a header on every wrap-up is noise.")
    ap.add_argument("--record", choices=["proposed", "accepted", "declined"])
    ap.add_argument("--list-ledger", action="store_true")
    ap.add_argument("--state", help="directory holding ledger.json (default: $SKILL_SCOUT_STATE)")
    ap.add_argument("--cache", help="directory for parsed transcripts (default: $SKILL_SCOUT_CACHE)")
    ap.add_argument("--manifest", action="store_true",
                    help="one line per episode + the lexical group it landed in. This is the "
                         "input for the semantic pass: small enough to read, complete enough "
                         "to regroup.")
    ap.add_argument("--evidence", default="",
                    help="comma-separated episode ids — print one evidence block for exactly "
                         "those, whatever the lexical pass thought. This is how a semantically "
                         "merged cluster gets its evidence.")
    ap.add_argument("--terms", default="")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    # Flags beat the environment, so a test never needs `export` — which is also what
    # keeps a test command statically analysable, and therefore silent under auto mode.
    global STATE, CACHE, LEDGER
    if a.state:
        STATE = Path(a.state).expanduser()
        LEDGER = STATE / "ledger.json"
    if a.cache:
        CACHE = Path(a.cache).expanduser()

    if a.record:
        return record(a.record, a.terms.split(","), a.note)

    if a.list_ledger:
        L = load_ledger()
        if not L["entries"]:
            print("ledger empty: %s" % LEDGER)
        for x in L["entries"]:
            print("%s  %-9s %-46s %s" % (x["date"], x["status"],
                                         ",".join(x["terms"])[:46], x["note"][:60]))
        return

    cutoff = time.time() - a.days * 86400
    files = []
    if PROJECTS.is_dir():
        for pd in sorted(PROJECTS.iterdir()):
            if not pd.is_dir() or EXCLUDE_DIR.search(pd.name):
                continue
            if a.scope != "all" and a.scope.lower() not in pd.name.lower():
                continue
            for jl in sorted(pd.glob("*.jsonl")):
                try:
                    # mtime is only a prefilter: a file last written before the window
                    # cannot hold an in-window event. What counts is decided per record.
                    if jl.stat().st_mtime >= cutoff:
                        files.append((pd.name, jl))
                except OSError:
                    pass
    files.sort(key=lambda x: (x[0], x[1].name))

    eps, seen_fp, spilled = [], {}, 0
    for proj, jl in files:
        try:
            events = cached(jl, a.rescan)
        except Exception as exc:
            print("warn: %s: %s" % (jl.name, exc), file=sys.stderr)
            continue
        if events and events[0][0] < cutoff:
            spilled += 1
        f = features(events, since=cutoff)
        if f["n_prompts"] < 2 or not f["terms"]:
            continue
        s = score(f)
        if s < a.min_score:
            continue
        # A resumed/forked session replays the same opening. Keep the richest copy.
        key = f["fp"]
        e = {
            "id": f["fp"][:6],
            "path": str(jl),
            "proj": short_project(proj),
            "date": time.strftime("%m-%d", time.localtime(f["last_ts"] or jl.stat().st_mtime)),
            "score": s, "f": f, "terms": set(f["terms"].keys()),
        }
        if key in seen_fp:
            if s > seen_fp[key]["score"]:
                seen_fp[key].update(e)
            continue
        seen_fp[key] = e
        eps.append(e)
    eps.sort(key=lambda x: (-x["score"], x["id"]))

    # Ambient vocabulary (your product name, "linkedin", "sheet") glues unrelated sessions
    # together. Anything common across the corpus carries no grouping signal.
    ambient = set()
    if len(eps) >= 10:
        df = Counter()
        for e in eps:
            for t in e["terms"]:
                df[t] += 1
        ambient = {t for t, c in df.items() if c / len(eps) > a.ambient}
        for e in eps:
            e["terms"] = e["terms"] - ambient

    used, clusters = set(), []
    for i, e in enumerate(eps):
        if i in used:
            continue
        group = [i]
        used.add(i)
        for j, o in enumerate(eps):
            if j not in used and overlap(e["terms"], o["terms"]) >= a.sim:
                group.append(j)
                used.add(j)
        clusters.append(group)

    led = load_ledger()
    suppressed, out, singles = 0, [], []
    for g in clusters:
        members = [eps[i] for i in g]
        shared = Counter()
        for m in members:
            for t in sorted(m["terms"]):
                shared[t] += 1
        need = 2 if len(members) > 1 else 1
        top = [t for t, c in ranked(shared, 60) if c >= need and t not in ambient][:8] or \
              [t for t, _ in ranked(shared, 6)]
        if not a.no_ledger and any(ledger_overlap(set(top), set(x.get("terms", []))) >= 0.6
                                   for x in led["entries"]):
            suppressed += 1
            continue
        days = len(set(m["date"] for m in members))
        if len(members) >= a.min_sessions and days >= 2:
            out.append((members, top, days))
        elif members[0]["score"] >= a.single_score and members[0]["f"]["errors"] >= 6:
            singles.append((members, top, days))

    if a.manifest:
        led_m = load_ledger() if not a.no_ledger else {"entries": []}
        where, muted = {}, set()
        for n, g in enumerate(clusters, 1):
            members = [eps[i] for i in g]
            shared = Counter()
            for m in members:
                for t in sorted(m["terms"]):
                    shared[t] += 1
            need = 2 if len(members) > 1 else 1
            ctop = [t for t, c in ranked(shared, 60) if c >= need][:8] or \
                   [t for t, _ in ranked(shared, 6)]
            hushed = any(ledger_overlap(set(ctop), set(x.get("terms", []))) >= 0.6
                         for x in led_m["entries"])
            tag = ("R%d" % n) if len(g) > 1 else "--"
            for i in g:
                where[eps[i]["id"]] = tag
                if hushed:
                    muted.add(eps[i]["id"])
        print("MANIFEST  %d episodes · %dd · [Rn] = lexical group, [--] = ungrouped%s"
              % (len(eps), a.days, ", ~ = already declined in the ledger" if muted else ""))
        print("Regroup by MEANING. The lexical pass only sees shared words.")
        for e in eps:
            f = e["f"]
            tag = where.get(e["id"], "--")
            if e["id"] in muted:
                tag = "~" + tag
            print("[%-3s] %s %s %-20s s%-3d e%-2d/r%d/c%d  %s"
                  % (tag, e["id"], e["date"], e["proj"][:20], e["score"],
                     f["errors"], f["max_retry"], f["corrections"], one_line(f["topic"], 88)))
            print("       terms: %s" % ", ".join(sorted(e["terms"])[:9]))
        return 0

    idx = skill_index()
    if a.evidence:
        want = [x.strip() for x in a.evidence.split(",") if x.strip()]
        members = [e for e in eps if e["id"] in want]
        missing = [w for w in want if w not in {e["id"] for e in members}]
        if missing:
            print("unknown id(s): %s — rerun --manifest with the same --days/--min-score"
                  % ", ".join(missing))
        if not members:
            return
        members.sort(key=lambda m: -m["score"])
        shared = Counter()
        for m in members:
            for t in sorted(m["terms"]):
                shared[t] += 1
        need = 2 if len(members) > 1 else 1
        top = [t for t, c in ranked(shared, 60) if c >= need][:8] or \
              [t for t, _ in ranked(shared, 6)]
        days = len(set(m["date"] for m in members))
        show_block(members, top, days, "MERGED", idx, since=cutoff, deep=True)
        print("\n(Evidence only. Nothing has been proposed or created.)")
        return

    # Quiet means "nothing would have been PRINTED", not "nothing was found":
    # --max-singles 0 leaves the singles list populated but shows none of it.
    if a.quiet and not out[:a.max_clusters] and not singles[:a.max_singles]:
        return 0
    print("SCAN  %dd · %d transcripts · %d episodes over score %d · %d recurring · %d one-off"
          % (a.days, len(files), len(eps), a.min_score, len(out), len(singles)))
    if spilled:
        print("WINDOW  %d transcript(s) reach back before the window; only in-window "
              "events were counted" % spilled)
    if ambient:
        print("AMBIENT  %d term(s) ignored as job vocabulary: %s"
              % (len(ambient), ", ".join(sorted(ambient)[:12])))
    if suppressed:
        print("LEDGER  %d cluster(s) suppressed as already proposed — %s" % (suppressed, LEDGER))
    if not out and not singles:
        print("\nNothing over threshold. No proposal.")
        return

    def show(members, top, days, tag):
        show_block(members, top, days, tag, idx, since=cutoff)

    for n, (members, top, days) in enumerate(out[:a.max_clusters], 1):
        show(members, top, days, "R%d RECURRING" % n)
    for n, (members, top, days) in enumerate(singles[:a.max_singles], 1):
        show(members, top, days, "S%d ONE-OFF" % n)
    print("\n(Evidence only. Nothing has been proposed or created.)")


if __name__ == "__main__":
    sys.exit(main() or 0)
