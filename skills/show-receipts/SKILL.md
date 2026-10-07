---
name: show-receipts
description: Use before ending any turn where you changed code and are about to say tests pass, the build works, lint is clean, or the task is done. Run the check, then show the command and its exit code.
---

# Show receipts

Don't say tests pass, the build works or lint is clean unless you ran the command in this session, after your last code edit, and it exited 0.

Before you finish:

1. If you edited code, run the project's real check (tests, build, lint, whatever applies) after the last edit.
2. End with a short receipts block for what you ran:

   ```
   Receipts
   - pytest -q            exit 0   41 passed
   - ruff check .         exit 0
   ```

3. If you couldn't run something, say so in plain words ("I didn't run the tests because the database isn't available here") instead of claiming it passes.
4. If a run failed, say it failed. Don't round "3 failed, 38 passed" up to "tests pass".

The show-receipts Stop hook checks this. If you claim a pass without a matching run after your last edit, it sends you back to run it.
