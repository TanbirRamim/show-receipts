# Contributing

Thanks for helping. The hook is one file, `hooks/show_receipts.py`, standard library only, Python 3.9+. Please keep it that way.

## Run the checks

```sh
python3 -m unittest discover tests
uvx ruff check .
uvx ruff format --check .
```

## The one rule

A false block is worse than a missed one. If a change makes the hook block in a case where the agent did run the check, it won't be merged. When in doubt, allow.

## False positive or missed claim?

Best report: the final message plus the commands that ran (or `python3 hooks/show_receipts.py check --explain <session.jsonl>`). Even better, a fixture. `scripts/make_fixture.py` cuts one turn out of your own transcript and scrubs it:

```sh
python3 scripts/make_fixture.py ~/.claude/projects/<project>/<session>.jsonl <turn> tests/fixtures/my_case.jsonl --scrub yourname yourcompany
```

It keeps only check commands, trims outputs and replaces home paths, emails, URLs and tokens. Read the file before you commit it anyway. The fixtures that ship with the repo are hand-written synthetic transcripts in the same format, not cut from anyone's sessions.

You can also see what the hook would have done across all your past sessions:

```sh
python3 hooks/show_receipts.py replay ~/.claude/projects/*/*.jsonl
```

## New test runner or build tool

Add it to the sets near the top of the commands section and add a line to `CommandTests`. If it's a script name only your project uses, the `extra_commands` config is the better place.
