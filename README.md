# Meta-Harness Skill

Meta-Harness is a Codex/Claude skill for running iterative delivery loops with explicit planning, locked rubrics, adversarial evaluation, failure routing, and trace emission.

It is intentionally domain-agnostic. It does not know React, FastAPI, design systems, or product rules by itself. Instead, it orchestrates the loop and delegates domain work to the right agents or skills.

## What It Does

- Decides whether a task needs an iterative harness or a simpler direct workflow.
- Detects intent as `DELIVER` for product/code artifacts or `IMPROVE` for skill/agent/workflow refinement.
- Locks a measurable rubric before implementation starts.
- Runs an Implement -> Evaluate -> Analyze loop.
- Routes failures to re-plan, retry implementation, reopen rubric, or stop on environment issues.
- Supports `--max-iter=until-pass` with an explicit budget.
- Emits trace JSON from plan artifacts for later skill evolution.
- Supports orchestration modes: local controller, parallel agents, and speculative parallel sidecars.

## Repository Layout

```text
.
├── SKILL.md
├── references/
│   └── program.md
└── scripts/
    └── extract-trace.py
```

## Install

Copy this repository folder into your skills directory:

```bash
mkdir -p "$HOME/.agents/skills"
cp -R meta-harness-skill "$HOME/.agents/skills/meta-harness"
```

If your runtime uses Claude's default skill path instead:

```bash
mkdir -p "$HOME/.claude/skills"
cp -R meta-harness-skill "$HOME/.claude/skills/meta-harness"
```

The skill entrypoint is `SKILL.md`.

## Usage

Use the skill when the task benefits from a measurable iteration loop:

```text
$meta-harness improve this dashboard until UX score is at least 8.5
$meta-harness implement the approved plan --target=9 --max-iter=3
$meta-harness rewrite these test cases for realism --target=9
```

Use direct execution instead when the task is a tiny one-off edit, lookup, typo fix, or a command with an obvious pass/fail result.

## Trace Extraction

The runtime script consolidates plan outputs into trace JSON:

```bash
python3 scripts/extract-trace.py /path/to/plan-dir
```

It reads:

- `outcome.json`
- `feedback/iter-*.json`
- `state/state-*.json`

Recent trace output separates who executed work from who should learn from a failure:

- `executor`: agent/skill that ran the work.
- `learning_owner`: skill/domain that should receive crystallization improvements.
- `criterion_evidence`: score evidence by rubric criterion.
- `owner_metrics`: retry and learning-signal aggregation by learning owner.

It writes consolidated traces to:

```text
~/.claude/traces/runs/
```

Smoke traces are excluded from crystallization when their ID or plan name contains `smoke`.

## Skill Evolution

`references/program.md` defines the self-improvement loop used by `--crystallize`.

The core rule is simple:

- Keep a change when score improves.
- Keep a change when score is unchanged but the skill gets simpler.
- Discard a change when score drops or complexity increases without benefit.

## Notes

- This repository contains only the skill package, not local trace history.
- Generated files such as `__pycache__` are intentionally ignored.
- The skill expects the host agent runtime to provide agent delegation, user question, shell, and file-edit tools.
