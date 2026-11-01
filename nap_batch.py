#!/usr/bin/env python3
"""nap_batch.py — answer memo's pending naps with an LLM, level by level.

Usage:  MEMORY_DIR=... ANTHROPIC_API_KEY=... nap_batch.py [--max-level L] [--dry] [--model M]

Loops: `memo nap --json --all` → for each node (lowest level first) build a
prompt from its children + v1 hints → one API call → `memo nap <node> "text"`.
Re-queries after each pass because answering a level makes the next one
answerable. --max-level caps how high the script goes (leave the era-level
nodes to the agent). Only stdlib; uses urllib for the Messages API.
"""
import json, os, re, subprocess, sys, urllib.request

MEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memo")
MODEL = "claude-opus-5"
LIMIT = 280

SYSTEM = """You compress an AI agent's memory log for its own later recall.
Write ONE line, at most %d bytes UTF-8, that a future session would grep for:
concrete names, paths, numbers, verdicts. Dense telegraphic style, '|' or ';'
between items, no preamble, no quotes around it. Dates as MM-DD. Keep the
words that identify things (repo names, model ids, hosts, people). Drop
process narrative. If hints (older summaries of the same period) are given,
reuse their vocabulary but only for facts present in the children.""" % LIMIT


def memo(*args):
    return subprocess.run([MEMO, *args], capture_output=True, text=True, check=True).stdout


def ask(prompt, model, key):
    body = {"model": model, "max_tokens": 300, "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)
    return "".join(c.get("text", "") for c in out["content"]).strip().splitlines()[0]


def build_prompt(n):
    lines = ["Scope: %s. Level %d node covering %s .. %s (%d memories)." %
             (n["scope"], n["level"], n["span"]["from_ts"][:10], n["span"]["to_ts"][:10], n["span"]["leaves"])]
    lines.append("\nChildren:")
    for c in n["children"]:
        tag = "#%s %s" % (c.get("id", c.get("node", "")[:6]), c.get("ts", "")[:10])
        lines.append("- %s %s" % (tag, c["text"]))
    hints = []
    for h in n.get("hints", []):  # "#lo-hi text": keep hints of similar span, not whole-history ones
        m = re.match(r"#(\d+)-(\d+)\s", h)
        if not m or int(m.group(2)) - int(m.group(1)) + 1 <= 4 * n["span"]["leaves"]:
            hints.append(h)
    if hints:
        lines.append("\nHints (older summaries of this period):")
        lines += ["- " + h for h in hints]
    lines.append("\nWrite the one-line summary now.")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    model = args[args.index("--model") + 1] if "--model" in args else MODEL
    max_level = int(args[args.index("--max-level") + 1]) if "--max-level" in args else 99
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and not dry:
        sys.exit("ANTHROPIC_API_KEY not set (use --dry to preview prompts)")
    done = 0
    while True:
        pend = json.loads(memo("nap", "--json", "--all") or "[]")
        pend = [n for n in pend if n["level"] <= max_level]
        if not pend:
            break
        pend.sort(key=lambda n: (n["level"], n["span"]["from_ts"]))
        for n in pend:
            prompt = build_prompt(n)
            if dry:
                print("=" * 60, "\n", prompt, "\n")
                continue
            text = ask(prompt, model, key)
            while len(text.encode()) > LIMIT:
                text = ask(prompt + "\n\nToo long (%d bytes). Shorten to under %d bytes." % (len(text.encode()), LIMIT), model, key)
            memo("nap", n["node"], text)
            done += 1
            print("[%d] L%d %s %s: %s" % (done, n["level"], n["scope"], n["node"][:6], text))
        if dry:
            break
    print("done: %d naps" % done)


if __name__ == "__main__":
    main()
