# Changelog

## Unreleased

- Config typos no longer switch the check off. A bare string in `ignore_paths` or `extra_commands` was read one character per regex (so `"generated"` ignored almost every edit), and a regex that didn't compile crashed the hook, which then allowed every stop. Now a string is one pattern and a bad regex is skipped (printed with `SHOW_RECEIPTS_DEBUG=1`). A non-number `max_blocks` falls back to 1.
- `check` and `replay` print a short error for a missing transcript instead of a traceback.

## 0.5.1 (2026-10-23)

- Gemini CLI: a `run_shell_command` exit code is now read only from the trailer the CLI adds after the output. Before, a command that printed `Exit Code: 1` itself (or a run that failed after printing `Exit Code: 0`) got the wrong status. Also a real-schema fixture covering `$patch` results, re-appended messages and `$rewindTo`.

## 0.5.0 (2026-10-23)

- `check --explain` prints the claims it found, the last edit and which tool made it, each test/build/lint run with its status, and the verdict, so you can see why a session was blocked or let through. It reads the same receipts the verdict uses (#20, thanks @chiragjangiir).
- German claim phrases and hedges, so German sessions get checked too (#24, thanks @chiragjangiir).
- `yamllint`, `hlint`, `luacheck`, `tflint`, `sqlfluff lint`, `buf lint` and `swiftformat --lint` now count as lint runs. `sqlfluff`/`buf` only with the `lint` subcommand and `swiftformat` only with `--lint`, since their other modes rewrite or build (#18, thanks @chiragjangiir).
- Transcript fixtures for blocked cases, so the tests cover real sessions that should be stopped (#22, thanks @chiragjangiir).
- Docs on auditing a session with `replay`, plus a troubleshooting section (#21, thanks @chiragjangiir).

## 0.4.0 (2026-10-18)

- Gemini CLI support: register the hook as an `AfterAgent` hook (see `examples/gemini-settings.json`). It reads Gemini's session files, both the JSONL kind and older single-JSON ones, counts `run_shell_command` runs by their exit code and `write_file`/`replace` as edits, and blocks at most `max_blocks` times per prompt even though Gemini sends the reason back as a new message (#8).

## 0.3.4 (2026-10-12)

- Edits to `CMakeLists.txt`, `requirements*.txt`, `constraints*.txt` and code files under `docs/` (like Sphinx `conf.py`) now count as code edits. Before, they were treated as docs, so a "tests pass" after changing them went unchecked.

## 0.3.3 (2026-10-12)

- `SHOW_RECEIPTS_DISABLE` is gone, since a project's agent settings can set environment variables. Use `"enabled": false` or `disabled_projects` in your own config instead.
- A repo's ignore folders now match from the project root, so an entry like `"Users"` or `"home"` can't hide every edit.
- The user config and Codex sessions are found through your real home folder, and `HOME`/`CODEX_HOME` pointing into the project are ignored.

## 0.3.2 (2026-10-12)

- `ignore_paths` in a repo's `.show-receipts.json` are now folder names matched literally, not regexes. Before, `"."` there ignored every edit, which turned the check off. Your own config still takes regexes, and those now also match relative paths.

## 0.3.1 (2026-10-12)

- `SHOW_RECEIPTS_CONFIG` is only honored when it points under your config folder and outside the project, and an `XDG_CONFIG_HOME` inside the project is ignored, so a repo can't use project-level env settings to load its own config. Thanks again to @davepoon.

## 0.3.0 (2026-10-12)

- Settings moved to your own config (`~/.config/show-receipts/config.json`). A repo's `.show-receipts.json` can now only add `ignore_paths`; it can no longer turn the check off, change the claims, `max_blocks` or `extra_commands`. Use `disabled_projects` in your config to turn it off for a folder. Thanks to @davepoon for pointing this out.
- Counts gotestsum, behave, cucumber-js, elm-test and dune test/runtest as test runs (@jay23606). `elm-test init` no longer counts. `uv run -- pytest`, `npx -- jest` and `opam exec -- dune test` now work too.

## 0.2.0 (2026-10-10)

- Catches more ways of saying it passed: "all green", "everything passes", "the suite is green", "pytest is happy", "type-checks fine", "tsc reports no errors", "no clippy warnings", "it compiles now", "verified the fix works". Hedged lines like "haven't verified" or "make the suite green" still pass through.
- More runners count as receipts: `bun run test`, `deno task test`, `deno lint`/`check`/`fmt --check`, `yarn workspace <name> test`, `yarn workspaces foreach run test`, `php artisan test`, `mix credo`. Most others (pnpm, nx, turbo, uv, poetry, tox, nox, gradle, mvn, swift, zig, phpunit, pest, mix) already worked and now have tests.
- `install.sh`: one-line install without npx or the plugin system. Copies the hook and adds it to `.claude/settings.json` (or `~/.claude` with `--global`), keeps your other settings, backs the old file up and does nothing on a second run.

## 0.1.0 (2026-10-08)

- First version: Stop hook for Claude Code and Codex that blocks unproven "tests pass", "build succeeds" and "lint is clean" claims.
- `check` and `replay` commands to try it on your own transcripts.
- Skill and AGENTS.md snippet that ask the agent to end with a receipts block.
