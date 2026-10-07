#!/usr/bin/env python3
"""Cut one turn out of a real transcript and scrub it into a test fixture.

    python3 scripts/make_fixture.py SESSION.jsonl TURN OUT.jsonl [--scrub WORD ...]

Keeps only the lines the hook reads (prompts, shell calls, edits, results,
final text), trims long outputs, and replaces home paths, emails, tokens and
any extra words you pass with --scrub. Read the result before committing it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import show_receipts as sr

HOME = str(Path.home())
SECRET = re.compile(r"(?:sk|ghp|gho|github_pat|xox[bap]|AKIA)[-_A-Za-z0-9]{12,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL = re.compile(r"https?://[^\s\"')]+")


def scrub(text: str, words: list[str]) -> str:
    text = text.replace(HOME, "/home/dev")
    text = re.sub(r"/(?:private/)?tmp/claude-\d+/[^\s\"']+", "/tmp/scratch", text)
    text = re.sub(r"/Users/[^/\s\"']+", "/home/dev", text)
    text = SECRET.sub("<redacted>", text)
    text = EMAIL.sub("dev@example.com", text)
    text = URL.sub("https://example.com", text)
    for w in words:
        text = re.sub(re.escape(w), "x" * min(len(w), 6), text, flags=re.I)
    return text


def _starts_turn(obj: dict) -> bool:
    if "payload" in obj:
        return obj.get("type") == "event_msg" and obj["payload"].get("type") == "task_started"
    msg = obj.get("message")
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain") or not isinstance(msg, dict):
        return False
    c = msg.get("content")
    if isinstance(c, str):
        return True
    kinds = {b.get("type") for b in c or [] if isinstance(b, dict)}
    return "text" in kinds and "tool_result" not in kinds


def cut(path: Path, turn: int) -> list[dict]:
    """Return the raw lines of `turn` (1-based), from its prompt to the next one."""
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    starts = [i for i, o in enumerate(lines) if _starts_turn(o)] + [len(lines)]
    return lines[starts[turn - 1] : starts[turn]] if turn < len(starts) else []


def slim(obj: dict, words: list[str]) -> dict | None:
    def s(v):
        if isinstance(v, str):
            return scrub(v, words)
        if isinstance(v, list):
            return [s(x) for x in v]
        if isinstance(v, dict):
            return {scrub(k, words): s(x) for k, x in v.items()}
        return v

    if "payload" in obj:
        p = obj["payload"]
        t = p.get("type")
        if obj["type"] == "event_msg" and t in ("task_started", "task_complete"):
            keep = {k: p.get(k) for k in ("type", "turn_id", "last_agent_message") if k in p}
            if keep.get("last_agent_message"):
                keep["last_agent_message"] = short_final(keep["last_agent_message"])
            return s({"type": obj["type"], "payload": keep})
        if obj["type"] == "event_msg" and t == "item_completed":
            it = p.get("item") or {}
            if it.get("type") == "CommandExecution":
                it = {k: it.get(k) for k in ("type", "command", "exit_code", "status", "aggregated_output")}
                if isinstance(it["command"], list) and it["command"]:
                    it["command"] = [*it["command"][:-1], check_segments(str(it["command"][-1]))]
                it["aggregated_output"] = (it["aggregated_output"] or "")[-200:]
                if not sr.command_categories(
                    it["command"][-1] if isinstance(it["command"], list) else str(it["command"])
                ):
                    return None
            elif it.get("type") == "FileChange":
                it = {
                    "type": "FileChange",
                    "status": it.get("status"),
                    "changes": {k: {} for k in it.get("changes") or {}},
                }
            else:
                return None
            return s({"type": obj["type"], "payload": {"type": t, "turn_id": p.get("turn_id"), "item": it}})
        if obj["type"] == "turn_context":
            return s({"type": "turn_context", "payload": {"cwd": p.get("cwd")}})
        return None
    msg = obj.get("message")
    if obj.get("type") not in ("user", "assistant") or not isinstance(msg, dict) or obj.get("isSidechain"):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        content = "(prompt removed)"
    if isinstance(content, list):
        new = []
        for b in content:
            if b.get("type") == "text":
                # narration is dropped; the prompt is replaced and the final answer restored in main()
                if obj["type"] == "user":
                    new.append({"type": "text", "text": "(prompt removed)"})
                else:
                    new.append({"type": "text", "text": ""})
            elif (
                b.get("type") == "tool_use"
                and b.get("name") == "Bash"
                and not sr.command_categories(str((b.get("input") or {}).get("command", "")))
            ):
                continue  # not a check, so the hook ignores it anyway
            elif b.get("type") == "tool_use" and b.get("name") in ("Bash", *sr._EDIT_TOOLS, *sr._DELEGATE_TOOLS):
                inp = b.get("input") or {}
                inp = {k: inp[k] for k in ("command", "file_path", "run_in_background") if k in inp}
                if "file_path" in inp and not str(inp["file_path"]).startswith(str(obj.get("cwd") or "")):
                    continue
                if "command" in inp:
                    inp["command"] = check_segments(str(inp["command"]))
                new.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": inp})
            elif b.get("type") == "tool_result":
                c = sr._text_of(b.get("content"))[-200:]
                new.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": b["tool_use_id"],
                        "is_error": bool(b.get("is_error")),
                        "content": c,
                    }
                )
        if not new:
            return None
        content = new
    out = {
        "type": obj["type"],
        "uuid": obj.get("uuid"),
        "cwd": obj.get("cwd"),
        "message": {"role": msg.get("role"), "content": content},
    }
    if obj.get("promptId"):
        out["promptId"] = obj["promptId"]
    res = obj.get("toolUseResult")
    if isinstance(res, dict) and "stdout" in res:
        out["toolUseResult"] = {
            "stdout": str(res.get("stdout"))[-200:],
            "stderr": "",
            "interrupted": bool(res.get("interrupted")),
        }
    return s(out)


def check_segments(cmd: str) -> str:
    """Keep only the parts of a shell command that are checks; the rest can be private."""
    parts = [p.strip() for p in sr._SPLIT_RE.split(cmd) if p.strip()]
    keep = [p for p in parts if sr.command_categories(p)]
    return " && ".join(keep) if len(keep) == len(parts) else " ; ".join([*keep, "true"])


def short_final(text: str) -> str:
    """First line plus the sentences that make claims; the rest is private chatter."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    claims = [c for c in sr.find_claims(text).values() if c != first]
    return "\n\n".join([first, *dict.fromkeys(claims)])


def _blocks(r: dict) -> list:
    c = (r.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("turn", type=int)
    ap.add_argument("out")
    ap.add_argument("--scrub", nargs="*", default=[])
    a = ap.parse_args()
    raw = cut(Path(a.session), a.turn)
    rows = [r for r in (slim(o, a.scrub) for o in raw) if r]
    used = {b["id"] for r in rows for b in _blocks(r) if b.get("type") == "tool_use"}
    for r in rows:
        if isinstance((r.get("message") or {}).get("content"), list):
            r["message"]["content"] = [
                b
                for b in r["message"]["content"]
                if not (b.get("type") == "tool_result" and b.get("tool_use_id") not in used)
                and not (b.get("type") == "text" and not b.get("text") and r["type"] == "assistant")
            ]
    # keep the full final answer, since that is what the hook reads
    final = sr.Transcript()
    pend: dict = {}
    for o in raw:
        (sr._parse_codex if "payload" in o else sr._parse_claude)(o, final, pend)
    for r in reversed(rows):
        if "payload" in r:
            break
    else:
        rows.append(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": scrub(short_final(final.last_assistant_text), a.scrub)}],
                },
            }
        )
    rows = [r for r in rows if "payload" in r or r["message"]["content"]]
    Path(a.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"wrote {len(rows)} lines to {a.out}")


if __name__ == "__main__":
    main()
