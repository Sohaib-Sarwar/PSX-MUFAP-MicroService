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

git checkout --quiet --orphan "$BRANCH"
git reset --quiet
git add --force data
git commit --quiet --message "data: ${OWNED_PREFIX%.} snapshot $(date -u +'%Y-%m-%dT%H:%MZ')"
git push --force origin "HEAD:refs/heads/$BRANCH"

echo "published to '$BRANCH':"
git ls-tree -r --name-only HEAD -- data | sed 's/^/  /'
