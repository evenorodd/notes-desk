#!/bin/sh
# The nightly run on Linux, started by notes-desk-nightly.service (safe to run
# by hand too): start the app if needed, run every night missed since the last
# successful run, push any new price files to GitHub, then show a desktop
# notification saying how it went.
set -u
cd "$(dirname "$0")/../.." || exit 1
out=$(mktemp)
trap 'rm -f "$out" "$out.rc"' EXIT

notify() {   # urgency (low, normal, critical), title, text
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -a "Notes desk" -u "$1" "$2" "$3" 2>/dev/null || true
}

if ! docker compose up -d --wait >"$out" 2>&1; then
    cat "$out"
    notify critical "Notes desk: run FAILED" "Docker couldn't start the app. Details: journalctl --user -u notes-desk-nightly"
    exit 1
fi

# Run the catch-up, showing its output (for the log) and keeping a copy.
{ docker compose exec -T app python3 notes_desk.py catch-up 2>&1; echo $? >"$out.rc"; } | tee "$out"
rc=$(cat "$out.rc")
nights=$(sed -n 's/.*\[\([0-9-]*\)\] done\. Report.*/\1/p' "$out" | tr '\n' ' ')
nights=${nights% }
alert=$(sed -n 's/.*ALERT: //p' "$out" | head -1)

if [ "$rc" -ne 0 ]; then
    [ -n "$alert" ] || alert=$(tail -n 1 "$out")
    notify critical "Notes desk: run FAILED" "${nights:+Ran $nights, then stopped. }$alert"
    exit 1
fi

if [ "${NOTES_DESK_PUSH:-1}" = 0 ]; then
    push="Price files kept here (not pushed)."
    prc=0
else
    push=$(sh docker/systemd/push-prices.sh 2>&1)
    prc=$?
fi
echo "$push"

if [ -z "$nights" ]; then
    notify low "Notes desk: nothing new" \
        "No new closes since the last run (the market was closed, or today's close isn't final yet)."
elif [ "$prc" -ne 0 ]; then
    notify critical "Notes desk: $nights ran OK, but the push FAILED" \
        "$(echo "$push" | tail -n 2 | tr '\n' ' ')"
else
    text="$(echo "$push" | tail -n 1) Open http://localhost:8000"
    if [ -n "$alert" ]; then
        notify normal "Notes desk: $nights ran OK, check a price" "$alert. $text"
    else
        notify normal "Notes desk: $nights ran OK" "$text"
    fi
fi
exit "$prc"
