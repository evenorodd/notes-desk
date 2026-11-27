#!/bin/sh
# Commits and pushes new or changed price files in feeds/, and nothing else,
# so other work in the folder is never committed. Run by
# notes-desk-nightly.service after a successful catch-up; safe to run by hand.
set -u
cd "$(dirname "$0")/../.." || exit 1

git add -- feeds
if git diff --cached --quiet -- feeds; then
    echo "No new price files to push."
    exit 0
fi
dates=$(git diff --cached --name-only -- feeds | sed -n 's/.*closes_\(.*\)\.csv$/\1/p' | sort | tr '\n' ' ')
git commit -q -m "Closes for ${dates% }" -- feeds || exit 1
echo "Committed closes for ${dates% }."

if git push -q; then
    echo "Pushed to GitHub."
else
    echo "PUSH FAILED. The commit is kept here and goes up with the next push."
    echo "If GitHub has changes you don't have, run: git pull, then git push"
    exit 1
fi
