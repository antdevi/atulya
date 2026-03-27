---
name: git_history_rewrite_and_branch_realignment
description: Use when incorrect, sensitive, or off-brand content has landed in Git history and must be removed from one or more branches without losing local work. Covers source identification, branch containment analysis, clean-clone rewrite, remote verification, force-push, local branch realignment, and recovery handling.
kind: brain_protocol
---

# Git History Rewrite And Branch Realignment

## Purpose
This protocol is for repository surgery.

Use it when a bad string, secret, wrong brand name, legal mistake, or other undesirable content has already entered Git history and a normal forward fix is not enough.

The protocol has two goals:

1. Remove the unwanted content from the relevant branch histories.
2. Preserve or restore the operator's intended local working state afterward.

## Use This Protocol When

- the content is already in branch history, not just the working tree
- the issue is serious enough that old commits should not keep showing it
- one or more remote branches must be rewritten
- local branches or uncommitted changes must survive the cleanup

## Do Not Use This Protocol When

- a normal commit is enough because only the latest file view matters
- the branch is shared in a way that makes history rewrite unacceptable
- the content lives only in build output or generated artifacts and the source has not been committed
- the problem can be solved by rotating a secret or revoking access without rewriting history

## Core Principles

- Fix the source, not only the symptom.
- Rewrite only the refs that actually contain the bad content.
- Do dangerous history work in a clean temporary clone, not in a dirty local tree.
- Verify before push, verify after push, and verify local recovery after that.
- Treat branch realignment as a separate phase from history rewrite.

## Mental Model

There are four distinct layers in this kind of incident:

1. The source file that generates the visible problem.
2. The set of commits that introduced or carried the problem.
3. The remote refs that still point at those commits.
4. The local branches and working tree that may now diverge after the rewrite.

If any of these layers is skipped, the cleanup is incomplete.

## Execution Flow

### 1. Find The True Source

Search for the visible text and identify the source file that actually produces it.

Do not assume the visible artifact is the authoritative source. In many repos, the visible issue comes from config, templates, or generated output.

If the issue is branding or copyright text, search both the exact string and the broader brand token.

### 2. Decide Whether History Rewrite Is Actually Needed

Check whether the bad text is present only in the current working tree, in the current branch tip, or inside older commits.

If it exists only in the latest commit or working tree, a normal fix may be enough.

If the user explicitly wants it removed from GitHub history, continue with rewrite planning.

### 3. Map Commit And Branch Containment

Identify:

- which commits introduced or modified the bad content
- which remote branches contain those commits
- whether additional refs still preserve the old chain

This matters because rewriting `main` and `ready` is not enough if another remote ref still points at the bad commit graph.

### 4. Protect The Local Working State

Before any ref surgery:

- inspect `git status`
- note the current branch
- isolate or stash only the files that must survive

If the local repo is already dirty, do not perform the rewrite there. Use a clean temporary clone for the dangerous work.

### 5. Choose The Rewrite Tool

Preferred order:

- `git-filter-repo` when available
- `git filter-branch` as a fallback

Use the smallest rewrite that solves the problem.

For string replacement inside one known file, a tree filter or index filter on the affected branches is usually enough.

### 6. Rewrite In A Clean Temporary Clone

Clone the repository to a temporary directory and perform the rewrite there.

Why:

- no collision with the user's dirty working tree
- easier rollback if the rewrite goes wrong
- cleaner verification of rewritten refs

Inside the temporary clone:

- create backup tags before rewriting important refs
- rewrite only the branches that contain the bad history
- keep the transformation deterministic and narrow

### 7. Verify Before Force Push

After rewriting, confirm:

- the unwanted string is gone from the rewritten branch tips
- the branch refs now point to new commit SHAs
- only the intended branches were rewritten

If the string is still found in the rewritten refs, stop and fix the rewrite before pushing anything.

### 8. Force Push Carefully

Push rewritten branches with `--force-with-lease`.

Read the remote response carefully.

Some platforms may display branch rule warnings while still accepting the push. The authoritative signal is the final push result, not the warning text alone.

### 9. Refresh And Realign The Original Local Repo

After the remote rewrite succeeds, the original local repo will usually be divergent.

Treat this as expected.

Recommended sequence:

1. fetch the rewritten remote refs
2. temporarily move local-only work out of the way
3. move local branch pointers onto the rewritten remote refs
4. restore the intended local changes

Use a detached head or another neutral state before force-moving the currently checked out branch.

### 10. Resolve Restoration Conflicts Intentionally

If a stash pop or restore creates a conflict:

- inspect only the conflicting area
- keep the operator's intended post-rewrite state
- clear the conflict markers
- leave the file as a normal local change unless the user asked for a commit

Do not blindly accept "updated upstream" or "stashed changes". Choose the desired semantic outcome.

### 11. Finish With Two Separate Verifications

Remote verification:

- rewritten branches no longer expose the bad string
- expected branch tips are in place

Local verification:

- local branches track the rewritten remote refs
- the intended working changes are still present
- the working tree is sane

## Output Contract

A successful run should leave behind:

- cleaned remote branch history for the targeted refs
- a clear record of which branches were rewritten
- a realigned local repo
- either a preserved local modification or an intentional follow-up commit

## Decision Guardrails

- If the user asks to remove something from history, explain that current branch view and full history are different targets.
- If only some branches contain the problem, rewrite only those branches.
- If the local tree is dirty, separate "remote rewrite" from "local recovery".
- If the content is branding-sensitive or legal-sensitive, verify exact strings, not just approximate wording.
- If another branch or tag still contains the bad commit chain, call that out before declaring success.

## Common Failure Modes

- fixing only the current file while leaving old history intact
- rewriting the wrong branch set
- rewriting in the user's dirty repo and disturbing unrelated work
- force-pushing rewritten history but not realigning local branches afterward
- restoring a stash without resolving the semantic conflict correctly
- forgetting that tags or secondary branches can still retain the old content

## Recovery Mindset

History rewrite is not just a Git operation. It is incident response.

Work in this order:

1. contain the blast radius
2. rewrite the minimal necessary history
3. verify the remotes
4. reattach local state
5. make the final visible wording match the user's intent

## References

- Command patterns and recovery snippets: [references/command_patterns.md](./references/command_patterns.md)
