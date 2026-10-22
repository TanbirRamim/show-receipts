# show-receipts

Your agent says the tests pass. show-receipts makes it prove it.

[![CI](https://github.com/TanbirRamim/show-receipts/actions/workflows/ci.yml/badge.svg)](https://github.com/TanbirRamim/show-receipts/actions/workflows/ci.yml) [![MIT](https://img.shields.io/badge/license-MIT-111111)](LICENSE)

In Claude Code:

```
/plugin marketplace add TanbirRamim/show-receipts
/plugin install show-receipts@show-receipts
```

Or from your shell: `claude plugin marketplace add TanbirRamim/show-receipts && claude plugin install show-receipts@show-receipts`. The hook applies from your next session. Codex, Gemini CLI and manual setups are [below](#other-ways-to-install).

![A real Claude Code run: the agent says "All tests pass." without running anything, show-receipts blocks the stop, and the agent admits it never ran the tests](assets/demo.svg)

## What it does

It's a Stop hook. When the agent is about to end its turn with "all tests pass", "the build succeeds" or "lint is clean", the hook reads the session transcript and looks for a matching command that ran after the last code edit and exited 0.

- Found one: the agent stops as normal. You never notice it.
- Didn't find one, or the last run failed: the stop is blocked and the agent is told which sentence has no receipt. It goes back and runs the check, or says plainly that it couldn't.

Before:

> I fixed `add` in calc.py. All tests pass.

(nothing ran)

After:

> Stop hook feedback: You said "All tests pass.", but no test command ran after your last code edit.
>
> I didn't run any tests, so I have no command or exit code to report. My earlier "All tests pass." wasn't a result I observed.

That's from a real run, shown in the image above. I'd told it not to run anything, so owning up was the honest way out. With permission to run tests, the block message asks it to go run them.

No LLM judging the LLM. It's one file of standard-library Python (about 900 lines) you can read in one sitting, it never calls the network, and it never blocks because of its own errors.

## Why

Every agent user has been told "all tests pass" by an agent that didn't run them, or ran them before its last edit, or piped them through `tail` and missed the failures. Telling it "always run the tests" in CLAUDE.md works until the context gets long. A hook doesn't forget.

## What counts as a receipt

A claim is matched to commands by kind:

| claim | commands that count |
|---|---|
| tests pass | pytest, unittest, jest, vitest, mocha, `go test`, `cargo test`, `npm test`, `make test`, rspec, `dotnet test`, `./scripts/run_tests.sh`, and so on |
| build succeeds | `npm run build`, `cargo build`, `go build`, `tsc`, `make`, `next build`, ... and any test run |
| lint is clean | ruff, eslint, mypy, pyright, `tsc --noEmit`, clippy, `go vet`, `npm run lint`, ... |

`npm run ci`, `make check` and similar count for all three. The run has to come after the last code edit and exit 0. If the exit code was 0 but the output says `2 failed` (a pipe through `tail` does that), it counts as failed.

Edits are Edit/Write tool calls, Codex file changes and `apply_patch`, Gemini CLI `write_file`/`replace` calls, and files Claude Code saw a Bash command change (`sed -i`, heredocs). Edits to docs (`*.md`, `docs/`, CHANGELOG), lockfiles, build output and files outside the project don't reset the clock. Neither do `git pull` or `git checkout`.

## False positives

A wrong block is worse than a missed claim, so it leans hard toward letting things through:

- Only confident sentences count. "Tests should pass", "I couldn't run the tests", "the tests pass in CI", "were passing before", questions, quoted text and code blocks are all ignored.
- One green run after the last edit is enough, even if a later run failed on purpose.
- A compound command that failed in some other step (`pytest; curl ...`) is treated as unknown, which means allowed.
- If the agent handed work to a subagent after the last edit, or a test run is still in the background, it allows.
- It blocks at most once per prompt. If the agent stops again with the same claim, it goes through. No loops.

I replayed it over 782 real turns from my own Claude Code and Codex history. 41 ended with a pass claim, and it would have blocked 0 of them, because all 41 were backed by a run. You can do the same with yours:

![Running the checker on a transcript with the test runs cut out, then on the real one](assets/demo.gif)


```sh
python3 hooks/show_receipts.py replay ~/.claude/projects/*/*.jsonl
```

## Honest limits

- It checks that a check ran and passed, not that the tests are any good. An agent that deletes the failing test and runs the rest gets through.
- It only knows the commands in its lists. A custom runner needs `extra_commands` in the config, otherwise a claim backed by it would be blocked once.
- It can't see inside subagents or background jobs, so it allows in those cases.
- "Fixed" and "done" aren't checked. They're too vague to judge without a lot of false blocks.
- Needs `python3` on PATH. Windows is only covered by CI so far.
- Codex support is tested on Codex-format transcripts, but I haven't run it live inside Codex yet. The same goes for Gemini CLI: the adapter follows its [hooks reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md) and session format, tested with synthetic fixtures.

## Other ways to install

**Claude Code, one project only.** Run this in the repo root:

```sh
curl -fsSL https://raw.githubusercontent.com/TanbirRamim/show-receipts/main/install.sh | sh
```

It copies the hook to `.claude/hooks/` and adds it to `.claude/settings.json` without touching your other settings (the old file is backed up first). Add `-s -- --global` after `sh` to install for every project in `~/.claude` instead. Rather do it by hand? Copy `hooks/show_receipts.py` to `.claude/hooks/` and merge [examples/claude-settings.json](examples/claude-settings.json).

**Codex.** Put the script somewhere stable and register it as a Stop hook in `~/.codex/hooks.json` (or a trusted project's `.codex/hooks.json`):

```sh
curl -fsSL https://raw.githubusercontent.com/TanbirRamim/show-receipts/main/hooks/show_receipts.py -o ~/.codex/show_receipts.py
```

and use [examples/codex-hooks.json](examples/codex-hooks.json).

**Gemini CLI.** Put the script somewhere stable and register it as an `AfterAgent` hook in `~/.gemini/settings.json` (or a project's `.gemini/settings.json`):

```sh
curl -fsSL https://raw.githubusercontent.com/TanbirRamim/show-receipts/main/hooks/show_receipts.py -o ~/.gemini/show_receipts.py
```

and merge [examples/gemini-settings.json](examples/gemini-settings.json) (Gemini's `timeout` is in milliseconds). An unproven claim is rejected once and the reason goes back to the agent as a new prompt; the retry is let through.

**Just the skill, any agent.** `npx skills add TanbirRamim/show-receipts` installs the skill (the receipts habit) into Claude Code, Codex, Cursor and others. That's only the instruction, not the hook, so nothing blocks; use the plugin above if you want the enforcement.

**Anything else (Cursor, Copilot, Aider...).** No Stop hook there, so paste [AGENTS.snippet.md](AGENTS.snippet.md) into your AGENTS.md. It's the same rule, just without the enforcement.

The plugin also ships a small skill that asks the agent to end with a receipts block (command, exit code, result), so it gets it right the first time instead of getting sent back.

## Config

Optional. Your settings live in your own config, `~/.config/show-receipts/config.json` (or `$XDG_CONFIG_HOME/show-receipts/config.json`, or another file in that folder named by `SHOW_RECEIPTS_CONFIG`):

```json
{
  "claims": ["tests", "build", "lint"],
  "max_blocks": 1,
  "extra_commands": { "tests": ["^\\./acme verify"] },
  "ignore_paths": ["/generated/"],
  "disabled_projects": ["~/work/legacy-app"]
}
```

`"enabled": false` turns it off everywhere, `disabled_projects` turns it off for those folders. `SHOW_RECEIPTS_DEBUG=1` prints errors to stderr instead of silently allowing.

A repository can ship a `.show-receipts.json` too, but only its `ignore_paths` are read, and there they're plain folder names matched from the project root (`"generated"`, `"web/dist"`), not regexes, so a repo can't ignore every edit with `"."`. Regexes in `ignore_paths` work in your own config. A repo you just cloned can't switch the check off or decide which commands count as proof. That also covers environment variables a project sets for the agent: `SHOW_RECEIPTS_CONFIG` is only used when it points under your config folder, and neither it, `XDG_CONFIG_HOME`, `HOME` nor `CODEX_HOME` is used when it points into the project. There's no environment variable that turns the check off, for the same reason.

Check one transcript by hand:

```sh
python3 hooks/show_receipts.py check path/to/session.jsonl
# or inspect claims, the last counted edit, and candidate commands:
python3 hooks/show_receipts.py check --explain path/to/session.jsonl
```

### Auditing past sessions

To audit past sessions in CI, a nightly cron job, or before enabling the hook:

```sh
python3 hooks/show_receipts.py replay ~/.claude/projects/*/*.jsonl
```

Or over the repository fixtures:

```sh
python3 hooks/show_receipts.py replay tests/fixtures/*.jsonl
```

`replay` walks each transcript, evaluates every finished turn, and prints the block reason for any turn that had an unproven claim. At the end, it prints a summary:

```
4 turns, 4 with pass claims, 0 would be blocked
```

- **turns**: total assistant turns analyzed.
- **with pass claims**: turns where the agent made a confident claim that tests, builds, or linters pass.
- **would be blocked**: turns where no matching proof ran after the last code edit, or the check failed.

## Why did it block me?

If the hook blocked a stop and you think it shouldn't have:

1. **The check ran before the last code edit.** A receipt must come *after* the last code modification. If the agent ran the test, then edited a file, the clock reset and the test needs to run again. (Doc edits, notes, and files outside the project do not reset the clock.)
2. **Piping through a command that hides exit codes.** Piping a failing check through `tail`, `grep`, or `cat` in bash returns the pipe's exit code unless `set -o pipefail` is active. While show-receipts inspects output for known failure patterns (like `1 failed`), clean test runners without pipes provide the most reliable receipts.
3. **Custom runner or command.** If your project uses an unusual runner script (e.g. `./run-tests` or `make check-fast`), add it to `extra_commands` in `~/.config/show-receipts/config.json`:
   ```json
   {
     "extra_commands": {
       "tests": ["^\\./run-tests"]
     }
   }
   ```
4. **Debugging hook execution.** Set `SHOW_RECEIPTS_DEBUG=1` in your environment to print errors to stderr instead of letting the hook silently allow.

## Contributing

False-positive reports are the most useful thing you can send. See [CONTRIBUTING.md](CONTRIBUTING.md), there's a script that turns a turn from your own transcript into a scrubbed test fixture.

MIT license.
