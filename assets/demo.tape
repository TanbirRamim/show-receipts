# vhs assets/demo.tape renders frames to demo-frames/, then ffmpeg overlays text+cursor into assets/demo.gif (see log)
Output demo-frames/
Set Shell bash
Set FontSize 18
Set Width 1200
Set Height 560
Set Padding 24
Set TypingSpeed 35ms
Set Theme "Builtin Dark"
Hide
Type "export PS1='$ '; grep -v 'tool_use' tests/fixtures/claude_pytest_proven.jsonl | grep -v tool_result > /tmp/no_receipts.jsonl; clear" Enter
Show
Type "# same session, but the agent never ran the tests" Enter
Sleep 400ms
Type "python3 hooks/show_receipts.py check /tmp/no_receipts.jsonl" Enter
Sleep 3s
Type "# the real session, where pytest ran after the last edit" Enter
Sleep 400ms
Type "python3 hooks/show_receipts.py check tests/fixtures/claude_pytest_proven.jsonl" Enter
Sleep 3500ms
