# Safety And Config

## JSON Rule

Every OpenClaw-facing Ripple CLI call must use JSON output. Use the wrappers in `scripts/` rather than raw `ripple-cli` so this rule is enforced consistently.

## Installation Rule

`scripts/install_init.sh` runs the real install script when needed and then returns a composed JSON payload with:

- install success/failure
- `doctor` snapshot
- `llm show` snapshot

It resolves the install script in this order:

1. `RIPPLE_INSTALL_SCRIPT` if the path exists
2. `~/.ripple/src/Ripple/install.sh`
3. repository-root `install.sh`

## LLM Secret Handling

Default behavior:

- do not auto-configure secrets
- do not auto-run `ripple-cli llm setup`
- tell the user they can either edit the Ripple config file or run `ripple-cli llm setup`

Explicit delegation behavior:

1. run `scripts/llm_set.sh ...`
2. run `scripts/llm_test.sh`
3. return success or failure clearly

## Domain Discovery Safety

Prefer:

- `domain schema`
- `domain example`
- `domain info`

Defer `domain dump` until lighter sources are insufficient, because `domain dump` can be large and noisy.

## Destructive Operations

Only execute the following on explicit user intent:

- `scripts/job_delete.sh <job_id>`
- `scripts/job_clean.sh ...`

Cancel is safe to expose prominently:

- `scripts/job_cancel.sh <job_id>`

## Lock Conflict Handling

If async submission reports another running job:

1. surface the running job ID
2. offer to check status now
3. offer to inspect the current job list
4. offer to cancel the running job

## Pass-Through Flags

Wrappers preserve extra CLI flags. This is how OpenClaw can still pass:

- `--config`
- `--db`
- `--status`
- `--source`
- `--limit`
- `--offset`
- `--summary`
- runtime controls such as `--max-waves`, `--max-llm-calls`, `--ensemble-runs`, `--deliberation-rounds`, `--report`, and `--simulation-horizon`

## LLM Budget Reminder

- Ripple defaults to `--max-llm-calls 800` when the user does not provide an override.
- This is a shared per-job budget across all roles, not a per-role allowance.
- Complex jobs, longer simulation horizons, larger wave counts, or deeper deliberation can exceed 800.
- When a task looks heavy, warn the user before submission and recommend a higher `--max-llm-calls`, otherwise the job may fail due to budget exhaustion.
