# Command Patterns

This reference supports the `git_history_rewrite_and_branch_realignment` brain protocol.
Use these patterns as templates and adjust the file paths, branches, and target strings to match the incident.

## 1. Find The Source Of A Visible String

```bash
rg -n "Bad Brand|Bad String|Copyright" .
```

If the visible problem is in a generated site or app, search for the source configuration instead of editing only generated files.

## 2. Check Whether The Current Branch Tip Still Contains The Bad Text

```bash
git grep -n "Bad String" HEAD -- path/to/file
```

## 3. Find Commits That Introduced Or Carried The String

```bash
git log --oneline --all -S"Bad String" -- path/to/file
```

## 4. Find Exact Commits Whose File Snapshot Still Contains The String

```bash
git rev-list --all -- path/to/file | while read c; do
  git grep -n "Bad String" "$c" -- path/to/file >/dev/null && echo "$c"
done
```

## 5. Find Which Remote Branches Still Contain A Commit

```bash
git branch -r --contains <commit_sha>
```

This is one of the most important steps in the whole protocol. It tells you which refs you actually need to rewrite.

## 6. Check Whether `git-filter-repo` Is Available

```bash
command -v git-filter-repo || true
```

If it is available, prefer it.
If not, use `git filter-branch` as a fallback.

## 7. Rewrite In A Clean Temporary Clone

### Preferred Concept

- clone into `/tmp/...`
- create backup tags for important remote refs
- rewrite only the needed branches
- verify before push

### Example With `git filter-branch`

```bash
tmpdir=$(mktemp -d /tmp/history-rewrite.XXXXXX)
git clone <repo-url> "$tmpdir"
cd "$tmpdir"

git checkout -b ready origin/ready
git tag backup/main-pre-rewrite origin/main
git tag backup/ready-pre-rewrite origin/ready

FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch --force --tree-filter '
if [ -f path/to/file ]; then
  perl -0pi -e '\''s/Bad String/Good String/g'\'' path/to/file
fi
' -- main ready
```

Use narrow replacement logic. Do not rewrite more content than needed.

## 8. Verify The Rewritten Branches Before Push

```bash
git grep -n "Bad String" main -- path/to/file
git grep -n "Bad String" ready -- path/to/file
git rev-parse main ready
```

No grep results is the goal.

## 9. Force Push The Rewritten Refs

```bash
git push --force-with-lease origin main ready
```

Read the response carefully.
Warnings do not always mean failure.
The final push result is what matters.

## 10. Refresh The Original Local Repo After Rewrite

```bash
git fetch origin main ready
git branch -vv
```

Expect local branches to appear ahead and behind at the same time. That is normal after a force-rewrite.

## 11. Preserve A Local File Change While Realigning Branches

```bash
git stash push -m "local-preserved-change" -- path/to/file
git switch --detach origin/ready
git branch -f main origin/main
git branch -f ready origin/ready
git switch ready
git stash pop
```

If `stash pop` conflicts, resolve the conflict manually in the file.

## 12. Resolve A Single-File Stash Conflict

Choose the intended final content, remove the conflict markers, and then clear the unmerged state:

```bash
git add path/to/file
git restore --staged path/to/file
```

That leaves the file as a normal working-tree change without forcing an immediate commit.

## 13. Final Verification

```bash
git grep -n "Bad String" origin/main origin/ready -- path/to/file
git status --short
git branch -vv
```

## 14. When To Make A Follow-Up Commit

Do a normal commit after the rewrite if:

- the rewritten history now has safe content but not the final desired wording
- the user wants the visible branch tips to use a different phrase
- the restored local change should become the new canonical branch state

## 15. Recovery Notes

- If you rewrote the wrong refs, stop and inspect the backup tags in the temporary clone.
- If a branch still shows the old string, find which ref still contains the old commit chain.
- If the local branch was checked out during a force-move attempt and Git refused, switch to detached head first.
- If the original repo becomes confusing after the rewrite, a fresh clone is often faster than hand-repair.
