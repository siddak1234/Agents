#!/usr/bin/env bash
# Deterministic half of /review-pr: fetch a pull request into an isolated
# worktree, run every gate CI runs against it, tear it down.
#
# Why a script and not model-driven Bash calls: none of this needs judgement,
# and a reviewer that re-derives it each run drifts. Same split as
# .claude/hooks/ — the deterministic part is code, the judgement is prose.
#
# Two failure modes this exists to prevent, both observed:
#   * Reviewing the wrong commit. FETCH_HEAD is shared mutable state; a second
#     fetch between write and read silently yields a worktree at the wrong SHA
#     and a full set of green gates that mean nothing. Every fetch here goes to
#     a namespaced ref, and `prepare` refuses to continue unless the worktree
#     HEAD equals what GitHub reports as the head of the pull request.
#   * Touching the contributor's checkout. Never `gh pr checkout`; the pull
#     request is only ever read through a detached worktree outside the repo.
#
# Subcommands: prepare <n> | gates <n> | teardown [<n>]
# Facts go to stdout as JSON. Diagnostics go to stderr.

set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
WORKTREE_HOME="${TMPDIR:-/tmp}"
WORKTREE_HOME="${WORKTREE_HOME%/}/agents-review-pr"
ref_for() { echo "refs/review-pr/$1"; }
tree_for() { echo "$WORKTREE_HOME/pr-$1"; }

die() { echo "pr-setup: $*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is not installed"; }

json() { python3 -c 'import json,sys; print(json.dumps(json.loads(sys.stdin.read()), indent=2))'; }

# --- prepare ---------------------------------------------------------------
# Emits the facts every later step depends on. Exits non-zero rather than
# emitting a worktree it could not verify.
prepare() {
  local n=$1 tree ref head_sha reported
  need gh; need git
  tree=$(tree_for "$n"); ref=$(ref_for "$n")

  reported=$(gh pr view "$n" --json headRefOid -q .headRefOid) \
    || die "cannot read pull request $n"
  [ -n "$reported" ] || die "pull request $n reported no head commit"

  # Clear any leftovers first: teardown deletes the ref, so it has to happen
  # before the fetch that creates it, not after.
  teardown "$n" >/dev/null 2>&1 || true

  git -C "$ROOT" fetch -q origin main || die "cannot fetch origin main"
  git -C "$ROOT" fetch -q --force origin "refs/pull/$n/head:$ref" \
    || die "cannot fetch head of pull request $n"

  mkdir -p "$WORKTREE_HOME"
  git -C "$ROOT" worktree add -q --detach "$tree" "$ref" \
    || die "cannot create worktree at $tree"

  head_sha=$(git -C "$tree" rev-parse HEAD)
  # The assertion this script exists for. Never gate a commit GitHub does not
  # agree is the head of the pull request. Remove the worktree before failing:
  # a leftover at the wrong SHA is worse than none, because `gates` would find
  # it and review it without complaint.
  if [ "$head_sha" != "$reported" ]; then
    teardown "$n" >/dev/null 2>&1 || true
    die "worktree is at $head_sha but GitHub reports $reported — refusing to review the wrong commit"
  fi

  ROOT="$ROOT" TREE="$tree" N="$n" HEAD_SHA="$head_sha" python3 - <<'PY' | json
import json, os, subprocess
root, tree, n = os.environ["ROOT"], os.environ["TREE"], os.environ["N"]
def git(*a): return subprocess.run(["git","-C",tree,*a],capture_output=True,text=True).stdout.strip()
changed = [p for p in git("diff","--name-only","origin/main...HEAD").splitlines() if p]
agents = sorted({p.split("/")[1] for p in changed
                 if p.startswith("agents/") and p.count("/") >= 2
                 and not p.split("/")[1].startswith("_")})
print(json.dumps({
    "pr": int(n), "worktree": tree,
    "head_sha": os.environ["HEAD_SHA"],
    "base_sha": git("rev-parse","origin/main"),
    "changed_files": changed,
    "agents_touched": agents,
    "registry_changed": "registry.yaml" in changed,
    "readme_changed": "README.md" in changed,
    "docs_changed": [p for p in changed if p.startswith("docs/")],
}))
PY
}

# --- gates -----------------------------------------------------------------
# Every deterministic gate CI runs, against the pull request's tree. Each
# result carries its exit code so the caller never parses prose for pass/fail.
gates() {
  local n=$1 tree agent head_sha reported
  tree=$(tree_for "$n")
  [ -d "$tree" ] || die "no worktree for pull request $n — run prepare first"

  # Defence in depth: never trust a worktree this invocation did not build.
  # A stale one can outlive a failed prepare, or the contributor can push
  # between prepare and gates, and either way the gates would pass against a
  # commit nobody is reviewing.
  head_sha=$(git -C "$tree" rev-parse HEAD)
  reported=$(gh pr view "$n" --json headRefOid -q .headRefOid) || die "cannot read pull request $n"
  [ "$head_sha" = "$reported" ] \
    || die "worktree is at $head_sha but the pull request head is now $reported — run prepare again"

  agent=$(git -C "$tree" diff --name-only origin/main...HEAD \
          | awk -F/ '$1=="agents" && NF>2 && $2 !~ /^_/ {print $2}' | sort -u | head -1)

  run() {  # run <label> <cmd...>
    local label=$1; shift
    local out rc
    out=$("$@" 2>&1) && rc=0 || rc=$?
    LABEL="$label" OUT="$out" RC="$rc" python3 -c '
import json,os; print(json.dumps({"label":os.environ["LABEL"],
"exit":int(os.environ["RC"]),"output":os.environ["OUT"][-4000:]}))'
  }

  {
    run scope  uv run agents --root "$tree" scope --base origin/main
    run strict uv run agents --root "$tree" list --strict
    if [ -n "$agent" ]; then
      run check uv run agents --root "$tree" check "$agent"
      run lint  uv run agents --root "$tree" lint  "$agent"
      run test  uv run agents --root "$tree" test  "$agent"
    fi
    run meta gh pr view "$n" --json \
      files,author,body,title,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,isCrossRepository
    conflicts "$n"
  } | python3 -c '
import json,sys
res={}
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    d=json.loads(line); res[d.pop("label")]=d
print(json.dumps(res, indent=2))'
}

# Would landing another open pull request first put this one in conflict?
conflicts() {
  local n=$1 mine others merged tmp
  mine=$(git -C "$ROOT" rev-parse "$(ref_for "$n")")
  others=$(gh pr list --state open --json number -q '.[].number' | grep -v "^${n}$" || true)
  out="[]"
  if [ -n "$others" ]; then
    out=$(N="$n" MINE="$mine" ROOT="$ROOT" OTHERS="$others" python3 - <<'PY'
import json, os, subprocess
root, mine = os.environ["ROOT"], os.environ["MINE"]
def git(*a):
    r = subprocess.run(["git","-C",root,*a],capture_output=True,text=True)
    return r.returncode, r.stdout.strip()
out=[]
for other in os.environ["OTHERS"].split():
    rc,_ = git("fetch","-q","--force","origin",f"refs/pull/{other}/head:refs/review-pr/{other}")
    if rc: continue
    rc, tree = git("merge-tree","--write-tree","main",f"refs/review-pr/{other}")
    if rc: continue
    rc, commit = git("commit-tree",tree,"-p","main","-p",f"refs/review-pr/{other}","-m","probe")
    if rc: continue
    rc, res = git("merge-tree","--write-tree",commit,mine)
    out.append({"pr":int(other),"conflicts":rc!=0,
                "files":sorted({l.split("\t")[-1] for l in res.splitlines() if "\t" in l}) if rc else []})
print(json.dumps(out))
PY
)
  fi
  OUT="$out" python3 -c '
import json,os; print(json.dumps({"label":"conflicts","exit":0,"output":json.loads(os.environ["OUT"])}))'
}

# --- teardown --------------------------------------------------------------
# Safe to run twice, and safe to run on a pull request that was never prepared.
teardown() {
  local n=${1:-}
  if [ -n "$n" ]; then
    git -C "$ROOT" worktree remove --force "$(tree_for "$n")" 2>/dev/null || true
    git -C "$ROOT" update-ref -d "$(ref_for "$n")" 2>/dev/null || true
  else
    for d in "$WORKTREE_HOME"/pr-*; do
      [ -d "$d" ] && git -C "$ROOT" worktree remove --force "$d" 2>/dev/null || true
    done
    git -C "$ROOT" for-each-ref --format='%(refname)' refs/review-pr \
      | while read -r r; do git -C "$ROOT" update-ref -d "$r"; done
  fi
  git -C "$ROOT" worktree prune
}

case "${1:-}" in
  prepare)  [ $# -eq 2 ] || die "usage: pr-setup.sh prepare <pr-number>";  prepare "$2" ;;
  gates)    [ $# -eq 2 ] || die "usage: pr-setup.sh gates <pr-number>";    gates "$2" ;;
  teardown) teardown "${2:-}" ;;
  *) die "usage: pr-setup.sh {prepare|gates|teardown} [<pr-number>]" ;;
esac
