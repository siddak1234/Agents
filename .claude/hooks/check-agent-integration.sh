#!/usr/bin/env bash
# Fires after Claude writes or edits a file. If that file was an agent
# manifest, re-run the integration gate and report failures back.
#
# Why a hook and not a rule: a rule is context Claude may or may not act on. A
# hook exits non-zero, and the failure has to be dealt with. Rules guide,
# hooks enforce — this is the enforcing half.
#
# Exit codes are the contract with Claude Code:
#   0  proceed silently
#   2  block, and show stderr to Claude so it fixes what is reported
# Anything else is treated as a non-blocking error, which is why every
# unexpected path below exits 0 rather than failing the contributor's session
# over a problem with this script.

set -uo pipefail

payload=$(cat)

# The hook fires on every write. Only manifests are our business.
path=$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
i = d.get("tool_input") or {}
print(i.get("file_path") or i.get("path") or "")
' 2>/dev/null) || exit 0

case "$path" in
  */agent.yaml|agent.yaml|*/registry.yaml|registry.yaml) ;;
  *) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -d "$root" ] || exit 0
cd "$root" || exit 0

# No environment yet — a contributor mid-setup should not be blocked by this.
command -v uv >/dev/null 2>&1 || exit 0

if out=$(uv run --frozen agents list --strict 2>&1); then
  exit 0
fi

{
  echo "This agent is registered but not yet integrated:"
  echo
  printf '%s\n' "$out" | sed 's/^/  /'
  echo
  echo "Work through each line. 'agents list --strict' is the gate CI runs."
} >&2
exit 2
