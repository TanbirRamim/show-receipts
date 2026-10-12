#!/usr/bin/env python3
"""show-receipts: a Stop hook that makes coding agents prove "tests pass".

When the agent ends its turn saying the tests, the build or the linter pass,
this hook reads the session transcript and looks for a matching command that
ran after the last code edit and exited 0. If there is none, the stop is
blocked and the agent is told which claim has no receipt.

Works with Claude Code and Codex transcripts. Standard library only.
When in doubt it allows: its own errors never block a stop.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pwd
except ImportError:  # Windows
    pwd = None

__version__ = "0.3.4"

CONFIG_NAME = ".show-receipts.json"
CATEGORIES = ("tests", "build", "lint")

# --------------------------------------------------------------------------
# Claims: what the agent says at the end of its turn
# --------------------------------------------------------------------------

_OK = r"(?:pass(?:es|ed|ing)?|succeed(?:s|ed)?|green|work(?:s|ing)?|happy)"
_FILL = r"(?:\s+(?:are|is|now|still|all|both|also|fully|again|locally|cleanly|successfully))*"
# The sentence ends here, or only "now"/"again"/"locally" follows. Keeps
# "everything passes" from matching "everything passes through the proxy".
_END = r"(?=\s*(?:[.!,;:)]|$|\s+(?:now|again|locally)\b))"
_TEST_TOOL_NAMES = r"(?:pytest|jest|vitest|mocha|rspec|phpunit|cargo\s+test|go\s+test|npm\s+test|nextest)"
_LINT_TOOL_NAMES = r"(?:lint(?:er)?|tsc|clippy|ruff|eslint|mypy|pyright|biome|golangci-lint|shellcheck)"

CLAIM_PATTERNS: dict[str, list[str]] = {
    "tests": [
        rf"\b(?:the\s+)?(?:new\s+|existing\s+|unit\s+|integration\s+|e2e\s+)?(?:tests?|specs?){_FILL}\s+{_OK}\b",
        rf"\b(?:whole\s+|full\s+|entire\s+)?(?:test\s+|spec\s+)?suite{_FILL}\s+{_OK}\b",
        rf"\b{_TEST_TOOL_NAMES}{_FILL}\s+{_OK}\b",
        r"\b\d+\s+(?:tests?\s+)?passed\b",
        r"\b\d+\s*/\s*\d+\s+tests?\s+pass",
        rf"\btests\b.*\bthey{_FILL}\s+{_OK}\b",
        r"\b(?:no|zero|0)\s+test\s+failures\b",
        rf"^(?:it'?s\s+)?all\s+green{_END}",
        rf"\beverything{_FILL}\s+(?:pass(?:es|ing)?|green){_END}",
        r"\bverified\s+(?:that\s+)?(?:the\s+)?(?:fix|change|patch)\s+works\b",
        r"\bverified\s+(?:it|the\s+(?:fix|change|patch))\s+with\s+(?:the\s+)?tests\b",
    ],
    "build": [
        rf"\bbuild(?:s)?{_FILL}\s+(?:{_OK}|fine|cleanly|successfully|without\s+(?:errors|warnings))\b",
        r"\bcompiles\s+(?:cleanly|successfully|fine|without\s+(?:errors|warnings))\b",
        rf"\b(?:it|code|project|crate|everything)\s+(?:now\s+)?compiles{_END}",
    ],
    "lint": [
        rf"\b(?:lint(?:er|ing)?|type[\s-]?checks?|typecheck(?:ing)?|clippy|ruff|eslint|mypy|pyright){_FILL}\s+(?:{_OK}|clean|cleanly|fine)\b",
        r"\b(?:no|0|zero)\s+(?:lint|type|clippy|eslint|mypy|tsc)\s+(?:errors|warnings|issues|problems)\b",
        rf"\b{_LINT_TOOL_NAMES}\s+(?:reports?\s+|shows?\s+|finds\s+)?(?:no|0|zero)\s+(?:errors|warnings|issues|problems)\b",
    ],
}
_CLAIM_RE = {k: [re.compile(p, re.I) for p in v] for k, v in CLAIM_PATTERNS.items()}

# A sentence with any of these is not a confident claim about this session,
# so we leave it alone. False positives are worse than misses.
_HEDGE_RE = re.compile(
    r"\b(?:not|never|cannot|can't|couldn't|didn't|don't|doesn't|won't|isn't|aren't|wasn't|weren't|haven't|hasn't|hadn't|"
    r"unable|fail(?:s|ed|ing|ure|ures)?|broken|skip(?:ped)?|"
    r"should|would|could|might|may|will|if|once|until|unless|whether|"
    r"expect(?:ed|s)?|assum\w*|hopefully|probably|likely|seems?|"
    r"make\s+sure|verify|confirm|ensure|(?:make|get|keep|turn)\s+(?:the|it|them|everything|all)|"
    r"you|your|ci|pipeline|actions|"
    r"previously|earlier|before|were|was|used\s+to|"
    r"reports?|reported|says?|said|claims?|claimed|according)\b",
    re.I,
)
# Phrases that contain a hedge word but are still clean results.
_HEDGE_SAFE_RE = re.compile(
    r"\b(?:0|zero|no)\s+(?:test\s+)?(?:failures?|failed|failing|errors?)\b|\bfailed\s*[:=]\s*0\b"
    rf"|\b{_LINT_TOOL_NAMES}\s+reports?\s+(?:no|0|zero)\s+(?:errors|warnings|issues|problems)\b",
    re.I,
)
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.S)


def _sentences(text: str) -> Iterator[str]:
    text = _FENCE_RE.sub(" ", text)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(">"):
            continue
        line = line.replace("`", "").replace("*", "")
        line = re.sub(r'"[^"]*"|\u201c[^\u201d]*\u201d', " ", line)  # quoted text is not a claim
        for sent in re.split(r"(?<=[.!?;])\s+", line):
            yield sent.strip()


def find_claims(text: str, categories: Iterable[str] = CATEGORIES) -> dict[str, str]:
    """Return {category: sentence} for confident pass claims in text."""
    found: dict[str, str] = {}
    for sent in _sentences(text or ""):
        if sent.endswith("?"):
            continue
        if _HEDGE_RE.search(_HEDGE_SAFE_RE.sub(" ", sent)):
            continue
        for cat in categories:
            if cat in found:
                continue
            if any(r.search(sent) for r in _CLAIM_RE.get(cat, [])):
                found[cat] = sent
    return found


# --------------------------------------------------------------------------
# Receipts: which commands count as proof
# --------------------------------------------------------------------------

_WRAPPERS = {
    "sudo",
    "time",
    "env",
    "uvx",
    "nice",
    "nohup",
    "command",
    "exec",
    "npx",
    "bunx",
    "pnpx",
    "xvfb-run",
    "dotenv",
    "caffeinate",
}
_RUN_WRAPPERS = {  # "<tool> run|exec <cmd>"
    "uv",
    "opam",
    "poetry",
    "pipenv",
    "pdm",
    "hatch",
    "rye",
    "bundle",
    "pixi",
    "conda",
    "mise",
}
_INTERPRETERS = re.compile(r"^(?:python[\d.]*|py|node|bash|sh|zsh|ruby|php|perl|deno|bun|tsx|ts-node)$")
_RUNNERS = {
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "deno",
    "go",
    "cargo",
    "mvn",
    "mvnw",
    "./mvnw",
    "gradle",
    "gradlew",
    "./gradlew",
    "dotnet",
    "swift",
    "mix",
    "rake",
    "make",
    "gmake",
    "just",
    "task",
    "nx",
    "turbo",
    "zig",
    "flutter",
    "dart",
    "bazel",
    "bazelisk",
    "sbt",
    "lein",
    "stack",
    "cabal",
    "composer",
    "tox",
    "nox",
    "hatch",
    "xcodebuild",
    "pre-commit",
    "lerna",
    "rush",
    "invoke",
    "poe",
    "ctest",
    "meson",
    "ninja",
    "cmake",
    "elixir",
    "rails",
    "bin/rails",
    "dune",
}
_TEST_TOOLS = {
    "pytest",
    "py.test",
    "unittest",
    "nose2",
    "jest",
    "vitest",
    "mocha",
    "ava",
    "tap",
    "rspec",
    "phpunit",
    "pest",
    "ctest",
    "tox",
    "nox",
    "nextest",
    "karma",
    "playwright",
    "cypress",
    "bats",
    "busted",
    "prove",
    "minitest",
    "testem",
    "jasmine",
    "uvu",
    "gotestsum",
    "behave",
    "cucumber-js",
    "elm-test",
}
_BUILD_TOOLS = {
    "tsc",
    "webpack",
    "vite",
    "esbuild",
    "rollup",
    "parcel",
    "next",
    "nuxt",
    "astro",
    "gcc",
    "g++",
    "clang",
    "clang++",
    "cc",
    "javac",
    "rustc",
    "swiftc",
    "xcodebuild",
    "tsup",
    "unbuild",
    "wasm-pack",
    "docker",
    "hugo",
    "mkdocs",
    "sphinx-build",
}
_LINT_TOOLS = {
    "ruff",
    "flake8",
    "pylint",
    "mypy",
    "pyright",
    "basedpyright",
    "eslint",
    "biome",
    "prettier",
    "black",
    "isort",
    "rubocop",
    "golangci-lint",
    "shellcheck",
    "stylelint",
    "hadolint",
    "markdownlint",
    "oxlint",
    "standard",
    "ty",
    "vale",
    "actionlint",
    "tsc",
    "clippy",
    "clippy-driver",
    "pre-commit",
    "vet",
    "staticcheck",
    "ktlint",
    "swiftlint",
    "credo",
}
_ALL_WORDS = re.compile(r"^(?:ci|check|checks|verify|validate|all|precommit|pre-commit)(?::.*)?$", re.I)
_TEST_WORD = re.compile(r"(?:^|[/_.:-])(?:tests?|specs?|e2e)(?:$|[/_.:-])", re.I)
_BUILD_WORD = re.compile(r"^(?:build|compile|package|assemble|dist)(?::.*)?$", re.I)
_LINT_WORD = re.compile(r"^(?:lint|typecheck|type-check|types|tsc|clippy|vet|fmt|format|style)(?::.*)?$", re.I)
_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _tokens(segment: str) -> list[str]:
    try:
        toks = shlex.split(segment, comments=True)
    except ValueError:
        toks = segment.split()
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if not out and (_ENV_ASSIGN.match(t) or t in _WRAPPERS or t in ("(", "{", "--")):
            i += 1
            continue
        if not out and t == "timeout" and i + 1 < len(toks):
            i += 2
            continue
        if not out and t in _RUN_WRAPPERS and i + 1 < len(toks) and toks[i + 1] in ("run", "exec"):
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def _segment_categories(toks: list[str]) -> set:
    cats: set = set()
    if not toks:
        return cats
    head = os.path.basename(toks[0]).lower()
    args = [t for t in toks[1:] if not t.startswith("-")]
    flags = [t for t in toks[1:] if t.startswith("-")]

    # bun/deno are interpreters, but "bun run build" or "deno task test" are runner calls.
    js_runner = head in ("bun", "deno") and bool(args) and args[0] in ("run", "task", "lint", "check", "fmt", "test")
    if _INTERPRETERS.match(head) and not js_runner:
        if "-m" in toks[1:3] and len(toks) > toks.index("-m") + 1:
            mod = toks[toks.index("-m") + 1].lower()
            if mod in ("build", "compileall"):
                return {"build"}
            return _segment_categories([mod, *toks[toks.index("-m") + 2 :]])
        if "--test" in flags or (args and args[0] == "test"):
            cats.add("tests")
        if args and _TEST_WORD.search(os.path.basename(args[0])):
            cats.add("tests")
        if head == "php" and args[:2] == ["artisan", "test"]:
            cats.add("tests")
        if args and re.search(r"manage\.py$", args[0]) and len(args) > 1 and args[1] == "test":
            cats.add("tests")
        if args and re.search(r"(?:^|/)(?:ci|check|verify)\w*\.\w+$", args[0]):
            cats.update(CATEGORIES)
        return cats

    if head in _TEST_TOOLS and not (head == "elm-test" and args[:1] in (["init"], ["install"])):
        cats.add("tests")
    if head in _BUILD_TOOLS:
        if head == "tsc":
            is_build = "--noEmit" not in flags
        elif head == "docker":
            is_build = "build" in args[:2]
        elif head in ("next", "nuxt", "astro", "vite"):
            is_build = args[:1] == ["build"]
        else:
            is_build = True
        if is_build:
            cats.add("build")
    if head in _LINT_TOOLS and (head != "tsc" or "--noEmit" in flags):
        cats.add("lint")

    if head in _RUNNERS or head.endswith("gradlew") or head.endswith("mvnw"):
        if head == "yarn" and args[:1] == ["workspace"]:
            args = args[2:]  # yarn workspace <name> <script>
        skip = ("run", "run-script", "exec", "x", "-C", "task", "workspaces", "foreach")
        sub = [a.lower() for a in args if a not in skip][:2]
        if head == "deno" and sub[:1] == ["check"]:
            cats.add("lint")
        if head in ("make", "gmake") and not sub:
            cats.add("build")
        for a in sub[:2]:
            if _TEST_WORD.search(a) or a in _TEST_TOOLS:
                cats.add("tests")
            if _BUILD_WORD.match(a) or (a in ("check",) and head == "cargo"):
                cats.add("build")
            if _LINT_WORD.match(a) or a in _LINT_TOOLS:
                cats.add("lint")
            if _ALL_WORDS.match(a) and not (head in ("cargo", "deno") and a == "check"):
                cats.update(CATEGORIES)
        if head in ("mvn", "mvnw", "./mvnw") and "verify" in sub:
            cats.update(CATEGORIES)
        if head == "dune" and sub[:1] == ["runtest"]:
            cats.update(("tests", "build"))
        if head in ("go",) and sub[:1] == ["vet"]:
            cats.add("lint")
        if head in ("xcodebuild",):
            cats.add("tests" if "test" in sub else "build")

    if not cats and (toks[0].startswith(("./", "scripts/", "bin/", "script/")) or head.endswith(".sh")):
        name = head.rsplit(".", 1)[0]
        if _TEST_WORD.search(name):
            cats.add("tests")
        elif _BUILD_WORD.match(name):
            cats.add("build")
        elif _LINT_WORD.match(name):
            cats.add("lint")
        elif _ALL_WORDS.match(name):
            cats.update(CATEGORIES)
    return cats


def command_categories(command: str, extra: dict[str, list[str]] | None = None) -> set:
    """Which claim categories a shell command can prove."""
    cats: set = set()
    for seg in _SPLIT_RE.split(command or ""):
        seg = seg.strip().lstrip("(").strip()
        if not seg:
            continue
        cats |= _segment_categories(_tokens(seg))
    for cat, patterns in (extra or {}).items():
        if cat in CATEGORIES and any(re.search(p, command or "") for p in patterns):
            cats.add(cat)
    # A test run builds the code, so it is also a receipt for "it builds".
    if "tests" in cats:
        cats.add("build")
    return cats


_TEST_FAIL_OUTPUT = re.compile(
    r"\b[1-9]\d*\s+(?:failed|failing|failures?|errors?)\b"
    r"|test result: FAILED|^FAIL(?:ED)?\b|FAILED \(|Tests:\s+[1-9]\d* failed",
    re.M,
)


# --------------------------------------------------------------------------
# Transcript parsing (Claude Code and Codex)
# --------------------------------------------------------------------------


@dataclass
class Event:
    kind: str  # "cmd" | "edit" | "delegate"
    command: str = ""
    ok: bool | None = None  # None means unknown (still running, backgrounded)
    output: str = ""
    path: str = ""


@dataclass
class Transcript:
    events: list[Event] = field(default_factory=list)
    last_user_id: str = ""
    last_assistant_text: str = ""
    cwd: str = ""
    # (number of events so far, final assistant text) at each finished turn
    stops: list[tuple[int, str]] = field(default_factory=list)

    def end_turn(self) -> None:
        if self.last_assistant_text:
            self.stops.append((len(self.events), self.last_assistant_text))
        self.last_assistant_text = ""


_DOC_EXT = re.compile(r"\.(?:md|mdx|markdown|txt|rst|adoc|org|csv|log)$", re.I)
# .txt files that drive a build or an install are code, not notes.
_TXT_CODE = re.compile(r"(?:^|/)(?:CMakeLists|requirements[\w.-]*|constraints[\w.-]*)\.txt$", re.I)
# Code that lives under docs/ (Sphinx conf.py, doctest helpers, docs site config) still counts.
_CODE_EXT = re.compile(
    r"\.(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|go|rs|rb|java|kt|c|h|cc|cpp|hpp|cs|swift|php|sh|toml|ya?ml|json)$", re.I
)
_DOC_NAME = re.compile(r"(?:^|/)(?:LICENSE|LICENCE|NOTICE|AUTHORS|CHANGELOG|CHANGES)[^/]*$", re.I)
_GENERATED = re.compile(
    r"(?:^|/)(?:package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|Cargo\.lock|poetry\.lock|uv\.lock|"
    r"Gemfile\.lock|composer\.lock|go\.sum|\.gitignore|\.DS_Store)$"
    r"|(?:^|/)(?:node_modules|dist|build|target|coverage|htmlcov|__pycache__|\.pytest_cache|\.mypy_cache|"
    r"\.ruff_cache|\.next|\.turbo|\.venv|venv|\.git)/"
)
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_based_edit_tool"}
_DELEGATE_TOOLS = {"Task", "Agent"}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str))
    return ""


def _read_lines(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def parse_transcript(path: Path) -> Transcript:
    tr = Transcript()
    pending: dict[str, Event] = {}
    for obj in _read_lines(path):
        if "payload" in obj and obj.get("type") in ("response_item", "event_msg", "session_meta", "turn_context"):
            _parse_codex(obj, tr, pending)
        else:
            _parse_claude(obj, tr, pending)
    return tr


def _parse_claude(obj: dict, tr: Transcript, pending: dict[str, Event]) -> None:
    if obj.get("isSidechain"):
        return
    if obj.get("cwd") and not tr.cwd:
        tr.cwd = obj["cwd"]
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    typ = obj.get("type")
    if typ == "user":
        if (
            isinstance(content, str)
            or (
                isinstance(content, list)
                and any(isinstance(b, dict) and b.get("type") == "text" for b in content)
                and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            )
        ) and not obj.get("isMeta"):
            tr.end_turn()
            tr.last_user_id = obj.get("promptId") or obj.get("uuid") or tr.last_user_id
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                ev = pending.pop(b.get("tool_use_id", ""), None)
                if ev is None or ev.kind != "cmd":
                    continue
                text = _text_of(b.get("content"))
                res = obj.get("toolUseResult")
                if isinstance(res, dict) and res.get("backgroundTaskId"):
                    ev.ok = None
                elif b.get("is_error") or (isinstance(res, dict) and res.get("interrupted")):
                    ev.ok = False
                else:
                    ev.ok = True
                if isinstance(res, dict):
                    text = "\n".join(str(res.get(k) or "") for k in ("stdout", "stderr")) or text
                    _bash_edits(tr, ev, res)
                ev.output = text[-4000:]
    elif typ == "assistant" and isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text"):
                tr.last_assistant_text = b["text"]
            if b.get("type") != "tool_use":
                continue
            name, inp = b.get("name", ""), b.get("input") or {}
            if name == "Bash" and isinstance(inp, dict):
                ev = Event("cmd", command=str(inp.get("command", "")))
                if inp.get("run_in_background"):
                    ev.ok = None
                tr.events.append(ev)
                pending[b.get("id", "")] = ev
            elif name in _EDIT_TOOLS and isinstance(inp, dict):
                tr.events.append(
                    Event("edit", path=str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""))
                )
            elif name in _DELEGATE_TOOLS:
                tr.events.append(Event("delegate"))


def _bash_edits(tr: Transcript, ev: Event, res: dict) -> None:
    """Claude Code records files a Bash call changed (sed -i, heredocs, scripts)."""
    diff = res.get("bashEditDiff")
    files = diff.get("files") if isinstance(diff, dict) else None
    if not files:
        return
    heads = {os.path.basename((_tokens(seg) or [""])[0]) for seg in _SPLIT_RE.split(ev.command)}
    if res.get("gitOperation") or heads & {"git", "gh"}:
        return  # checkouts, merges and pulls change files, but that isn't new code to test
    edits = [Event("edit", path=str(f.get("filePath", ""))) for f in files if isinstance(f, dict)]
    try:
        i = tr.events.index(ev)
    except ValueError:
        return
    # A check that also writes files (snapshots, caches) still counts as run after them.
    at = i if command_categories(ev.command) else i + 1
    tr.events[at:at] = edits


def _parse_codex(obj: dict, tr: Transcript, pending: dict[str, Event]) -> None:
    p = obj.get("payload") or {}
    typ = obj.get("type")
    if typ in ("session_meta", "turn_context") and p.get("cwd") and not tr.cwd:
        tr.cwd = str(p["cwd"])
    if typ == "event_msg":
        if p.get("type") == "task_complete":
            if p.get("last_agent_message"):
                tr.last_assistant_text = p["last_agent_message"]
            tr.end_turn()
        if p.get("type") in ("task_started", "user_message"):
            tr.last_user_id = p.get("turn_id") or tr.last_user_id or str(id(p))
        if p.get("type") == "agent_message" and p.get("message"):
            tr.last_assistant_text = p["message"]
        it = p.get("item")
        if p.get("type") == "item_completed" and isinstance(it, dict):
            kind = it.get("type")
            if kind == "CommandExecution":
                cmd = it.get("command")
                if isinstance(cmd, list):
                    cmd = cmd[-1] if len(cmd) >= 3 and cmd[1] in ("-lc", "-c") else " ".join(map(str, cmd))
                code = it.get("exit_code")
                ok = None if code is None else (code == 0 and it.get("status") in (None, "completed"))
                tr.events.append(
                    Event("cmd", command=str(cmd or ""), ok=ok, output=str(it.get("aggregated_output") or "")[-4000:])
                )
            elif kind == "FileChange" and it.get("status") != "failed":
                for path in it.get("changes") or {}:
                    tr.events.append(Event("edit", path=str(path)))
            elif kind == "AgentMessage":
                text = _text_of([{"text": c.get("text")} for c in it.get("content") or [] if isinstance(c, dict)])
                if text:
                    tr.last_assistant_text = text
            elif kind == "UserMessage":
                tr.last_user_id = p.get("turn_id") or it.get("id") or tr.last_user_id
    elif typ == "response_item":
        if p.get("type") in ("custom_tool_call", "function_call") and p.get("name") == "apply_patch":
            body = p.get("input") or p.get("arguments") or ""
            for path in _PATCH_FILE.findall(str(body)):
                tr.events.append(Event("edit", path=path.strip()))
        elif p.get("type") == "function_call" and p.get("name") in ("shell", "local_shell"):
            # Older Codex: commands as function calls; output arrives separately.
            try:
                args = json.loads(p.get("arguments") or "{}")
            except ValueError:
                args = {}
            cmd = args.get("command")
            if isinstance(cmd, list):
                cmd = cmd[-1] if len(cmd) >= 3 and cmd[1] in ("-lc", "-c") else " ".join(map(str, cmd))
            if cmd:
                ev = Event("cmd", command=str(cmd))
                tr.events.append(ev)
                pending[p.get("call_id", "")] = ev
        elif p.get("type") == "function_call_output" and p.get("call_id") in pending:
            ev = pending.pop(p["call_id"])
            out = p.get("output")
            text = out if isinstance(out, str) else _text_of(out)
            m = re.search(r'"exit_code"\s*:\s*(-?\d+)|Process exited with code (-?\d+)|Exit code:? (-?\d+)', text)
            if m:
                ev.ok = int(next(g for g in m.groups() if g is not None)) == 0
            ev.output = text[-4000:]


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------


@dataclass
class Verdict:
    block: bool
    missing: dict[str, str] = field(default_factory=dict)  # category -> claim sentence
    failed: dict[str, str] = field(default_factory=dict)  # category -> failing command
    reason: str = ""


def _project_relative(p: str, cwd: str) -> str:
    if cwd and p.startswith("/"):
        root = cwd.replace("\\", "/").rstrip("/") + "/"
        return p[len(root) :] if p.startswith(root) else ""
    return p[2:] if p.startswith("./") else p


def _counts_as_edit(path: str, cwd: str, ignore: list[str], project_dirs: Iterable[str] = ()) -> bool:
    if not path:
        return True
    p = path.replace("\\", "/")
    is_doc = (_DOC_EXT.search(p) and not _TXT_CODE.search(p)) or ("/docs/" in f"/{p}" and not _CODE_EXT.search(p))
    if is_doc or _DOC_NAME.search(p) or _GENERATED.search(p):
        return False
    if any(re.search(rx, f"/{p.lstrip('/')}") for rx in ignore):
        return False
    # A repo's folders only match from the project root, so "Users" or "home"
    # can't hide every absolute path.
    rel = _project_relative(p, cwd)
    if rel and any(rel == d or rel.startswith(d + "/") for d in project_dirs):
        return False
    if cwd and (p.startswith("/") or os.path.isabs(p)):
        root = cwd.replace("\\", "/").rstrip("/") + "/"
        if not p.startswith(root):
            return False  # plans, memory, scratch files outside the project
    return True


def evaluate(tr: Transcript, message: str, config: dict | None = None, cwd: str = "") -> Verdict:
    cfg = config or {}
    cats = [c for c in cfg.get("claims", CATEGORIES) if c in CATEGORIES]
    claims = find_claims(message, cats)
    if not claims:
        return Verdict(False)
    cwd = cwd or tr.cwd
    ignore = list(cfg.get("ignore_paths", []))
    extra = cfg.get("extra_commands") or {}

    last_edit = -1
    for i, ev in enumerate(tr.events):
        if ev.kind == "edit" and _counts_as_edit(ev.path, cwd, ignore, cfg.get("project_ignore_dirs", [])):
            last_edit = i
    after = tr.events[last_edit + 1 :]
    if any(ev.kind == "delegate" for ev in after):
        return Verdict(False)  # a subagent may have run them; we can't see its log

    missing: dict[str, str] = {}
    failed: dict[str, str] = {}
    for cat, sentence in claims.items():
        state = "none"  # none | ok | unknown | failed
        fail_cmd = ""
        for ev in after:
            if ev.kind != "cmd" or cat not in command_categories(ev.command, extra):
                continue
            if ev.ok is None:
                state = "unknown" if state != "ok" else state
                continue
            ok = ev.ok
            fail_out = "tests" in command_categories(ev.command, extra) and _TEST_FAIL_OUTPUT.search(ev.output or "")
            if ok and fail_out:
                ok = False
            elif not ok and not fail_out and len([x for x in _SPLIT_RE.split(ev.command) if x.strip()]) > 1:
                ok = None  # a compound command can fail in a step that isn't the check
            if ok:
                state = "ok"  # one green run after the last edit is enough
            elif ok is None:
                state = "unknown" if state != "ok" else state
            elif state != "ok":
                state, fail_cmd = "failed", ev.command
        if state == "none":
            missing[cat] = sentence
        elif state == "failed":
            failed[cat] = fail_cmd
    if not missing and not failed:
        return Verdict(False)
    return Verdict(True, missing, failed, _reason(missing, failed, last_edit >= 0))


_NOUN = {"tests": "test", "build": "build", "lint": "lint or typecheck"}


def _reason(missing: dict[str, str], failed: dict[str, str], edited: bool) -> str:
    lines = ["show-receipts: you're about to finish with a claim I can't find proof for in this session."]
    for cat, sent in missing.items():
        when = " after your last code edit" if edited else ""
        lines.append(f'- You said "{_clip(sent)}", but no {_NOUN[cat]} command ran{when}.')
    for cat, cmd in failed.items():
        lines.append(f"- The last {_NOUN[cat]} run (`{_clip(cmd, 80)}`) did not pass.")
    lines.append(
        "Run it now and report the command and its exit code. If you can't run it, "
        "say that plainly instead of claiming it passes."
    )
    return "\n".join(lines)


def _clip(s: str, n: int = 140) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


# --------------------------------------------------------------------------
# Hook plumbing
# --------------------------------------------------------------------------


# A repository may only add ignore paths. Everything that can switch the check
# off or change what counts as proof lives in the user's own config.
PROJECT_KEYS = ("ignore_paths",)


def _real_home() -> Path:
    # HOME can be set by a project's agent settings, so ask the OS instead.
    if pwd is not None:
        try:
            return Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (KeyError, OSError):
            pass
    return Path.home()


def _inside(path: Path, folder: Path | None) -> bool:
    return folder is not None and (path == folder or folder in path.parents)


def user_config_path(project: Path | None = None) -> Path:
    # A repository can set environment variables for the agent (for example in
    # .claude/settings.json), so neither variable may point back into the project.
    home = (_real_home() / ".config").resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser().resolve() if xdg and os.path.isabs(xdg) else home
    if _inside(base, project):
        base = home
    default = base / "show-receipts" / "config.json"
    if _inside(base, project):
        return Path(os.devnull)  # even the real home is inside the project: use no config
    override = os.environ.get("SHOW_RECEIPTS_CONFIG")
    if not override:
        return default
    path = Path(override).expanduser().resolve()
    if _inside(path, base) and not _inside(path, project):
        return path
    return default


def _read_json(f: Path) -> dict:
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _project_config(cwd: str) -> tuple[dict, Path | None]:
    d = Path(cwd or ".").resolve()
    for parent in [d, *d.parents]:
        f = parent / CONFIG_NAME
        if f.is_file():
            return _read_json(f), parent
        if (parent / ".git").exists():
            return {}, parent
    return {}, None


_FOLDER = re.compile(r"[\w.-]+(/[\w.-]+)*")


def _project_ignore_dir(entry: object) -> str | None:
    # Project entries are folder names matched literally, not regexes, so a repo
    # can't ignore every edit with "." and quietly switch the check off.
    if not isinstance(entry, str):
        return None
    d = entry.strip().strip("/")
    if not _FOLDER.fullmatch(d) or any(part in (".", "..") for part in d.split("/")):
        return None
    return d


def load_config(cwd: str) -> dict:
    project, root = _project_config(cwd)
    user = _read_json(user_config_path(root or Path(cwd or ".").resolve()))
    cfg = {k: v for k, v in user.items() if k != "disabled_projects"}
    here = Path(cwd or ".").resolve()
    for p in user.get("disabled_projects") or []:
        try:
            off = Path(str(p)).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if here == off or off in here.parents or (root is not None and root == off):
            cfg["enabled"] = False
    dirs = [d for d in (_project_ignore_dir(x) for x in project.get("ignore_paths") or []) if d]
    if dirs:
        cfg["project_ignore_dirs"] = dirs
    return cfg


def _find_codex_transcript(session_id: str, cwd: str = "") -> Path | None:
    if not session_id:
        return None
    home = _real_home() / ".codex"
    env = os.environ.get("CODEX_HOME")
    if env and not _inside(Path(env).expanduser().resolve(), Path(cwd).resolve() if cwd else None):
        home = Path(env).expanduser()
    base = home / "sessions"
    if not base.is_dir():
        return None
    hits = sorted(base.rglob(f"*{session_id}*.jsonl"), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def _state_file(session_id: str) -> Path:
    h = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / "show-receipts" / f"{h}.json"


def _blocks_so_far(session_id: str, turn: str) -> int | None:
    try:
        data = json.loads(_state_file(session_id).read_text())
        return int(data.get("blocks", 0)) if data.get("turn") == turn else 0
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):
        return None


def _record_block(session_id: str, turn: str, n: int) -> bool:
    try:
        f = _state_file(session_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"turn": turn, "blocks": n}))
        return True
    except OSError:
        return False


def run_hook(payload: dict) -> dict | None:
    """Return the JSON to print (a block decision) or None to allow."""
    cwd = str(payload.get("cwd") or os.getcwd())
    cfg = load_config(cwd)
    if cfg.get("enabled") is False:
        return None
    session = str(payload.get("session_id") or "")
    tpath = payload.get("transcript_path")
    path = Path(tpath).expanduser() if tpath else _find_codex_transcript(session, cwd)
    if not path or not path.is_file():
        return None
    tr = parse_transcript(path)
    message = payload.get("last_assistant_message")
    if not isinstance(message, str) or not message.strip():
        message = tr.last_assistant_text
    verdict = evaluate(tr, message, cfg, cwd)
    if not verdict.block:
        return None

    # Loop guard: at most max_blocks per user prompt. If we can't keep count,
    # fall back to the host's own flag and never block a continued stop.
    turn = str(payload.get("prompt_id") or payload.get("turn_id") or tr.last_user_id or "")
    max_blocks = int(cfg.get("max_blocks", 1))
    so_far = _blocks_so_far(session, turn) if session and turn else None
    if so_far is None:
        if payload.get("stop_hook_active"):
            return None
        so_far = 0
    if so_far >= max_blocks:
        return None
    if not _record_block(session, turn, so_far + 1) and payload.get("stop_hook_active"):
        return None
    return {"decision": "block", "reason": verdict.reason}


def _cli(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="show-receipts", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="check a transcript file by hand")
    c.add_argument("transcript")
    c.add_argument("--message", help="final message to check (default: last one in the transcript)")
    c.add_argument("--cwd", default="")
    rp = sub.add_parser("replay", help="replay past transcripts and show every stop that would be blocked")
    rp.add_argument("transcripts", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        return _replay(args.transcripts)
    if args.cmd != "check":
        ap.print_help()
        return 2
    tr = parse_transcript(Path(args.transcript))
    msg = args.message if args.message is not None else tr.last_assistant_text
    v = evaluate(tr, msg, load_config(args.cwd or tr.cwd or "."), args.cwd)
    print(v.reason if v.block else "ok: no unproven claims")
    return 1 if v.block else 0


def replay(path: Path) -> Iterator[tuple[int, str, Verdict]]:
    """Yield (turn number, final message, verdict) for each finished turn."""
    tr = parse_transcript(path)
    tr.end_turn()
    cfg = load_config(tr.cwd) if tr.cwd and os.path.isdir(tr.cwd) else {}
    for n, (upto, text) in enumerate(tr.stops, 1):
        part = Transcript(events=tr.events[:upto], cwd=tr.cwd)
        yield n, text, evaluate(part, text, cfg)


def _replay(paths: list[str]) -> int:
    turns = claims = blocks = 0
    for p in paths:
        for n, text, v in replay(Path(p)):
            turns += 1
            claims += bool(find_claims(text))
            if v.block:
                blocks += 1
                print(f"{p} turn {n}:\n{v.reason}\n")
    print(f"{turns} turns, {claims} with pass claims, {blocks} would be blocked")
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        return _cli(sys.argv[1:])
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        out = run_hook(payload if isinstance(payload, dict) else {})
    except Exception as exc:  # never block a stop because of our own bug
        if os.environ.get("SHOW_RECEIPTS_DEBUG"):
            print(f"show-receipts error: {exc!r}", file=sys.stderr)
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
