---
name: ripple
description: Orchestrates Ripple CLI inside OpenClaw for installation, domain guidance, request validation, async simulation submission, 30-second polling, historical job lookup, cancellation, and explicit cleanup. Use when the user wants to run or manage Ripple simulations without leaving OpenClaw.
compatibility: Designed for OpenClaw. Requires bash, local filesystem access, and either a working ripple-cli binary or this repository's install.sh path.
metadata:
  product: openclaw
  repository: Ripple-Dev
---

# Ripple

This is an OpenClaw orchestration skill. It is not a Ripple domain skill.

Do not confuse this skill with Ripple's internal domain packages under `skills/`, such as `social-media` or `pmf-validation`. Those are simulation domains that Ripple consumes. This skill teaches OpenClaw how to drive Ripple CLI safely.

## Use This Skill For

- installing and initializing Ripple
- checking environment health and current LLM config visibility
- discovering Ripple domains and understanding how to author a request
- validating a request before submission
- submitting a simulation job asynchronously
- polling a running job every 30 seconds by default
- checking job status immediately on demand
- viewing historical jobs, results, and logs
- cancelling a running job
- deleting or cleaning jobs only when the user explicitly asks

## Core Rules

1. Use the bundled scripts in `scripts/` instead of calling raw `ripple-cli` directly whenever a wrapper exists.
2. All OpenClaw-facing Ripple CLI calls must use JSON output. Every bundled wrapper enforces `--json`.
3. Prefer `domain schema` and `domain example` before `domain dump`.
4. Treat `<domain>` as a variable placeholder. Never special-case `pmf-validation`; `pmf-validation` is only one example domain.
5. Default new simulations to asynchronous submission. `scripts/job_run.sh` always adds `--async`.
6. After async submission, explicitly tell the user: polling will happen every 30 seconds by default.
7. Use `scripts/job_status.sh` for the normal polling loop. `scripts/job_wait.sh` exists, but the default orchestration path is explicit status polling every 30 seconds.
8. Do not automatically run `ripple-cli llm setup`. If LLM config is missing, ask the user to edit the Ripple config file or run `ripple-cli llm setup` manually.
9. If the user explicitly provides LLM settings, you may run `scripts/llm_set.sh ...` and then `scripts/llm_test.sh`.
10. `domain dump` is a heavy fallback for deep domain background, not the default discovery path.
11. `job delete` and `job clean` are destructive. Use them only on explicit user intent.

This skill must preserve the full user-facing Ripple CLI surface for OpenClaw, including `domain schema`, `domain example`, `domain dump`, `job cancel`, `job delete`, and `job clean`.

## Default Workflow

### New Simulation

1. If the environment may be missing or stale, run `scripts/version.sh` and `scripts/doctor.sh`. If Ripple is not installed or broken, run `scripts/install_init.sh`.
2. Identify the likely domain with `scripts/domain_list.sh`.
3. Inspect `scripts/domain_schema.sh <domain>` and `scripts/domain_example.sh <domain>` before asking follow-up questions.
4. Ask only for the missing business facts needed to complete the request.
5. Write a normalized request JSON file. Start from `assets/request-templates/generic-request.json`, `assets/request-templates/social-media-request.json`, or `assets/request-templates/pmf-validation-request.json`.
6. Run `scripts/validate.sh --input <request-file>` plus any explicit selectors such as `--skill`, `--platform`, `--channel`, or `--vertical`.
7. If validation is not ready, do not submit the job. Ask targeted follow-up questions and rewrite the request.
8. If validation passes, run `scripts/job_run.sh --input <request-file>` plus any explicit runtime overrides.
9. Report the returned `job_id` and tell the user that polling will happen every 30 seconds by default. Also mention that they can ask for an immediate status check at any time.
10. Poll with `scripts/job_status.sh <job_id>` every 30 seconds until the job reaches `completed`, `failed`, or `cancelled`.
11. On completion, run `scripts/job_result.sh <job_id>`.
12. If the user asks for process detail or wants to inspect how the result was produced, run `scripts/job_log.sh <job_id>`.

### Historical Or Control Intents

- `scripts/job_status.sh <job_id>`: check a running or finished job now.
- `scripts/job_list.sh ...`: inspect historical jobs, optionally with filters.
- `scripts/job_result.sh <job_id> [--summary]`: fetch a historical result.
- `scripts/job_log.sh <job_id>`: fetch a historical compact log.
- `scripts/job_cancel.sh <job_id>`: request cancellation of a running job.
- `scripts/job_delete.sh <job_id>`: delete one historical job only on explicit user intent.
- `scripts/job_clean.sh ...`: batch cleanup only on explicit user intent.

## Domain Guidance Strategy

Use this escalation order:

1. `scripts/domain_list.sh`
2. `scripts/domain_schema.sh <domain>`
3. `scripts/domain_example.sh <domain>`
4. `scripts/domain_info.sh <domain>`
5. `scripts/domain_dump.sh <domain> --section <section>`
6. `scripts/domain_dump.sh <domain>`

Use `domain dump` only when schema/example/info are not enough to coach the user through domain-specific business details.

## LLM Configuration Policy

- Default path: remind the user to configure Ripple secrets themselves.
- Safe recommendations:
  - edit the Ripple config file directly
  - run `ripple-cli llm setup` manually
- Explicit delegation path:
  - `scripts/llm_show.sh`
  - `scripts/llm_set.sh --platform ... --model ... --api-key ... --url ... --api-mode ...`
  - `scripts/llm_test.sh`

## Request Authoring Notes

- `event.seed_text` must exist through at least one domain-appropriate text field.
- For `social-media`, prefer `event.title`, `event.body`, `source.author_profile`, and `platform`.
- For `pmf-validation`, prefer `event.product_name`, `event.differentiators`, `event.validation_question`, `source.summary`, plus `platform`, `channel`, and `vertical` when available.
- When the user is unsure how to structure a request, read the domain schema/example first instead of guessing.

## References

- See [the command map](references/command-map.md) for the full wrapper-to-CLI mapping and deep command coverage.
- See [the workflow playbooks](references/workflow-playbooks.md) for the end-to-end orchestration flow.
- See [the input authoring guide](references/input-authoring-guide.md) for request construction rules.
- See [the history and control guide](references/history-and-control.md) for job status, history, cancel, delete, and clean behavior.
- See [the safety and config guide](references/safety-and-config.md) for secret handling, installation behavior, and destructive-operation boundaries.
