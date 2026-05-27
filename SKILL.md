---
name: meta-harness
description: "Orchestration harness for iterative delivery. Runs a Plan→Implement→Evaluate→Analyze loop with locked rubric, failure routing, optional cron scheduling, and trace emission for skill evolution. Pure orchestration — no domain knowledge. Auto-detects delivery vs workflow improvement. Plans parallel implementation lanes when safe. Supports run-until-pass."
argument-hint: "<task> [--target=<score>] [--max-iter=N|until-pass] [--budget=<spec>] [--cron=<spec>] [--rubric=reopen] | --crystallize [target]"
crystallized: false
---

# Meta-Harness — Orchestration Loop

Pure orchestration only: decide whether to run, how to sequence phases, when to stop, how to pass state, and how to emit traces. Domain choices belong to delegated agents/skills.

## Prompting Rule

Use `AskUserQuestion` only for real approval or real user decisions: gate override, low-confidence rubric choices, plateau routing, `--crystallize` lock, privacy/safety approval, or budget/risk escalation.

Do **not** ask whether to run agents in parallel by default. Decide internally from the plan. Report split decisions only when spawning agents, when user asks about routing, or when deliberately keeping a large task local.

## Phase 0 — Gate

Proceed when at least one is true:
- Multi-step, multi-file, or ambiguous success.
- Success needs a measurable rubric.
- Expected iteration count is at least 2.
- Target is a skill/agent/workflow needing iterative polish.
- User wants autonomous "keep running until good" behavior.
- User invokes `--crystallize`.

Skip for one-shot Q&A, single-file tweaks, doc typos, or tasks where an existing lint/test/compile loop is enough.

Output one line: `Gate: PROCEED — reason …` or `Gate: SKIP — reason …`.

## Intent Detection

State one line before planning:
- `Intent: IMPROVE` for skill/agent/prompt/workflow/definition edits.
- `Intent: IMPROVE` for `--crystallize`.
- `Intent: DELIVER` for runnable code, product, page, API, script, or artifact work.
- Ambiguous defaults to `DELIVER` with the ambiguity surfaced.

Defaults:
- Shared floor: `target=7`, `target_min=6`.
- `DELIVER`: `max-iter=3`, criteria `correctness, completeness, edge-cases, craft`, runtime/adversarial eval.
- `IMPROVE`: `max-iter=2`, criteria `correctness, efficiency, reusability`, structural eval.

## Phase 0.5 — Internal Orchestration Default

Set routing internally. Do not ask for mode unless the user explicitly asked to choose, cost/risk changes materially, or approval is required.

If user gave a mode (`local only`, `no agents`, `parallel agents`, `speculative parallel`, `proposal-only agents`, `use worktrees`), record it. Otherwise:

```text
Mode: Auto recommended — internal routing
Reason: <one line>
```

Use:
- `Local controller` for single-file edits, tight debugging, one-command checks, or high context-transfer cost.
- `Parallel agents` for independent implementation slices, test/docs/review sidecars, or broad audits with clear ownership.
- `Speculative parallel` for coupled uncertain choices where competing proposals, risk reviews, or isolated worktree patches can be compared.

Persist `orchestration_mode` in `{plan_dir}/rubric.json` or `{plan_dir}/state/state-0.json`. Phase 1 `Parallelization Strategy` is the primary input for actual spawning.

## Run-Until-Pass

`--max-iter=until-pass` requires `--budget=<token_or_wallclock>`. Stop only on SUCCESS, repeated PLATEAU after 2 re-plans, REGRESSION, BUDGET, ENV, or user interrupt. On PLATEAU, re-plan first. Surface cumulative status every 3 iterations.

## Phase 1 — Plan

Delegate to `planner`, or reuse existing `./plans/.../plan.md` for `--continue`.

Required outputs:
- `{plan_dir}/spec.md` plus sprint/phase breakdown.
- At least one testable behavior.
- `Parallelization Strategy`.

Planner must include:

```text
Implementation parallelism: Parallel lanes / Sequential / Speculative lanes
Reason: <one line>
```

`Parallelization Strategy` must specify:
- `Can parallelize: yes/no`
- `Implementation lanes`: files/modules/responsibility/owner boundaries.
- `Sequential dependencies`: setup/shared contracts/merge points.
- `Verification`: per-lane check plus final verification.
- `Recommended Phase 3 Agent Split Gate input`: `Spawn`, `Speculative parallel`, or `Local only`, with reason.

Rules:
- Parallel lanes require independent ownership and no edit conflicts.
- Speculative lanes are for coupled uncertainty where proposals/patches can shorten convergence.
- Sequential is correct for single-file, tightly coupled, order-dependent, or high handoff-cost work.
- Start with 2-3 lanes; push back once if parallelizable work is left sequential without reason.

## Phase 2 — Rubric Setup

Draft the rubric before implementation. Keep 3-5 criteria.

Ask via `AskUserQuestion` only for low-confidence criteria, thresholds, or tradeoffs. If all criteria are high-confidence, lock without asking.

Write `{plan_dir}/rubric.json` with `locked: true`. Do not change it mid-loop unless user explicitly requests `--rubric=reopen`.

## Phase 3 — Iterate

Apply trace-learned rules when relevant: dashboards stay compact and restrained; runtime/chunk failures get a clean restart before judgment; Vietnamese/content rewrites preserve IDs, facts, dates, and one tone concern at a time.

### 3a.0 Agent Split Gate

Before implementation, decide internally:

```text
Agent split: Spawn / Local only / No split
Reason: <one line>
```

Spawn when Phase 1 lanes are safe or sidecar scout/review/proposal work can help without blocking the controller.

Rules:
- Respect explicit `Local controller` / `no agents`.
- Prefer Phase 1 `Implementation lanes`.
- Agents need ownership, acceptance criteria, work context, reports path, and plans path.
- Controller owns integration, conflict resolution, and final verification; agents must not revert others' edits.
- Keep immediate blockers local.
- Record any override of planner split in `state/state-{i}.json`.

### 3a. Implement

`DELIVER` delegates to `fullstack-developer` or user-specified agent. `IMPROVE` edits target skill/agent directly, or spawns workers only when split gate says safe.

One iteration fixes one concern. Multiple edits are OK if they serve that concern. Generator reads spec, rubric, and previous state; it must not self-evaluate.

### 3b. Evaluate

`DELIVER` uses tester/runtime/browser checks. `IMPROVE` uses code-reviewer or structural re-scoring.

Evaluator writes `{plan_dir}/feedback/iter-{i}.json` with per-criterion scores and non-empty evidence. Empty evidence invalidates the score.

For web/frontend/dashboard tasks, run fast browser smoke before heavy E2E:
1. Prefer project smoke script: `npm run dev:smoke`, `npm run smoke`, or `npm run test:smoke`.
2. Otherwise use `ck:chrome-devtools` Puppeteer against the local URL.
3. Record URL, command, assertions, errors/screenshots when relevant, and pass/fail.

### 3c. Failure Analysis

If not passed:
1. Classify: `plan`, `implementation`, `rubric`, or `environment`.
2. Route:
   - `plan` → re-plan with feedback.
   - `implementation` → next generator with targeted feedback.
   - `rubric` → halt and request `--rubric=reopen`.
   - `environment` → halt and report.
3. Write `{plan_dir}/state/state-{i}.json` with `prior_scores`, `failure_class`, `targeted_criteria`, `hypothesis`, and `what_not_to_retry`.

## Phase 4 — Stop Conditions

Stop when any fires: SUCCESS (all criteria meet target), EXHAUSTED (`i >= max-iter`), PLATEAU (composite improves `<0.3` across last 2 iterations), REGRESSION (drops `>=1.0`), BUDGET, CANCELED, ENV (same env error 3 times), or FLOOR_FAIL (any criterion below `target_min`).

Write `{plan_dir}/outcome.json` with exit code and best iteration.

## Phase 5 — Cron

Use `--cron=<spec>` only for long-lived measurable goals. Scheduled ticks rerun Phases 1-4, reuse plan when valid, append to the same trace run when possible, and stop on SUCCESS. Do not schedule if gate skipped, rubric is unlocked, or target is not measurable.

## Phase 6 — Trace

Emit trace after evaluator writes feedback and after final outcome is written.

Runtime script:
- Source of truth: `scripts/extract-trace.py` inside this skill.
- Runtime copy: `$HOME/.claude/traces/extract-trace.py`.
- Store: `$HOME/.claude/traces/runs/{trace_id}.json`.
- Stdlib-only; failure is non-fatal but must be noted in `outcome.json`.

Trace must capture feedback sources, scores, criterion evidence, retry failures, targeted criteria, `what_not_to_retry`, attribution, owner metrics, learning signals, friction summary, outcome, trajectory, and best iteration.

Attribution separates:
- `executor`: agent/skill that ran the work.
- `orchestrator`: harness/pipeline when known.
- `learning_owner`: skill/domain that should learn from the failure.
- `failure_domain`: concise domain such as `ui_visual_design`, `runtime_recovery`, `trace_hygiene`.

Do not crystallize from executor alone. `highest_retry_skill` is legacy executor data; crystallize target selection must use `highest_learning_owner`. Unknown-owner retries are excluded individually; trace-level crystallization stays allowed when at least one retry has known `learning_owner`. Missing failure evidence and smoke traces are excluded. `orchestrator` / `meta_harness_used` must come from explicit plan artifacts such as `outcome.json` or `rubric.json`, never filename guesses.

Useful evolution traces must include `criterion_evidence`, `owner_metrics`, and per-retry `learning_signal` so crystallize can see what failed, why, who should learn, and what rule should change.

## `--crystallize [target]`

Skip Phases 1-5. Improve a skill/agent/command from trace friction, then propose lock when quality gate is met.

Flow: choose target from argument or `highest_learning_owner` among non-smoke, evidence-backed traces; locate by walking `<cwd>/.claude/`, parent `.claude/`, then `~/.claude/`; edit only target skill `.md` files or single agent/command file; read `references/program.md`; evolve one concern per iteration. KEEP if score improves or stays equal with simpler target; DISCARD if score drops or gets more complex. Append to `~/.claude/traces/evolution-log.md`.

Lock gate: at least 3 KEEP, latest composite at least 8.5, no DISCARD in last 2 iterations. Ask `Lock now` / `Continue evolving` / `Stop`. On lock, set `crystallized: true`, `crystallized_at`, and `crystallized_score` in main frontmatter.

Never touch unrelated skills, `CLAUDE.md`, hooks, or settings during crystallize.

## File Layout

Plan files: `{plan_dir}/{plan.md,spec.md,rubric.json,feedback/iter-{i}.json,state/state-{i}.json,outcome.json,reports/meta-harness-report.md}`. Trace/package files: `~/.claude/traces/{extract-trace.py,evolution-log.md,runs/{trace_id}.json}` and `meta-harness/{SKILL.md,references/program.md,scripts/extract-trace.py}`.
