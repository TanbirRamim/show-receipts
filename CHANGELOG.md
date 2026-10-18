# Changelog

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
