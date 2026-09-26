#!/usr/bin/env bash
#
# Publish this run's snapshots to the data branch.
#
# The data branch is rewritten as a single orphan commit every time rather than
# appended to. A working day produces roughly a megabyte of superseded JSON;
# keeping that history would add a few hundred megabytes a year to a repository
# that nobody would ever check out, and every clone — including the next run's —
# would pay for it. One commit means the branch costs what the current data
# costs, permanently.
#
# Usage: publish-data.sh <dataset-prefix>       e.g. publish-data.sh psx.

set -euo pipefail

OWNED_PREFIX="${1:?usage: publish-data.sh <dataset-prefix>}"
BRANCH="${DATA_BRANCH:-service-data}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

have_remote=0
if git fetch --no-tags --depth=1 origin "$BRANCH"; then
  have_remote=1
fi

# Both scrapers publish to one branch, and each owns only the files whose names
# start with its prefix. Everything else is restored from the branch tip, so a
# PSX run cannot roll back the fund data MUFAP published an hour earlier.
if [ "$have_remote" = 1 ]; then
  while read -r tracked; do
    [ -n "$tracked" ] || continue
    case "${tracked#data/}" in
      "$OWNED_PREFIX"*) ;;
      *) git checkout FETCH_HEAD -- "$tracked" ;;
    esac
  done < <(git ls-tree -r --name-only FETCH_HEAD -- data)
fi

# `data/` is git-ignored on the source branch so a local scrape cannot dirty it;
# --force is what puts it in this commit deliberately.
git add --force data

if [ "$have_remote" = 1 ] && git diff --quiet --cached FETCH_HEAD -- data; then
  echo "snapshots are byte-identical to what is already published — nothing to push"
  exit 0
fi

# Both domains now run in their own concurrency group, so a PSX publish and a
# MUFAP publish can genuinely overlap. A force-push would let the loser silently
# erase the winner, so the merge-and-push is retried: on each attempt the other
# domain's files are re-taken from whatever is on the branch right now, and the
# push is rejected outright if the branch moved after that read.
attempt=1
max_attempts=5

while :; do
  git checkout --quiet --orphan "$BRANCH-work-$attempt"
  git reset --quiet
  git add --force data
  git commit --quiet     --message "data: ${OWNED_PREFIX%.} snapshot $(date -u +'%Y-%m-%dT%H:%MZ')"

  # --force-with-lease, not --force: it refuses if the remote advanced since
  # our fetch, which is exactly the case where force would destroy the other
  # domain's work.
  expected=""
  if [ "$have_remote" = 1 ]; then
    expected="--force-with-lease=refs/heads/$BRANCH:$(git rev-parse FETCH_HEAD)"
  else
    expected="--force-with-lease=refs/heads/$BRANCH:"
  fi

  if git push $expected origin "HEAD:refs/heads/$BRANCH" 2>&1; then
    break
  fi

  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "::error::could not publish to '$BRANCH' after $max_attempts attempts"
    exit 1
  fi

  echo "branch moved under us — remerging (attempt $((attempt + 1)))"
  sleep $(( attempt * 3 ))
  attempt=$(( attempt + 1 ))

  # Re-read the branch and re-take everything this run does not own.
  if git fetch --no-tags --depth=1 origin "$BRANCH"; then
    have_remote=1
    while read -r tracked; do
      [ -n "$tracked" ] || continue
      case "${tracked#data/}" in
        "$OWNED_PREFIX"*) ;;
        *) git checkout FETCH_HEAD -- "$tracked" ;;
      esac
    done < <(git ls-tree -r --name-only FETCH_HEAD -- data)
  fi
done

echo "published to '$BRANCH':"
git ls-tree -r --name-only HEAD -- data | sed 's/^/  /'
