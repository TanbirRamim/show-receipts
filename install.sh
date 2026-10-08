#!/bin/sh
# Install the show-receipts Stop hook for Claude Code without npx or the plugin system.
#
#   curl -fsSL https://raw.githubusercontent.com/TanbirRamim/show-receipts/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --global     # ~/.claude instead of ./.claude
#
# Safe to run again: it updates the hook script, adds the settings entry only once,
# keeps everything else in settings.json and backs the old file up first.
set -eu

URL="https://raw.githubusercontent.com/TanbirRamim/show-receipts/main/hooks/show_receipts.py"
scope=project
for arg in "$@"; do
  case "$arg" in
    --global) scope=global ;;
    --project) scope=project ;;
    -h | --help)
      echo "usage: install.sh [--project | --global]"
      exit 0
      ;;
    *)
      echo "install.sh: unknown option $arg" >&2
      exit 2
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || {
  echo "install.sh: python3 is needed (the hook is a Python script)" >&2
  exit 1
}

if [ "$scope" = global ]; then
  base="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  cmd='python3 "$HOME/.claude/hooks/show_receipts.py"'
  [ -n "${CLAUDE_CONFIG_DIR:-}" ] && cmd="python3 \"$base/hooks/show_receipts.py\""
else
  base=".claude"
  cmd='python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/show_receipts.py"'
fi

mkdir -p "$base/hooks"
tmp="$base/hooks/.show_receipts.py.tmp"
if [ -n "${SHOW_RECEIPTS_SRC:-}" ]; then
  cp "$SHOW_RECEIPTS_SRC" "$tmp"
else
  curl -fsSL "$URL" -o "$tmp"
fi
python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$tmp" || {
  rm -f "$tmp"
  echo "install.sh: downloaded hook is not valid Python, nothing changed" >&2
  exit 1
}
mv "$tmp" "$base/hooks/show_receipts.py"

python3 - "$base/settings.json" "$cmd" <<'PY'
import json, os, shutil, sys, time

path, cmd = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            text = f.read()
        data = json.loads(text) if text.strip() else {}
    except ValueError as e:
        sys.exit(f"install.sh: {path} is not valid JSON ({e}); fix it first, nothing changed")
    if not isinstance(data, dict):
        sys.exit(f"install.sh: {path} is not a JSON object, nothing changed")

hooks = data.setdefault("hooks", {})
stop = hooks.setdefault("Stop", []) if isinstance(hooks, dict) else None
if not isinstance(stop, list):
    sys.exit(f"install.sh: unexpected hooks layout in {path}, nothing changed")
for group in stop:
    for h in (group or {}).get("hooks", []) if isinstance(group, dict) else []:
        if "show_receipts.py" in str(h.get("command", "")):
            print(f"{path} already runs show-receipts, left it alone")
            sys.exit(0)

stop.append({"hooks": [{"type": "command", "command": cmd, "timeout": 30}]})
if os.path.exists(path):
    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    print(f"backed up {path} to {backup}")
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print(f"added the show-receipts Stop hook to {path}")
PY

echo "done. It applies from your next Claude Code session."
