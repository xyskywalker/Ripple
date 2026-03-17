# Input Authoring Guide

## Guiding Principle

Do not guess Ripple request structure from memory. Read the relevant domain schema and example first.

Preferred discovery order:

1. `scripts/domain_list.sh`
2. `scripts/domain_schema.sh <domain>`
3. `scripts/domain_example.sh <domain>`
4. `scripts/domain_info.sh <domain>`
5. `scripts/domain_dump.sh <domain> --section <section>`
6. `scripts/domain_dump.sh <domain>`

## Minimum Normalization Flow

1. Identify the domain.
2. Read the domain schema for required and recommended fields.
3. Read the domain example for a concrete shape.
4. Ask follow-up questions only for missing required or high-value recommended fields.
5. Write one normalized request JSON file.
6. Validate that file before submission.

## Domain-Agnostic Request Skeleton

Use `assets/request-templates/generic-request.json` as the neutral starting point.

Always make sure:

- `skill` matches the chosen Ripple domain
- `event` exists
- some domain-appropriate `event.seed_text` source exists
- selectors such as `platform`, `channel`, and `vertical` are either present in JSON or passed explicitly through CLI flags
- runtime knobs such as `simulation_horizon`, `ensemble_runs`, `deliberation_rounds`, and `report` are intentional

## `social-media` Guidance

Start with:

- `scripts/domain_schema.sh social-media`
- `scripts/domain_example.sh social-media`

Prefer collecting:

- `event.title`
- `event.body`
- `event.content_type`
- `platform`
- `source.author_profile`
- `simulation_horizon`

Common seed-text rule:

- `event.title`, `event.body`, `event.content`, `event.text`, `event.summary`, or `event.description` can satisfy the text requirement

Suggested template:

- `assets/request-templates/social-media-request.json`

## `pmf-validation` Guidance

Start with:

- `scripts/domain_schema.sh pmf-validation`
- `scripts/domain_example.sh pmf-validation`

Prefer collecting:

- `platform`
- `channel`
- `vertical`
- `event.product_name`
- `event.category`
- `event.differentiators`
- `event.validation_question`
- `source.summary`

Common seed-text rule:

- `event.title`, `event.summary`, or `event.description` must be sufficient to establish the validation context

Suggested template:

- `assets/request-templates/pmf-validation-request.json`

## When To Escalate To `domain dump`

Only escalate when schema/example/info do not answer one of these needs:

- the user needs deeper business background for a domain
- you need exact prompt/report/background material from the domain package
- the user needs help understanding what a particular domain really evaluates

Use the narrowest dump first:

- `scripts/domain_dump.sh <domain> --section schema`
- `scripts/domain_dump.sh <domain> --section examples`
- `scripts/domain_dump.sh <domain> --section reports`
- `scripts/domain_dump.sh <domain> --file <relative_path>`

## Validation Loop

After writing a request:

1. run `scripts/validate.sh --input <request-file>`
2. if validation is not ready, do not submit
3. ask the smallest set of follow-up questions needed to make the request valid
4. rewrite the same normalized request file and validate again
