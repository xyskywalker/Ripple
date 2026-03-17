# History And Control

Treat running-job management and historical-job retrieval as first-class intents.

## Running Job Status

- Immediate check: `scripts/job_status.sh <job_id>`
- Blocking wait on explicit request: `scripts/job_wait.sh <job_id>`
- Default monitor policy: `scripts/job_status.sh <job_id>` every 30 seconds

Terminal states:

- `completed`
- `failed`
- `cancelled`

## Historical Job List

Primary command:

- `scripts/job_list.sh`

Useful filters:

- `scripts/job_list.sh --status completed`
- `scripts/job_list.sh --status failed --limit 50`
- `scripts/job_list.sh --source cli --offset 20 --limit 20`

## Historical Result

Full result:

- `scripts/job_result.sh <job_id>`

Summary first:

- `scripts/job_result.sh <job_id> --summary`

## Historical Log

- `scripts/job_log.sh <job_id>`

Use this when the user wants the reasoning trail, timeline, or compact process recap.

## Cancel

- `scripts/job_cancel.sh <job_id>`

After cancellation:

1. confirm the request was sent
2. optionally continue checking `scripts/job_status.sh <job_id>` until terminal

## Delete

Delete is destructive and explicit-intent only.

- `scripts/job_delete.sh <job_id>`

The wrapper adds `--yes` because OpenClaw is a non-interactive caller.

## Clean

Batch cleanup is destructive and explicit-intent only.

Preview:

- `scripts/job_clean.sh --dry-run --before 7d`

Execute:

- `scripts/job_clean.sh --before 7d`
- `scripts/job_clean.sh --status completed --before 30d`

The wrapper adds `--yes` unless the user already supplied it.
