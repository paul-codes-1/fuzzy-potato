#!/usr/bin/env bash
# dead-man heartbeat -> CloudWatch; never fails the caller
# Called from cron on job success (via `&& heartbeat.sh <job>`) so a missing
# ping = a job that didn't run/succeed. PATH is set because cron's minimal env
# doesn't include the aws v2 install dir.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
aws cloudwatch put-metric-data --region us-east-1 --namespace LT/Heartbeat --metric-name "$1" --value 1 >/dev/null 2>&1 || true
