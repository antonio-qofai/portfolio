#!/bin/sh
# launchd entry for the report exporter: scripts/report_job.sh UV OUT TZ
#
# Runs at 05:45, at load, and every 30 minutes, but exports at most once a
# day: it exits at once if OUT already holds an ok report from today (in TZ,
# the due policy's timezone), which keeps Airtable to one read a day.
#
# After a scheduled wake the Mac can run this before Wi-Fi is back, and uv
# then fails to reach PyPI. So it waits up to 2 minutes for DNS, and if the
# network never comes it exits without touching OUT; the next trigger retries.
uv="$1"; out="$2"; tz="$3"
dns_check="${REPORT_JOB_DNS_CHECK:-/usr/bin/host -W 5 api.airtable.com}"  # tests override

today=$(TZ="$tz" date +%Y-%m-%d)
if [ -f "$out" ] && grep -q "\"generated_at\": \"$today" "$out" && grep -q '"status": "ok"' "$out"; then
  exit 0
fi

tries=0
until $dns_check >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge 24 ]; then
    echo "$(date '+%Y-%m-%dT%H:%M:%S') no network after 2 minutes, will retry"
    exit 1
  fi
  sleep 5
done

exec "$uv" run --no-project --python 3.12 --with requests python -m src.report --out "$out"
