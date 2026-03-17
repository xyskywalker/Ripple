# Workflow Playbooks

## 1. First-Time Install Or Recovery

Use this when Ripple may not be installed or the local environment looks broken.

1. Run `scripts/install_init.sh`.
2. Read the returned `doctor` payload.
3. Read the returned `llm_show` payload.
4. If installation succeeds but LLM config is missing, tell the user Ripple is installed and ask them to either edit the config file or run `ripple-cli llm setup`.
5. Only run `scripts/llm_set.sh` and `scripts/llm_test.sh` if the user explicitly delegates credentials or endpoint details.

## 2. New Simulation

1. If environment confidence is low, run `scripts/version.sh` and `scripts/doctor.sh`.
2. Determine the best domain.
3. Read `scripts/domain_schema.sh <domain>`.
4. Read `scripts/domain_example.sh <domain>`.
5. Ask only for missing fields that block a valid request.
6. Write a normalized request JSON file from a template under `assets/request-templates/`.
7. Run `scripts/validate.sh --input <request-file>` plus any explicit selectors.
8. If validation is not ready, stop and ask targeted follow-up questions.
9. If validation is ready but the task looks complex, long-horizon, or high-wave, warn that Ripple only defaults to `max_llm_calls=800` and recommend a larger override before submission.
10. Run `scripts/job_run.sh --input <request-file>`.
11. Return the `job_id` and explicitly say polling will happen every 30 seconds by default.
12. Poll with `scripts/job_status.sh <job_id>` every 30 seconds until `completed`, `failed`, or `cancelled`.
13. When terminal, run `scripts/job_result.sh <job_id>`.
14. If the user asks for process detail, run `scripts/job_log.sh <job_id>`.

## 3. Immediate Status Check

Use this when the user asks for status before the next default poll window.

1. Run `scripts/job_status.sh <job_id>` immediately.
2. Return the latest state.
3. If the job is still non-terminal and the default monitor should continue, remind the user that polling remains on the 30-second cadence unless they ask otherwise.

## 4. Historical Job List

1. Run `scripts/job_list.sh`.
2. If needed, refine with `--status`, `--source`, `--limit`, or `--offset`.
3. Summarize the returned jobs and surface the most relevant `job_id` values for the next action.

## 5. Historical Result

1. Run `scripts/job_result.sh <job_id> --summary` if the user first wants the concise result.
2. If the user wants the full payload, run `scripts/job_result.sh <job_id>`.
3. If the user asks how the conclusion was reached, run `scripts/job_log.sh <job_id>`.

## 6. Cancel

1. Run `scripts/job_cancel.sh <job_id>`.
2. Confirm the cancel request to the user.
3. If the user wants confirmation that cancellation finished, continue with `scripts/job_status.sh <job_id>` until terminal.

## 7. Delete Or Clean

These are explicit-intent flows only.

### Delete One Job

1. Confirm the user really wants deletion.
2. Run `scripts/job_delete.sh <job_id>`.
3. Return the deletion payload.

### Batch Clean

1. Confirm the user really wants cleanup.
2. If they want preview first, run `scripts/job_clean.sh --dry-run ...`.
3. If they want execution, run `scripts/job_clean.sh ...`.
4. Return the cleaned count and freed bytes.

## 8. Lock Conflict

If `scripts/job_run.sh` returns a lock conflict because another job is running:

1. surface the `running_job_id` if present
2. offer three next actions:
   - check the running job now with `scripts/job_status.sh <running_job_id>`
   - inspect `scripts/job_list.sh`
   - cancel the running job with `scripts/job_cancel.sh <running_job_id>`

## 9. When To Use `job_wait`

Default orchestration should be explicit status polling.

Use `scripts/job_wait.sh <job_id>` only when the user explicitly wants a blocking wait in one step instead of the normal 30-second status loop.
