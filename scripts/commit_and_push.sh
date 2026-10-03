#!/usr/bin/env bash
# Commit the regenerated data/jobs.json and docs/index.html and push them to $BRANCH.
#
# Both files are rewritten on every run, so if $BRANCH moved while we were fetching
# jobs, a rebase will usually conflict. Generated files can't be merged by hand, so
# instead we throw our commit away, reset to the latest $BRANCH and re-run the
# update, which merges the fresh API results into the latest data/jobs.json.
#
# Env: BRANCH (default main), MAX_RETRIES (default 3),
#      UPDATE_CMD (default "python scripts/update_jobs.py").
set -uo pipefail

BRANCH="${BRANCH:-main}"
MAX_RETRIES="${MAX_RETRIES:-3}"
UPDATE_CMD="${UPDATE_CMD:-python scripts/update_jobs.py}"
FILES=(data/jobs.json docs/index.html)

has_conflict_markers() {
  grep -nE '^(<<<<<<<|=======|>>>>>>>)( |$)' "${FILES[@]}"
}

for attempt in $(seq 0 "$MAX_RETRIES"); do
  if has_conflict_markers; then
    echo "::error::Merge-conflict markers found in generated files; refusing to commit."
    exit 1
  fi

  git add "${FILES[@]}"
  if git diff --cached --quiet; then
    echo "No changes to commit"
    exit 0
  fi
  git commit -q -m "Update paralegal jobs ($(date -u +'%Y-%m-%d %H:%M UTC'))"

  if git pull -q --rebase origin "$BRANCH" && ! has_conflict_markers && git push -q origin "HEAD:$BRANCH"; then
    echo "Pushed to $BRANCH"
    exit 0
  fi

  if [ "$attempt" -ge "$MAX_RETRIES" ]; then
    break
  fi
  echo "::warning::Rebase conflict or push rejected (attempt $((attempt + 1))). Rebuilding on the latest $BRANCH."
  git rebase --abort 2>/dev/null || true
  git fetch -q origin "$BRANCH"
  git reset -q --hard "origin/$BRANCH"
  $UPDATE_CMD || exit 1
done

git rebase --abort 2>/dev/null || true
echo "::error::Could not push after $MAX_RETRIES retries."
exit 1
