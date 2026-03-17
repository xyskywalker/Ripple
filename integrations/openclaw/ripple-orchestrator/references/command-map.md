# Command Map

All OpenClaw-facing Ripple CLI invocations must go through JSON mode. Use the bundled wrappers below whenever a wrapper exists.

## Environment And Diagnostics

| Intent | Wrapper | Underlying CLI | Notes |
| --- | --- | --- | --- |
| Install + init + quick health snapshot | `scripts/install_init.sh` | `install.sh`, then `ripple-cli doctor --json`, then `ripple-cli llm show --json` | Runs the real install script, not a dry-run. |
| Version | `scripts/version.sh` | `ripple-cli version --json` | Safe quick probe. |
| Doctor | `scripts/doctor.sh` | `ripple-cli doctor --json` | Accepts `--config` and `--db` pass-through. |

## LLM Configuration

| Intent | Wrapper | Underlying CLI | Notes |
| --- | --- | --- | --- |
| Show current config | `scripts/llm_show.sh` | `ripple-cli llm show --json` | Safe default inspection path. |
| Update config | `scripts/llm_set.sh ...` | `ripple-cli llm set ... --json` | Use only when the user explicitly delegates secrets/config values. |
| Connectivity test | `scripts/llm_test.sh` | `ripple-cli llm test --json` | Run after `llm set` when the user wants verification. |
| Manual interactive setup | no wrapper | `ripple-cli llm setup` | Human-interactive. Recommend this path when the user wants to manage secrets themselves. |

## Domain Discovery And Guidance

| Intent | Wrapper | Underlying CLI | Notes |
| --- | --- | --- | --- |
| List all domains | `scripts/domain_list.sh` | `ripple-cli domain list --json` | Use first when the domain is not known yet. |
| Domain metadata | `scripts/domain_info.sh <domain>` | `ripple-cli domain info <domain> --json` | Good for supported prompts/platforms/examples overview. |
| All schemas | `scripts/domain_schema.sh` | `ripple-cli domain schema --json` | Returns an index across domains. |
| One domain schema | `scripts/domain_schema.sh <domain>` | `ripple-cli domain schema <domain> --json` | Preferred primary source for field requirements. |
| All example indexes | `scripts/domain_example.sh` | `ripple-cli domain example --json` | Lightweight example index. |
| One domain examples | `scripts/domain_example.sh <domain>` | `ripple-cli domain example <domain> --json` | Preferred primary source for request shape. |
| Full dump | `scripts/domain_dump.sh <domain>` | `ripple-cli domain dump <domain> --json` | Heavy fallback only. |
| Section dump | `scripts/domain_dump.sh <domain> --section <section>` | `ripple-cli domain dump <domain> --section <section> --json` | Useful for focused deep dives. |
| Single file dump | `scripts/domain_dump.sh <domain> --file <relative_path>` | `ripple-cli domain dump <domain> --file <relative_path> --json` | Load only when lighter sources are insufficient. |

Deep command coverage examples that must stay supported:

- `ripple-cli domain schema pmf-validation --json`
- `ripple-cli domain example pmf-validation --json`
- `ripple-cli domain example social-media --json`
- `ripple-cli domain dump <domain> --section <section> --json`
- `ripple-cli domain dump <domain> --file prompts/omniscient.md --json`

## Validation

| Intent | Wrapper | Underlying CLI | Notes |
| --- | --- | --- | --- |
| Validate a request file | `scripts/validate.sh --input <request-file>` | `ripple-cli validate --input <request-file> --json` | Use before every submission. |
| Validate with selectors | `scripts/validate.sh --input <request-file> --skill <domain> --platform <platform> --channel <channel> --vertical <vertical>` | `ripple-cli validate --input <request-file> --skill <domain> --platform <platform> --channel <channel> --vertical <vertical> --json` | Use when selectors are provided out-of-band from the JSON body. |

## Job Execution And Control

| Intent | Wrapper | Underlying CLI | Notes |
| --- | --- | --- | --- |
| Submit a new job | `scripts/job_run.sh --input <request-file>` | `ripple-cli job run --input <request-file> --async --json` | Wrapper always appends `--async`. |
| Submit with overrides | `scripts/job_run.sh --input <request-file> --skill <domain> ...` | `ripple-cli job run --input <request-file> ... --async --json` | Supports runtime knobs such as `--max-waves`, `--ensemble-runs`, `--report`, `--config`, `--db`. |
| Check status now | `scripts/job_status.sh <job_id>` | `ripple-cli job status <job_id> --json` | Primary polling primitive. |
| Blocking wait | `scripts/job_wait.sh <job_id>` | `ripple-cli job wait <job_id> --poll-interval 30 --json` | Wrapper defaults to 30 seconds unless an explicit `--poll-interval` is already provided. |
| Historical list | `scripts/job_list.sh` | `ripple-cli job list --json` | Fast local-only read. |
| Filtered historical list | `scripts/job_list.sh --status <status> --source <source> --limit <n> --offset <n>` | `ripple-cli job list --status <status> --source <source> --limit <n> --offset <n> --json` | Useful for pagination and triage. |
| Full result | `scripts/job_result.sh <job_id>` | `ripple-cli job result <job_id> --json` | Historical or terminal result. |
| Summary result | `scripts/job_result.sh <job_id> --summary` | `ripple-cli job result <job_id> --summary --json` | Use when the user wants the short version first. |
| Compact log | `scripts/job_log.sh <job_id>` | `ripple-cli job log <job_id> --json` | Use for process review. |
| Cancel running job | `scripts/job_cancel.sh <job_id>` | `ripple-cli job cancel <job_id> --json` | First-class OpenClaw capability. |
| Delete one historical job | `scripts/job_delete.sh <job_id>` | `ripple-cli job delete <job_id> --yes --json` | Explicit intent only. Wrapper appends `--yes`. |
| Batch clean history | `scripts/job_clean.sh --before 7d` | `ripple-cli job clean --before 7d --yes --json` | Explicit intent only. Wrapper appends `--yes`. |

Additional destructive examples:

- `ripple-cli job delete <job_id> --yes --json`
- `ripple-cli job clean --before 7d --yes --json`
- `ripple-cli job clean --status completed --before 30d --yes --json`

## Commands OpenClaw Should Not Use As Normal Automation

- `ripple-cli llm setup`
  - interactive
  - recommend for self-managed secret configuration
- `ripple-cli _worker`
  - hidden internal worker entrypoint
  - not a user-facing command
