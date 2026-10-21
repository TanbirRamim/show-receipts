"""Tests for hooks/show_receipts.py. Run with: python3 -m unittest discover tests"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "show_receipts.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(HOOK.parent))

import show_receipts as sr  # noqa: E402

# --------------------------------------------------------------------------
# helpers to write small transcripts
# --------------------------------------------------------------------------

_n = 0


def _id() -> str:
    global _n
    _n += 1
    return f"toolu_{_n:04d}"


def user(text: str, prompt_id: str = "p1") -> dict:
    return {
        "type": "user",
        "uuid": f"u-{prompt_id}",
        "promptId": prompt_id,
        "cwd": "/repo",
        "message": {"role": "user", "content": text},
    }


def say(text: str) -> dict:
    return {
        "type": "assistant",
        "cwd": "/repo",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def bash(command: str, ok: bool = True, out: str = "", **extra) -> list:
    tid = _id()
    res = {"stdout": out, "stderr": "", "interrupted": False}
    res.update(extra)
    return [
        {
            "type": "assistant",
            "cwd": "/repo",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}],
            },
        },
        {
            "type": "user",
            "cwd": "/repo",
            "toolUseResult": res,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "is_error": not ok,
                        "content": out if ok else f"Exit code 1\n{out}",
                    }
                ],
            },
        },
    ]


def edit(path: str = "/repo/src/app.py", tool: str = "Edit") -> list:
    tid = _id()
    return [
        {
            "type": "assistant",
            "cwd": "/repo",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tid, "name": tool, "input": {"file_path": path}}],
            },
        },
        {
            "type": "user",
            "cwd": "/repo",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]},
        },
    ]


def write_jsonl(rows: list, directory: str) -> Path:
    flat = []
    for r in rows:
        flat.extend(r if isinstance(r, list) else [r])
    p = Path(directory) / "t.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in flat))
    return p


def verdict(rows: list, message: str | None = None, config: dict | None = None, cwd: str = "/repo") -> sr.Verdict:
    with tempfile.TemporaryDirectory() as d:
        tr = sr.parse_transcript(write_jsonl(rows, d))
    return sr.evaluate(tr, message if message is not None else tr.last_assistant_text, config, cwd)


# --------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------


class ClaimTests(unittest.TestCase):
    def test_confident_claims_are_found(self):
        for text, cat in [
            ("All tests pass.", "tests"),
            ("Done. The test suite passes.", "tests"),
            ("42 passed in 3.1s", "tests"),
            ("Unit tests are green now.", "tests"),
            ("12/12 tests pass", "tests"),
            ("All 12 tests pass, no failures.", "tests"),
            ("The build succeeds.", "build"),
            ("It compiles cleanly.", "build"),
            ("Lint is clean.", "lint"),
            ("mypy passes and there are no type errors.", "lint"),
        ]:
            with self.subTest(text=text):
                self.assertIn(cat, sr.find_claims(text))

    def test_hedged_or_negative_sentences_are_ignored(self):
        for text in [
            "Tests should pass now.",
            "I couldn't run the tests, so I don't know if they pass.",
            "Did the tests pass?",
            "Run npm test to make sure the tests pass.",
            "Once CI is green we can merge.",
            "The tests pass in CI.",
            "Tests were passing before my change.",
            "Its agent reports 16 tests passing.",
            'When the agent says "tests pass", it gets checked.',
            "If the build succeeds, ship it.",
            "Fixed the bug.",
            "Done!",
            "All 274 tests use a fake network.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(sr.find_claims(text), {})

    def test_code_blocks_and_quotes_are_ignored(self):
        self.assertEqual(sr.find_claims("```\n12 passed\n```\n> all tests pass"), {})

    def test_other_ways_of_saying_it_passes(self):
        for text, cat in [
            ("All green.", "tests"),
            ("Everything passes.", "tests"),
            ("Everything is green now.", "tests"),
            ("The suite is green.", "tests"),
            ("Suite passes.", "tests"),
            ("pytest is happy.", "tests"),
            ("pytest passes.", "tests"),
            ("jest passes.", "tests"),
            ("cargo test passes.", "tests"),
            ("go test is green.", "tests"),
            ("All specs pass.", "tests"),
            ("The spec suite passes.", "tests"),
            ("No test failures.", "tests"),
            ("All 37 tests passing.", "tests"),
            ("Verified the fix works.", "tests"),
            ("I verified the fix with the tests.", "tests"),
            ("Tests pass locally.", "tests"),
            ("The whole suite passes.", "tests"),
            ("Full test suite is green.", "tests"),
            ("Ran the tests and they all pass.", "tests"),
            ("Tests are all passing.", "tests"),
            ("Compiles cleanly.", "build"),
            ("It compiles now.", "build"),
            ("Builds cleanly.", "build"),
            ("The project builds without errors.", "build"),
            ("Build is green.", "build"),
            ("cargo build succeeds.", "build"),
            ("Compiles without warnings.", "build"),
            ("Type-checks fine.", "lint"),
            ("Typechecks cleanly.", "lint"),
            ("No lint errors.", "lint"),
            ("tsc reports no errors.", "lint"),
            ("No type errors.", "lint"),
            ("Lint passes.", "lint"),
            ("Linter is happy.", "lint"),
            ("ruff is clean.", "lint"),
            ("clippy is happy.", "lint"),
            ("No lint warnings.", "lint"),
            ("eslint reports 0 problems.", "lint"),
            ("mypy is clean.", "lint"),
            ("Zero lint errors.", "lint"),
            ("No clippy warnings.", "lint"),
        ]:
            with self.subTest(text=text):
                self.assertIn(cat, sr.find_claims(text))

    def test_lookalikes_that_are_not_claims(self):
        for text in [
            "CI should pass now.",
            "Run the tests to confirm.",
            "Tests should be added.",
            "I didn't run the tests.",
            'You said "all green" earlier.',
            "```\nall green\n```",
            "> everything passes",
            "Everything is done.",
            "All green lights on the design.",
            "The green button now works.",
            "Everything works as intended?",
            "Let me know if everything passes.",
            "Next step: make the suite green.",
            "I haven't verified the fix works.",
            "Please verify the fix works.",
            "pytest isn't happy yet.",
            "Once it compiles, we can ship.",
            "Type-checking might complain.",
            "No tests exist for this module.",
            "Need to add lint config.",
            "The suite was green before this change.",
            "It will compile after the rename.",
            "Everything passes through the middleware.",
            "Requests pass through the proxy.",
            "All green tickets are closed.",
            "The tests cover no errors path.",
            "No errors in the logs, but I didn't run the tests.",
            "Tests need to be written.",
            "To check, run npm test.",
            "The fix works around the bug.",
            "Everything is wired up.",
            "The user said tests pass.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(sr.find_claims(text), {})


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


class CommandTests(unittest.TestCase):
    def cats(self, cmd):
        return sr.command_categories(cmd)

    def test_test_runners(self):
        for cmd in [
            "pytest -q",
            "python3 -m pytest",
            "python3.12 -m unittest discover tests",
            "npm test",
            "pnpm --filter web test",
            "yarn test:unit",
            "go test ./...",
            "cargo test -q",
            "cargo nextest run",
            "uv run pytest",
            "npx vitest run",
            "bun test",
            "node --test",
            "./scripts/run_tests.sh",
            "make test",
            "mvn -q test",
            "./gradlew test",
            "dotnet test",
            "bundle exec rspec",
            "python manage.py test",
            "cd app && FOO=1 pytest | tail -5",
            "timeout 60 npx jest",
            "deno test",
            "swift test",
            "mix test",
            "tox -e py312",
            "gotestsum -- ./...",
            "behave",
            "cucumber-js",
            "dune test",
            "elm-test",
            "dune runtest",
            "opam exec -- dune test",
            "uv run -- pytest",
            "npx -- jest",
        ]:
            with self.subTest(cmd=cmd):
                self.assertIn("tests", self.cats(cmd))

    def test_scaffolding_is_not_a_test_run(self):
        for cmd in ["elm-test init", "elm-test install elm/json"]:
            with self.subTest(cmd=cmd):
                self.assertNotIn("tests", self.cats(cmd))

    def test_more_runners(self):
        tests = [
            "bun test",
            "bun run test",
            "deno test -A",
            "deno task test",
            "pnpm test",
            "pnpm -r test",
            "yarn test",
            "yarn workspace api test",
            "yarn workspaces foreach -A run test",
            "nx test api",
            "nx run api:test",
            "nx affected -t test",
            "npx nx test api",
            "turbo test",
            "turbo run test --filter=api",
            "uv run pytest -q",
            "poetry run pytest",
            "tox",
            "nox -s tests",
            "gradle test",
            "mvn test",
            "swift test",
            "zig build test",
            "phpunit",
            "vendor/bin/phpunit tests",
            "./vendor/bin/pest",
            "php artisan test",
            "composer test",
            "mix test",
            "gotestsum -- ./...",
            "behave",
            "cucumber-js",
            "dune test",
            "elm-test",
        ]
        for cmd in tests:
            with self.subTest(cmd=cmd):
                self.assertIn("tests", self.cats(cmd))
        other = {
            "bun run build": {"build"},
            "deno lint": {"lint"},
            "deno check main.ts": {"lint"},
            "deno fmt --check": {"lint"},
            "mix credo": {"lint"},
            "swift build": {"build"},
            "zig build": {"build"},
            "nx build web": {"build"},
            "turbo lint": {"lint"},
            "yarn workspace api build": {"build"},
        }
        for cmd, want in other.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(self.cats(cmd), want)
        for cmd in ["bun script.ts", "deno run main.ts", "bun run dev", "php artisan serve", "mix deps.get"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.cats(cmd), set())

    def test_build_and_lint(self):
        self.assertIn("build", self.cats("npm run build"))
        self.assertIn("build", self.cats("cargo build --release"))
        self.assertIn("build", self.cats("go build ./..."))
        self.assertIn("build", self.cats("make"))
        self.assertIn("build", self.cats("next build"))
        self.assertIn("build", self.cats("python -m build"))
        self.assertIn("lint", self.cats("ruff check ."))
        self.assertIn("lint", self.cats("uvx ruff check ."))
        self.assertIn("lint", self.cats("npx tsc --noEmit"))
        self.assertIn("build", self.cats("npx tsc -p ."))
        self.assertIn("lint", self.cats("cargo clippy -- -D warnings"))
        self.assertIn("lint", self.cats("npm run typecheck"))
        self.assertIn("lint", self.cats("go vet ./..."))

    def test_more_linters(self):
        for cmd in [
            "yamllint .",
            "hlint src",
            "luacheck .",
            "sqlfluff lint sql/",
            "buf lint proto/",
            "tflint",
            "swiftformat --lint .",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.cats(cmd), {"lint"})

    def test_lint_subcommand_and_flag_gated_tools(self):
        # sqlfluff's other subcommands rewrite or parse, buf's build the schema:
        # neither may prove a lint claim.
        for cmd in ["sqlfluff fix sql/", "sqlfluff parse sql/", "buf generate", "buf build"]:
            with self.subTest(cmd=cmd):
                self.assertNotIn("lint", self.cats(cmd))
        # swiftformat without --lint rewrites files, it does not check them.
        self.assertNotIn("lint", self.cats("swiftformat ."))

    def test_a_ci_script_proves_everything(self):
        self.assertEqual(self.cats("npm run ci"), {"tests", "build", "lint"})
        self.assertEqual(self.cats("make check"), {"tests", "build", "lint"})

    def test_reading_is_not_running(self):
        for cmd in [
            "cat tests/test_app.py",
            "grep -r pytest .",
            "ls tests",
            "echo all tests pass",
            "git commit -m 'tests pass'",
            "npm install",
            "sed -n 1,20p tests/x.py",
            "open test.html",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.cats(cmd), set())

    def test_extra_commands_from_config(self):
        self.assertIn("tests", sr.command_categories("./acme verify-all", {"tests": [r"acme verify-all"]}))


# --------------------------------------------------------------------------
# verdicts on synthetic transcripts
# --------------------------------------------------------------------------


class VerdictTests(unittest.TestCase):
    def test_no_claim_allows(self):
        self.assertFalse(verdict([user("fix it"), edit(), say("I changed the parser.")]).block)

    def test_claim_without_any_run_blocks(self):
        v = verdict([user("fix it"), edit(), say("Fixed it and all tests pass.")])
        self.assertTrue(v.block)
        self.assertIn("tests", v.missing)
        self.assertIn("no test command ran after your last code edit", v.reason)

    def test_claim_with_green_run_after_edit_allows(self):
        rows = [user("fix it"), edit(), bash("pytest -q", True, "12 passed"), say("All tests pass.")]
        self.assertFalse(verdict(rows).block)

    def test_run_before_the_last_edit_does_not_count(self):
        rows = [user("fix it"), bash("pytest -q", True, "12 passed"), edit(), say("All tests pass.")]
        self.assertTrue(verdict(rows).block)

    def test_failing_run_blocks_with_the_command(self):
        rows = [user("x"), edit(), bash("pytest -q", False, "1 failed, 11 passed"), say("All tests pass.")]
        v = verdict(rows)
        self.assertTrue(v.block)
        self.assertEqual(v.failed, {"tests": "pytest -q"})

    def test_piped_run_that_hides_a_failure_blocks(self):
        rows = [user("x"), edit(), bash("pytest -q | tail -3", True, "2 failed, 10 passed in 1s"), say("Tests pass.")]
        self.assertTrue(verdict(rows).block)

    def test_one_green_run_wins_over_a_later_deliberate_failure(self):
        rows = [
            user("x"),
            edit(),
            bash("go test ./...", True, "ok"),
            bash("go test ./...", False, "FAIL"),
            say("All tests pass."),
        ]
        self.assertFalse(verdict(rows).block)

    def test_compound_command_failing_elsewhere_is_unknown(self):
        rows = [user("x"), edit(), bash("pnpm test; curl localhost:1", False, "Tests 65 passed"), say("Tests pass.")]
        self.assertFalse(verdict(rows).block)

    def test_doc_edits_after_tests_do_not_reset(self):
        rows = [
            user("x"),
            edit(),
            bash("pytest", True, "3 passed"),
            edit("/repo/README.md"),
            edit("/repo/docs/guide.rst"),
            edit("/repo/CHANGELOG.md"),
            say("All tests pass."),
        ]
        self.assertFalse(verdict(rows).block)

    def test_edits_outside_the_project_do_not_reset(self):
        rows = [
            user("x"),
            edit(),
            bash("pytest", True, "3 passed"),
            edit("/home/dev/.claude/memory/notes.py", "Write"),
            say("All tests pass."),
        ]
        self.assertFalse(verdict(rows).block)

    def test_bash_file_changes_count_as_edits(self):
        diff = {"bashEditDiff": {"files": [{"filePath": "/repo/src/app.py"}]}}
        rows = [
            user("x"),
            bash("pytest", True, "3 passed"),
            bash("sed -i '' s/a/b/ src/app.py", True, "", **diff),
            say("All tests pass."),
        ]
        self.assertTrue(verdict(rows).block)

    def test_git_checkouts_are_not_edits(self):
        diff = {"bashEditDiff": {"files": [{"filePath": "/repo/src/app.py"}]}}
        rows = [
            user("x"),
            edit(),
            bash("pytest", True, "3 passed"),
            bash("git pull -q", True, "", **diff),
            say("All tests pass."),
        ]
        self.assertFalse(verdict(rows).block)

    def test_delegating_to_a_subagent_allows(self):
        tid = _id()
        rows = [
            user("x"),
            edit(),
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": tid, "name": "Agent", "input": {}}],
                },
            },
            say("All tests pass."),
        ]
        self.assertFalse(verdict(rows).block)

    def test_background_run_is_unknown_and_allows(self):
        rows = [user("x"), edit(), bash("npm test", True, "", backgroundTaskId="b1"), say("Tests pass.")]
        self.assertFalse(verdict(rows).block)

    def test_build_claim_needs_a_build(self):
        rows = [user("x"), edit(), bash("ruff check .", True), say("The build succeeds.")]
        self.assertTrue(verdict(rows).block)
        rows = [user("x"), edit(), bash("cargo test", True, "ok"), say("The build succeeds.")]
        self.assertFalse(verdict(rows).block)

    def test_config_can_limit_claims(self):
        rows = [user("x"), edit(), say("Lint is clean.")]
        self.assertTrue(verdict(rows).block)
        self.assertFalse(verdict(rows, config={"claims": ["tests"]}).block)

    def test_code_named_like_docs_still_counts(self):
        for path in ("/repo/CMakeLists.txt", "/repo/requirements-dev.txt", "/repo/docs/conf.py", "/repo/docs/site.yml"):
            rows = [user("x"), edit(), bash("pytest", True, "1 passed"), edit(path), say("Tests pass.")]
            self.assertTrue(verdict(rows).block, path)
        for path in ("/repo/notes.txt", "/repo/docs/guide.rst", "/repo/docs/img/arch.svg"):
            rows = [user("x"), edit(), bash("pytest", True, "1 passed"), edit(path), say("Tests pass.")]
            self.assertFalse(verdict(rows).block, path)

    def test_config_ignore_paths(self):
        rows = [user("x"), edit(), bash("pytest", True, "1 passed"), edit("/repo/gen/schema.py"), say("Tests pass.")]
        self.assertTrue(verdict(rows).block)
        self.assertFalse(verdict(rows, config={"ignore_paths": ["/gen/"]}).block)
        self.assertFalse(verdict(rows, config={"project_ignore_dirs": ["gen"]}).block)
        self.assertTrue(verdict(rows, config={"project_ignore_dirs": ["ge"]}).block)


# --------------------------------------------------------------------------
# synthetic transcripts in the real Claude Code and Codex formats
# --------------------------------------------------------------------------


def _load(name: str) -> list:
    return [json.loads(x) for x in (FIXTURES / name).read_text().splitlines() if x.strip()]


def _drop_checks(rows: list) -> list:
    """Remove every check command (and its result) from a transcript."""
    drop = set()
    out = []
    for r in rows:
        c = (r.get("message") or {}).get("content")
        if isinstance(c, list) and any(b.get("type") == "tool_use" and b.get("name") == "Bash" for b in c):
            drop |= {b["id"] for b in c if b.get("type") == "tool_use"}
            continue
        if isinstance(c, list) and any(b.get("type") == "tool_result" and b.get("tool_use_id") in drop for b in c):
            continue
        item = (r.get("payload") or {}).get("item") or {}
        if item.get("type") == "CommandExecution":
            continue
        out.append(r)
    return out


def _edit_before_final(rows: list, path: str, codex: bool = False) -> list:
    if codex:
        fc = {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "FileChange", "status": "completed", "changes": {path: {}}},
            },
        }
        return [*rows[:-1], fc, rows[-1]]
    return rows[:-1] + edit(path) + [rows[-1]]


class TranscriptFixtureTests(unittest.TestCase):
    CASES: ClassVar[list] = [
        ("claude_pytest_proven.jsonl", "/home/dev/projects/csv-tidy/src/csv_tidy/dates.py", False),
        ("claude_cargo_proven.jsonl", "/home/dev/projects/tally/src/lib.rs", False),
        ("codex_proven.jsonl", "/home/dev/projects/notes-app/apps/web/src/search.tsx", True),
    ]

    def check(self, rows):
        with tempfile.TemporaryDirectory() as d:
            p = write_jsonl(rows, d)
            tr = sr.parse_transcript(p)
            tr.end_turn()
            self.assertTrue(tr.stops, "fixture has a final message")
            upto, text = tr.stops[-1]
            return sr.evaluate(sr.Transcript(events=tr.events[:upto], cwd=tr.cwd), text)

    def test_proven_claims_are_allowed(self):
        for name, _, _ in self.CASES:
            with self.subTest(name=name):
                self.assertFalse(self.check(_load(name)).block)

    def test_same_claims_without_the_runs_are_blocked(self):
        for name, _, _ in self.CASES:
            with self.subTest(name=name):
                v = self.check(_drop_checks(_load(name)))
                self.assertTrue(v.block)
                self.assertIn("tests", v.missing)

    def test_same_claims_after_a_later_edit_are_blocked(self):
        for name, path, codex in self.CASES:
            with self.subTest(name=name):
                self.assertTrue(self.check(_edit_before_final(_load(name), path, codex)).block)

    def test_cli_check_and_replay(self):
        for name, _, _ in self.CASES:
            r = subprocess.run(
                [sys.executable, str(HOOK), "check", str(FIXTURES / name)], capture_output=True, text=True
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = subprocess.run(
            [sys.executable, str(HOOK), "replay", *map(str, FIXTURES.glob("*.jsonl"))], capture_output=True, text=True
        )
        self.assertIn("0 would be blocked", r.stdout)

    def test_cli_check_explain(self):
        r = subprocess.run(
            [sys.executable, str(HOOK), "check", "--explain", str(FIXTURES / "claude_pytest_proven.jsonl")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("claims:", r.stdout)
        self.assertIn("tests", r.stdout)
        self.assertIn("last edit:", r.stdout)
        self.assertIn("receipts:", r.stdout)
        self.assertIn("verdict:     ok", r.stdout)

    def test_cli_check_explain_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            rows = _edit_before_final(_load("claude_pytest_proven.jsonl"), "src/dates.py", False)
            p = write_jsonl(rows, d)
            r = subprocess.run(
                [sys.executable, str(HOOK), "check", "--explain", str(p)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("claims:", r.stdout)
            self.assertIn("last edit:", r.stdout)
            self.assertIn("before the last edit, ignored", r.stdout)
            self.assertIn("verdict:     block", r.stdout)


# --------------------------------------------------------------------------
# the hook contract: stdin JSON in, block decision JSON out, never loop
# --------------------------------------------------------------------------


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.xdg = Path(self.tmp.name) / "xdg"
        self.env = dict(os.environ, TMPDIR=self.tmp.name, XDG_CONFIG_HOME=str(self.xdg))
        self.env.pop("SHOW_RECEIPTS_DISABLE", None)
        self.env.pop("SHOW_RECEIPTS_CONFIG", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, rows, message, **payload):
        p = write_jsonl(rows, self.tmp.name)
        data = {
            "session_id": "s1",
            "transcript_path": str(p),
            "cwd": "/repo",
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": message,
        }
        data.update(payload)
        r = subprocess.run(
            [sys.executable, str(HOOK)], input=json.dumps(data), capture_output=True, text=True, env=self.env
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_blocks_then_lets_go_on_the_retry(self):
        rows = [user("fix", "p9"), edit()]
        out = self.run_hook(rows, "All tests pass.")
        self.assertEqual(out["decision"], "block")
        self.assertIn("All tests pass.", out["reason"])
        # the agent ignores us and stops again: we don't block twice for one prompt
        self.assertIsNone(self.run_hook(rows, "All tests pass.", stop_hook_active=True))

    def test_new_prompt_gets_a_fresh_budget(self):
        self.assertIsNotNone(self.run_hook([user("a", "p1"), edit()], "Tests pass."))
        self.assertIsNotNone(self.run_hook([user("a", "p1"), edit(), user("b", "p2"), edit()], "Tests pass."))

    def test_prompt_id_from_payload_is_used(self):
        rows = [user("a", "p1"), edit()]
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", prompt_id="x1"))
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", prompt_id="x2"))
        self.assertIsNone(self.run_hook(rows, "Tests pass.", prompt_id="x2"))

    def test_allows_proven_claim(self):
        rows = [user("fix"), edit(), bash("pytest", True, "3 passed")]
        self.assertIsNone(self.run_hook(rows, "All 3 tests pass."))

    def test_falls_back_to_transcript_message(self):
        rows = [user("fix"), edit(), say("All tests pass.")]
        self.assertIsNotNone(self.run_hook(rows, ""))

    def test_bad_input_never_blocks(self):
        for stdin in ["", "not json", "[]", json.dumps({"transcript_path": "/nope.jsonl"})]:
            r = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, env=self.env)
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def write_user_config(self, data: dict, name: str = "config.json") -> Path:
        f = self.xdg / "show-receipts" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data))
        return f

    def test_disable_env_and_config(self):
        rows = [user("fix"), edit()]
        # The env switch is gone: a project's agent settings could set it.
        self.env["SHOW_RECEIPTS_DISABLE"] = "1"
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", session_id="env"))
        del self.env["SHOW_RECEIPTS_DISABLE"]
        proj = Path(self.tmp.name) / "proj"
        proj.mkdir()
        self.write_user_config({"disabled_projects": [str(proj)]})
        self.assertIsNone(self.run_hook(rows, "Tests pass.", cwd=str(proj)))

    def test_repo_config_cannot_turn_the_check_off(self):
        rows = [user("fix"), edit()]
        proj = Path(self.tmp.name) / "proj"
        proj.mkdir()
        (proj / ".show-receipts.json").write_text(
            json.dumps({"enabled": False, "claims": [], "max_blocks": 0, "extra_commands": {"tests": ["^true"]}})
        )
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", cwd=str(proj)))

    def test_config_env_pointing_into_the_project_is_ignored(self):
        rows = [user("fix"), edit()]
        proj = Path(self.tmp.name) / "proj"
        (proj / ".git").mkdir(parents=True)
        (proj / "show-receipts").mkdir()
        evil = proj / "show-receipts" / "config.json"
        evil.write_text('{"enabled": false}')
        self.env["SHOW_RECEIPTS_CONFIG"] = str(evil)
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", cwd=str(proj)))
        # Moving the config dir into the repo doesn't help either.
        self.env["XDG_CONFIG_HOME"] = str(proj)
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", cwd=str(proj / "show-receipts"), session_id="s2"))

    def test_config_env_under_the_config_dir_is_honored(self):
        rows = [user("fix"), edit()]
        self.env["SHOW_RECEIPTS_CONFIG"] = str(self.write_user_config({"enabled": False}, "work.json"))
        self.assertIsNone(self.run_hook(rows, "Tests pass."))
        outside = Path(self.tmp.name) / "elsewhere.json"
        outside.write_text('{"enabled": false}')
        self.env["SHOW_RECEIPTS_CONFIG"] = str(outside)
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", session_id="s2"))

    def test_repo_config_only_adds_ignore_paths(self):
        proj = Path(self.tmp.name) / "proj"
        proj.mkdir()
        self.write_user_config({"ignore_paths": ["/a/"], "max_blocks": 2})
        (proj / ".show-receipts.json").write_text(json.dumps({"ignore_paths": ["/gen/"], "enabled": False}))
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.xdg)}):
            os.environ.pop("SHOW_RECEIPTS_CONFIG", None)
            cfg = sr.load_config(str(proj))
        self.assertEqual(cfg, {"ignore_paths": ["/a/"], "max_blocks": 2, "project_ignore_dirs": ["gen"]})

    def test_repo_ignore_paths_are_folders_not_regexes(self):
        bad = [".", "/", "", "*", ".*", "..", "a|b", "src/../..", 3]
        self.assertEqual([sr._project_ignore_dir(x) for x in bad], [None] * len(bad))
        self.assertEqual(sr._project_ignore_dir("/generated/"), "generated")
        self.assertEqual(sr._project_ignore_dir("web/dist"), "web/dist")

    def test_repo_folders_match_from_the_project_root(self):
        rows = [user("x"), edit("/Users/me/proj/a.py"), bash("pytest", True, "1 passed")]
        rows += [edit("/Users/me/proj/src/a.py"), say("Tests pass.")]
        for dirs in (["Users"], ["me"], ["proj"], ["a.py/x"]):
            self.assertTrue(verdict(rows, config={"project_ignore_dirs": dirs}, cwd="/Users/me/proj").block, dirs)
        self.assertFalse(verdict(rows, config={"project_ignore_dirs": ["src"]}, cwd="/Users/me/proj").block)

    def test_home_pointing_into_the_project_is_ignored(self):
        rows = [user("fix"), edit()]
        proj = Path(self.tmp.name) / "proj"
        (proj / ".git").mkdir(parents=True)
        cfg = proj / ".config" / "show-receipts" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{"enabled": false}')
        self.env["HOME"] = str(proj)
        self.env.pop("XDG_CONFIG_HOME")
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", cwd=str(proj)))

    def test_repo_cannot_ignore_every_edit(self):
        rows = [user("fix"), edit()]
        proj = Path(self.tmp.name) / "proj"
        proj.mkdir()
        (proj / ".show-receipts.json").write_text(json.dumps({"ignore_paths": ["."]}))
        self.assertIsNotNone(self.run_hook(rows, "Tests pass.", cwd=str(proj)))

    def test_codex_payload_without_transcript_path(self):
        home = Path(self.tmp.name) / "codex"
        day = home / "sessions" / "2026" / "10" / "07"
        day.mkdir(parents=True)
        rows = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "FileChange", "status": "completed", "changes": {"/repo/a.py": {}}},
                },
            },
        ]
        (day / "rollout-2026-10-07T10-00-00-abc123.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.env["CODEX_HOME"] = str(home)
        out = self.run_hook([], "All tests pass.", session_id="abc123", transcript_path=None, turn_id="t1")
        self.assertEqual(out["decision"], "block")


# --------------------------------------------------------------------------
# Gemini CLI: session JSONL (chatRecordingService.ts) and the AfterAgent hook
# --------------------------------------------------------------------------


def _gemini_rows() -> list[dict]:
    return _load("gemini_pytest_proven.jsonl")


def _gemini_shell(cid: str, command: str, output: str, status: str = "success") -> dict:
    res = [{"functionResponse": {"id": cid, "name": "run_shell_command", "response": {"output": output}}}]
    return {"id": cid, "name": "run_shell_command", "args": {"command": command}, "status": status, "result": res}


def _gemini_msg(mid: str, text="", calls=()) -> dict:
    return {"id": mid, "type": "gemini", "content": text, "toolCalls": list(calls)}


class GeminiTests(unittest.TestCase):
    def parse(self, rows):
        with tempfile.TemporaryDirectory() as d:
            return sr.parse_transcript(write_jsonl(rows, d))

    def verdict(self, rows):
        with tempfile.TemporaryDirectory() as d:
            p = write_jsonl(rows, d)
            tr = sr.parse_transcript(p)
            return sr.evaluate(tr, tr.last_assistant_text, cwd="/home/dev/projects/tiny-cache")

    def test_fixture_is_proven(self):
        self.assertFalse(self.verdict(_gemini_rows()).block)

    def test_fixture_without_the_passing_run_is_blocked(self):
        rows = [r for r in _gemini_rows() if not any(c.get("id") == "shell-2" for c in r.get("toolCalls") or [])]
        v = self.verdict(rows)
        self.assertTrue(v.block)
        self.assertIn("tests", v.missing)

    def test_failing_exit_code_is_a_failed_run(self):
        rows = _gemini_rows()[:2]
        rows.append(
            _gemini_msg(
                "e",
                calls=[
                    {
                        "id": "r",
                        "name": "write_file",
                        "args": {"file_path": "/home/dev/projects/tiny-cache/a.py"},
                        "status": "success",
                    }
                ],
            )
        )
        rows.append(_gemini_msg("t", "Tests pass.", [_gemini_shell("s", "pytest", "Output: 1 failed\nExit Code: 1")]))
        v = self.verdict(rows)
        self.assertTrue(v.block)
        self.assertIn("tests", v.failed)

    def test_later_message_versions_replace_earlier_ones(self):
        # The CLI re-appends a whole message under the same id when it changes.
        rows = [
            *_gemini_rows()[:2],
            _gemini_msg("m", calls=[_gemini_shell("s", "pytest", "Output: 2 passed")]),
            _gemini_msg(
                "m",
                calls=[
                    _gemini_shell("s", "pytest", "Output: 2 passed"),
                    {
                        "id": "w",
                        "name": "replace",
                        "args": {"file_path": "/home/dev/projects/tiny-cache/a.py"},
                        "status": "success",
                    },
                ],
            ),
            _gemini_msg("f", "All tests pass."),
        ]
        tr = self.parse(rows)
        self.assertEqual([e.kind for e in tr.events], ["cmd", "edit"])
        self.assertTrue(self.verdict(rows).block)

    def test_rewind_and_patch_records(self):
        rows = [
            *_gemini_rows()[:2],
            _gemini_msg(
                "m1", calls=[{"id": "w", "name": "replace", "args": {"file_path": "/x/a.py"}, "status": "success"}]
            ),
            {"$rewindTo": "m1"},
            _gemini_msg("m2", calls=[_gemini_shell("s", "pytest", "")]),
            {
                "$patch": {
                    "id": "m2",
                    "toolCalls": [
                        {
                            "id": "s",
                            "result": [{"functionResponse": {"response": {"output": "Output: x\nExit Code: 2"}}}],
                        }
                    ],
                }
            },
        ]
        tr = self.parse(rows)
        self.assertEqual([(e.kind, e.ok) for e in tr.events], [("cmd", False)])

    def test_background_and_cancelled_commands(self):
        rows = [
            *_gemini_rows()[:2],
            _gemini_msg(
                "m",
                calls=[
                    dict(
                        _gemini_shell("a", "pytest", "Command moved to background (PID: 9)."),
                        args={"command": "pytest", "is_background": True},
                    ),
                    _gemini_shell("b", "pytest", "Command was cancelled by user", status="cancelled"),
                ],
            ),
        ]
        tr = self.parse(rows)
        self.assertEqual([e.ok for e in tr.events], [None, False])

    def test_delegate_and_legacy_whole_file(self):
        legacy = {
            "sessionId": "s",
            "projectHash": "h",
            "messages": [
                {"id": "u", "type": "user", "content": "go"},
                _gemini_msg(
                    "a",
                    calls=[{"id": "w", "name": "write_file", "args": {"file_path": "/x/a.py"}, "status": "success"}],
                ),
                _gemini_msg("b", "Tests pass.", [{"id": "i", "name": "invoke_agent", "args": {}, "status": "success"}]),
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "session-legacy.json"
            p.write_text(json.dumps(legacy, indent=2))
            tr = sr.parse_transcript(p)
        self.assertEqual([e.kind for e in tr.events], ["edit", "delegate"])
        self.assertEqual(tr.last_assistant_text, "Tests pass.")

    def test_claude_and_codex_are_not_mistaken_for_gemini(self):
        for name in ("claude_pytest_proven.jsonl", "codex_proven.jsonl"):
            self.assertFalse(any(sr._is_gemini(o) for o in _load(name)[:3]), name)


class GeminiHookTests(unittest.TestCase):
    """The AfterAgent payload: prompt_response instead of last_assistant_message."""

    setUp = HookTests.setUp
    tearDown = HookTests.tearDown
    run_hook = HookTests.run_hook

    def gemini(self, rows, response, **payload):
        data = {
            "hook_event_name": "AfterAgent",
            "prompt": "fix it",
            "prompt_response": response,
            "timestamp": "2026-10-17T09:00:16Z",
        }
        data.update(payload)
        return self.run_hook(rows, None, **data)

    def test_proven_fixture_is_allowed(self):
        rows = _gemini_rows()
        self.assertIsNone(self.gemini(rows, "All 3 tests pass."))

    def test_unproven_blocks_once_then_allows_the_retry(self):
        rows = [r for r in _gemini_rows() if not any(c.get("id") == "shell-2" for c in r.get("toolCalls") or [])]
        out = self.gemini(rows, "All 3 tests pass.")
        self.assertEqual(out["decision"], "block")
        # Gemini sends our reason back as a new user message, then fires AfterAgent with stop_hook_active.
        retry = [*rows, {"id": "u-retry", "type": "user", "content": [{"text": out["reason"]}]}]
        self.assertIsNone(self.gemini(retry, "All 3 tests pass.", stop_hook_active=True))

    def test_odd_payloads_allow(self):
        for extra in (
            {"prompt_response": 5},
            {"transcript_path": ""},
            {"stop_hook_active": "yes", "prompt_response": None},
        ):
            self.assertIsNone(self.gemini([{"sessionId": "s", "projectHash": "h"}], "Tests pass.", **extra))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("sh") and os.name != "nt", "needs a POSIX shell")
class InstallScriptTests(unittest.TestCase):
    def run_install(self, cwd, *args):
        env = dict(os.environ, SHOW_RECEIPTS_SRC=str(HOOK))
        return subprocess.run(["sh", str(ROOT / "install.sh"), *args], cwd=cwd, env=env, capture_output=True, text=True)

    def settings(self, d):
        return json.loads((Path(d) / ".claude" / "settings.json").read_text())

    def test_fresh_install_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_install(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((Path(d) / ".claude" / "hooks" / "show_receipts.py").exists())
            first = self.settings(d)
            self.assertEqual(len(first["hooks"]["Stop"]), 1)
            r = self.run_install(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.settings(d), first)
            self.assertEqual(list((Path(d) / ".claude").glob("settings.json.bak-*")), [])

    def test_merges_without_clobbering_and_backs_up(self):
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir()
            old = {
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "echo bye"}]}],
                    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}],
                },
            }
            (claude / "settings.json").write_text(json.dumps(old))
            r = self.run_install(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            new = self.settings(d)
            self.assertEqual(new["permissions"], old["permissions"])
            self.assertEqual(new["hooks"]["PreToolUse"], old["hooks"]["PreToolUse"])
            self.assertEqual(new["hooks"]["Stop"][0], old["hooks"]["Stop"][0])
            self.assertIn("show_receipts.py", new["hooks"]["Stop"][1]["hooks"][0]["command"])
            backups = list(claude.glob("settings.json.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text()), old)

    def test_broken_settings_are_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir()
            (claude / "settings.json").write_text("{ not json")
            r = self.run_install(d)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual((claude / "settings.json").read_text(), "{ not json")

    def test_global_uses_config_dir(self):
        with tempfile.TemporaryDirectory() as d:
            env_dir = Path(d) / "cfg"
            env = dict(os.environ, SHOW_RECEIPTS_SRC=str(HOOK), CLAUDE_CONFIG_DIR=str(env_dir))
            r = subprocess.run(
                ["sh", str(ROOT / "install.sh"), "--global"], cwd=d, env=env, capture_output=True, text=True
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((env_dir / "hooks" / "show_receipts.py").exists())
            cmd = json.loads((env_dir / "settings.json").read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
            self.assertIn(str(env_dir), cmd)
