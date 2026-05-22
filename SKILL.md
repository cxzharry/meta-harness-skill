---
name: meta-harness
description: "Orchestration harness for iterative delivery. Runs a Plan→Implement→Evaluate→Analyze loop with locked rubric, failure routing, optional cron scheduling, and trace emission for skill evolution. Pure orchestration — no domain knowledge. Auto-detects intent: end-to-end task delivery vs workflow/skill/agent improvement. Supports run-until-pass."
argument-hint: "<task> [--target=<score>] [--max-iter=N|until-pass] [--budget=<spec>] [--cron=<spec>] [--rubric=reopen] | --crystallize [target]"
crystallized: false
---

# Meta-Harness — Orchestration for Self-Improving Delivery Loops

Pure orchestration. No domain knowledge (React, FastAPI, design language, etc.) — those belong to delegated agents/skills. Meta-harness decides only: whether to run, how to sequence phases, when to stop, how to pass state, and how to emit traces.

## User Prompt Convention

Every user interaction goes through `AskUserQuestion` with 2-4 structured options `{ label, description }` — click-to-select, not free-text. Label ≤4 words, description one line. Free-type only via an explicit `Other / custom` option when the answer space is open-ended. Applies cross-cutting: Phase 0 gate override, orchestration mode choice, Phase 2 rubric confirm, Phase 4 plateau routing, `--crystallize` lock gate.

---

## Phase 0 — Pre-Flight: Should we use meta-harness at all?

**DO analyze before invoking.** Answer YES to at least one to proceed:

| Use it when… | Skip it when… |
|---|---|
| Task is non-trivial (multi-step, multi-file, ambiguous success) | Single-file edit, config tweak, doc typo |
| Success is measurable via a rubric (score, pass/fail per criterion) | Output is a one-shot answer (Q&A, lookup, summary) |
| Expected iteration count ≥ 2 (hard to get right first try) | A linter/test/compile already provides the feedback loop |
| Target is a skill/agent/workflow needing iterative polish | No one will re-run it; throwaway script |
| User wants "keep running until it's good" autonomy | User wants a quick answer now |

If all right-column rules apply → **refuse to run and recommend direct delegation** (e.g., plain `planner` + `fullstack-developer` + `tester`, or just a single agent call).

State the gate decision in 1 line before Phase 1: `Gate: PROCEED — reason …` or `Gate: SKIP — reason …`.

---

## Intent Detection (auto, not a CLI flag)

Meta-harness infers intent from the user's task description — no `--mode` CLI flag. State the detected intent in 1 line before Phase 1: `Intent: DELIVER — …` or `Intent: IMPROVE — …`.

**Signals for `IMPROVE` (workflow/skill/agent polish — speed-biased, quality floor):**
- Task mentions editing a skill, agent, prompt, workflow command, `.md` definition file
- Path in task points inside any `.claude/skills/`, `.claude/agents/`, or `.claude/commands/` — global (`~/.claude/…`), project-root (`<cwd>/.claude/…`), or any parent-directory scope (walk upward from CWD)
- Verbs: "improve", "polish", "refine", "tune", "make X better", "sửa skill", "tối ưu agent"
- Target noun is a named skill/agent/workflow

**Signals for `DELIVER` (end-to-end task artifact — quality-first):**
- Task mentions building a feature, product, page, component, API, script
- Output is runnable code / a working artifact, not a prompt definition
- Verbs: "build", "implement", "ship", "add", "create the X", "làm X"

**Ambiguous case:** default to `DELIVER` and surface the ambiguity: `Intent: DELIVER — ambiguous, flip with one word if wrong`.

Profile defaults per intent (applied automatically, overridable by flags). Shared: `target=7`, `target_min=6` (hard floor).

| | DELIVER (quality-first) | IMPROVE (speed-biased) |
|---|---|---|
| `max-iter` | 3 | 2 |
| Rubric criteria | correctness, completeness, edge-cases, craft | correctness, efficiency, reusability |
| Evaluator | full adversarial (Playwright / runtime probe) | structural re-scoring against definition |
| Speed bias | none | shorter eval, fewer criteria, fail-fast |

`IMPROVE` trades iteration depth for speed, **never the `target_min` floor**.

## Phase 0.5 — Orchestration Mode Choice

After Phase 0 says PROCEED and intent is detected, AI proposes the orchestration mode with a one-line recommendation and asks the user via `AskUserQuestion`.

Skip this question only when the user already gave an explicit mode, e.g. "local only", "no agents", "spawn agents", "parallel agents", "speculative parallel", "proposal-only agents", or "use worktrees". In that case, record `Mode: <explicit> — user requested`.

Question format:

```text
Question: "Recommended: <mode> — <reason>. Which orchestration mode should meta-harness use?"
Options:
- Auto recommended — Use AI's recommended mode and continue without more routing prompts.
- Local controller — Keep work in the controller unless a later blocker forces escalation.
- Parallel agents — Spawn file-owned or proposal-only agents when it can shorten delivery.
- Speculative parallel — Run 2-3 competing proposals or isolated worktrees for uncertain coupled work.
```

Recommendation rules:
- Recommend **Local controller** for single-file edits, tight debugging, one-command checks, or when context transfer costs more than it saves.
- Recommend **Parallel agents** for independent multi-file implementation, test/docs/review sidecars, or broad audits with clear ownership.
- Recommend **Speculative parallel** for coupled but uncertain design/fix choices where 2-3 proposals, risk reviews, or isolated worktree patches can be compared before integration.
- Recommend **Auto recommended** only when the best mode may change across iterations; this lets Phase 3a.0 decide per iteration while keeping the user out of repeated routing prompts.

Persist the selected mode into `{plan_dir}/rubric.json` or `{plan_dir}/state/state-0.json` as `orchestration_mode`, and pass it to Phase 3a.0. The selected mode constrains the Agent Split Gate, except explicit user follow-up can override it.

## Run-Until-Pass (--max-iter=until-pass)

For "keep running until it passes" autonomy — set `--max-iter=until-pass`. Behavior:

- No hard iteration cap. Only SUCCESS / PLATEAU / REGRESSION / BUDGET / ENV / user-interrupt can stop the loop.
- **Mandatory budget** — `--max-iter=until-pass` requires `--budget=<token_or_wallclock>` (otherwise refuse to start; prevents runaway cost).
- **Plateau is advisory, not terminal** — on PLATEAU, meta-harness auto-routes back to Phase 1 re-plan (with failure analysis attached) instead of exiting. Only surfaces to user if plateau persists across 2 consecutive re-plans.
- For multi-session persistence pair with `--cron=<spec>` so ticks continue after this session ends.
- Surface cumulative state (iter count, best composite, tokens burned) to user every 3 iterations so they can interrupt if uneconomical.

---

## Phase 1 — Plan

Delegate to `planner` agent (or existing `./plans/…/plan.md` if `--continue` / active plan detected).

**Output:** `{plan_dir}/spec.md` + sprint/phase breakdown.

Meta-harness responsibility:
- Ensure `plan_dir` exists (use naming from hook injection)
- Ensure plan has ≥1 sprint/phase with **testable behaviors**
- Do NOT prescribe tech stack / design choices — that's the planner's / user's call

If plan lacks testable behaviors → push back to planner once, then fail the gate.

---

## Phase 2 — Rubric Setup (locked before any iteration)

AI always drafts the rubric first — user can accept as-is or co-define. Orchestration mode was already handled in Phase 0.5; do not ask it again here.

**Step 1 — AI drafts with confidence tags.** Seed criteria from the Intent Detection profile row matching the detected intent (DELIVER or IMPROVE); extend or trim to 3-5 total. For each, mark confidence:
- `high` — obvious from task + intent (e.g. `correctness` for a code task)
- `low` — task-specific judgment call (e.g. weighting, thresholds, domain-specific criteria)

**Step 2 — Ask user via `AskUserQuestion` (2-4 questions, allow typing).** Focus questions on the `low`-confidence parts; skip `high`-confidence ones. Typical shape:

- Q1 (always): "Accept AI-drafted rubric or co-define?" — options: `Accept AI draft` / `Co-define with AI` / type custom
- Q2+ (only on low-confidence items): "For criterion X, what matters most?" — options: AI's 2-3 best guesses + type custom
- Never exceed 4 questions. If everything is `high` confidence → skip straight to lock with 1 acceptance question.

**Step 3 — Lock.** Merge answers, write `{plan_dir}/rubric.json` with `locked: true`. Do NOT modify mid-loop. Changing a locked rubric requires explicit `--rubric=reopen` flag — logged as a rubric-change event in the trace.

---

## Phase 3 — Iterate: Implement → Evaluate → Analyze

Per iteration `i`:

### Trace-Learned Operating Rules

Apply these lessons when the task matches the domain; they came from repeated trace friction and should reduce retries:

- **Operational dashboard/UI work:** Prefer restrained neutral surfaces, compact tables/lists, clear information hierarchy, and small semantic badges/text. Avoid large red/yellow/green category cards, decorative colored panels for every status, and card-heavy layouts that reduce scanability. For dashboard action roadmaps, use prioritized rows/lists over three large colored cards.
- **Dashboard runtime recovery:** When a dashboard has runtime/chunk/client-manifest errors, do a clean dev-server restart before judging UI recovery. Kill stale listeners, clear/rebuild generated runtime output when appropriate, then verify with repeated browser reloads or repeated HTTP probes. Do not trust one initial HTTP 200.
- **Human-facing Vietnamese/content rewrite:** Preserve IDs, assertions, dates, and business facts first; improve one tone/realism concern at a time. Avoid broad global replacements that make Vietnamese sound unnatural. Prefer concise, spoken wording with explicit pronouns only when they improve clarity.

### 3a.0 Agent Split Gate (MANDATORY)

Before Phase 3a starts, make an internal orchestration decision using the Phase 0.5 selected mode as a constraint:

```text
Agent split: Spawn / Local only / No split
Reason: <one line>
```

- **Spawn** when selected mode is `Auto recommended`, `Parallel agents`, or `Speculative parallel`, and work is safely parallelizable:
  - independent implementation slices with clear file ownership, or
  - speculative/review/scout sidecars for coupled work.
- **Speculative parallel** is valid when selected mode is `Auto recommended` or `Speculative parallel`, and the solution is coupled but multiple approaches, risk reviews, or patch proposals can run in parallel. Agents should return proposals/patches or use isolated worktrees; the controller owns comparison, integration, and final verification.
- Keep the immediate blocker local in the controller session; do not hand off work that the controller must wait on before doing anything useful.
- Start with 2-3 agents by default. Scale up when slices and ownership are clear, or when using isolated worktrees/proposal-only sidecars.
- Define each spawned agent's ownership: files/modules/responsibility, acceptance criteria, work context, reports path, and plans path.
- Tell implementation agents they are not alone in the codebase and must not revert others' edits.
- Continue useful non-overlapping controller work while agents run.
- **Local only** is valid for selected mode `Local controller`, single-file edits, one-command checks, tightly coupled debugging, or when context transfer would cost more than it saves.
- **No split** is valid for pure Q&A or when Phase 0 skipped the harness.
- User-facing output should mention the split decision only when agents are spawned, when user asked about routing, or when a large task is intentionally kept local because parallelization would be harmful.

The split decision is part of meta-harness output quality. Missing it is a failed iteration, even if the final artifact passes.

### 3a. Implement (Generator)
Delegate: DELIVER → `fullstack-developer` (or user-specified); IMPROVE → edit target skill/agent file directly, or spawn workers when the Agent Split Gate says work is safely parallelizable: independent implementation slices may edit owned files, while speculative/review/scout sidecars for coupled work should return proposals/patches or use isolated worktrees for competing patches. **ONE change per iteration** = one concern (e.g. fix one criterion's worst evidence, tighten one section, remove one redundancy); not "one line". Multiple edits OK if they serve one concern. Generator reads spec + locked rubric + `state-{i-1}.json` (skip state-read when i=1; Generator reads spec+rubric only). Outputs code/file changes + commit. **Must not self-evaluate.**

### 3b. Evaluate
Delegate: DELIVER → `tester` with Playwright/appropriate tooling; IMPROVE → `code-reviewer` or structural re-scoring. Writes `{plan_dir}/feedback/iter-{i}.json` (per-criterion scores + evidence). Job is to **break** the output, not confirm it. **Anti-gaming rule:** each score requires non-empty evidence (line refs, quotes, screenshots); empty evidence → re-run evaluator, score rejected.

For web/frontend/dashboard/product UI DELIVER tasks, run the fast browser verification path before heavier E2E:

1. Prefer the project's domain smoke script if present: `npm run dev:smoke`, `npm run smoke`, or `npm run test:smoke`.
2. If no project smoke exists, use `ck:chrome-devtools` Puppeteer against the local URL for a focused flow probe.
3. Record URL, command, assertions, and pass/fail evidence in `feedback/iter-{i}.json`; include screenshot path and console errors when failed.
4. Escalate to Playwright/full E2E only for critical workflows, regression suites, cross-browser needs, or when Puppeteer smoke passes but the rubric still requires deeper coverage.

### 3c. Failure Analysis (before next iteration — MANDATORY)
If `passed == false`:
1. **Classify** failure class: `plan` / `implementation` / `rubric` / `environment`.
2. **Route**:
   - `plan` → re-plan (loop back to Phase 1 with feedback attached)
   - `implementation` → next Generator with targeted-criteria feedback
   - `rubric` (criterion ill-defined) → halt, prompt `--rubric=reopen`
   - `environment` (server/tool broken) → halt, report
3. **State-pass** → write `{plan_dir}/state/state-{i}.json` with: prior_scores, failure_class, targeted_criteria, hypothesis, what_not_to_retry. Next Generator **reads it first**.

---

## Phase 4 — Stopping Criteria (evaluate after every iteration)

Stop loop when **any** fires:

1. **Target met** — `all criteria >= target_score` (passed). Exit: SUCCESS.
2. **Max iterations hit** — `i >= max-iter`. Exit: EXHAUSTED — report best iteration.
3. **Diminishing returns** — composite score improved by `< 0.3` across last 2 iterations. Exit: PLATEAU — ask user continue / stop / re-plan.
4. **Regression** — composite dropped `>= 1.0` from prior iteration. Exit: REGRESSION — revert to prior iter, ask user.
5. **Cost cap** — cumulative tokens / wall-clock exceed `--budget` (if set). Exit: BUDGET.
6. **User interrupt** — "stop" / "cancel" / "abort". Exit: CANCELED.
7. **Environmental failure (3x)** — same env error three times. Exit: ENV.
8. **Floor fail** — `any criterion < target_min` after an iteration completes (applies to BOTH intents; rule is strict `<`, criterion equal to floor passes). Exit: FLOOR_FAIL — quality floor breached, escalate to user.

Each exit writes `{plan_dir}/outcome.json` with exit code + best-iter reference.

---

## Phase 5 — Cron Scheduling (optional autonomous re-run)

For long-lived goals ("keep improving until target hit across many sessions"), use `--cron=<spec>`. Behavior:
1. On first run: delegate to `schedule` skill with the exact same command minus `--cron` flag (to avoid recursive scheduling).
2. Each scheduled tick runs Phases 1–4 (plan may be reused from `{plan_dir}/plan.md` with `--continue`).
3. Stop scheduling automatically when an iteration hits SUCCESS exit.
4. User can `--cron=stop` to manually stop.
5. Every tick appends to the same trace run ID (or opens a new one if plan changed).

**Don't schedule if** Phase 0 gate says SKIP, or rubric isn't locked, or task doesn't have a measurable target.

---

## Phase 6 — Trace Emission (hooks for skill evolution)

Emit trace **after every iteration** (upsert — same trace file overwritten each time). This ensures interrupted sessions still capture partial friction data.

```bash
# Bootstrap: auto-install runtime script from skill package if missing
RUNTIME_SCRIPT="$HOME/.claude/traces/extract-trace.py"
mkdir -p "$HOME/.claude/traces/runs"
if [ ! -f "$RUNTIME_SCRIPT" ]; then
  # Search known skill install locations (global → cwd-relative)
  for candidate in \
    "$HOME/.claude/skills/meta-harness/scripts/extract-trace.py" \
    "$(pwd)/.claude/skills/meta-harness/scripts/extract-trace.py"; do
    if [ -f "$candidate" ]; then
      cp "$candidate" "$RUNTIME_SCRIPT" && break
    fi
  done
  [ ! -f "$RUNTIME_SCRIPT" ] && echo "TRACE_WARN: extract-trace.py not found — copy skill package to install" && exit 0
fi

TRACE_PATH=$(python3 "$RUNTIME_SCRIPT" "${plan_dir}")
test -f "$TRACE_PATH" && echo "TRACE_OK $TRACE_PATH" || echo "TRACE_WARN: emission failed, continuing"
```

Run this after Phase 3b (evaluator writes feedback file) and after Phase 4 exit (final outcome written to `outcome.json`). Two calls total per iteration: one after eval, one after outcome.

Note: `extract-trace.py` is stdlib-only, no venv required. Source of truth is `scripts/extract-trace.py` inside this skill — copy the skill folder to get the full package. Failure is non-fatal — log to `{plan_dir}/outcome.json` and continue.

Trace captures (from `feedback/iter-*.json`, `state/state-*.json`, and `outcome.json`): `feedback_sources`, `evaluator_scores`, `evaluator_scores_avg`, `retries[]` (iter, sprint, skill, failure_class, non-empty failure evidence, targeted_criteria, what_not_to_retry, source_feedback), `friction_summary` (highest_retry_skill, lowest_avg_criterion, total_retries, retry_failure_evidence_missing, exclude_from_crystallize), `trace_kind`, `outcome`, `outcome_detail` (exit_code, composite_trajectory, best_iteration).

Trace quality rules:
- A failed iteration must emit actionable retry `failure` text. Extractor falls back through `failures`, `top_blockers`, `evidence`, `summary`, then state `hypothesis`.
- `retry_failure_evidence_missing` must be `0` for traces used to evolve skills/agents.
- Smoke or validation-only traces must use an ID or plan name containing `smoke`; extractor marks them `trace_kind: "smoke"` and `exclude_from_crystallize: true`.

---

## `--crystallize [target]` — Evolve → Lock

Skip Phases 1-5. Iteratively improve target from trace friction, auto-propose lock when quality gate is met.

1. **Select target.** If `target` given → use it. If omitted → aggregate `friction_summary.total_retries` across `~/.claude/traces/runs/*.json`, excluding traces where `friction_summary.exclude_from_crystallize == true`, `trace_kind == "smoke"`, or `friction_summary.retry_failure_evidence_missing > 0`; pick highest.
2. **Locate** by walking scopes: first `<cwd>/.claude/` → walk upward each parent `.claude/` → finally `~/.claude/`. Within each scope try `skills/{name}/SKILL.md` (+`references/*.md`) → `agents/{name}.md` → `commands/{name}.md`. *Editable surface*: skill = all `.md` files in folder (main-file = `SKILL.md`); agent/cmd = single file body + frontmatter. `crystallized` flag always goes on the main-file frontmatter. Abort if already set.
3. **Evolve loop** — read the rulebook at first found path: `~/.claude/skills/meta-harness/references/program.md` → `{cwd}/.claude/skills/meta-harness/references/program.md`. Same multi-path discovery as Phase 6 bootstrap. Quick ref: *KEEP* when composite score ↑, or unchanged + target simpler; *DISCARD* when score ↓, or unchanged + target more complex. Append each iteration to `~/.claude/traces/evolution-log.md`. Constraints: one change per iter, never touch other skills / CLAUDE.md / hooks / settings.
4. **Lock gate** — after every KEEP, check: ≥3 KEEP total, latest composite ≥8.5, no DISCARD in last 2 iters. If met → `AskUserQuestion` proposing crystallize (options: `Lock now` / `Continue evolving` / `Stop`). On `Lock now` → flip frontmatter (`crystallized: true / crystallized_at / crystallized_score`), append `Decision: CRYSTALLIZE` row to evolution-log, exit. Manual edits still allowed; remove flag to resume.

---

## File Layout

```
{plan_dir}/
├── plan.md                  # from planner / existing plan
├── spec.md                  # (DELIVER only, if planner emitted one)
├── rubric.json              # locked rubric (Phase 2)
├── feedback/iter-{i}.json   # evaluator output
├── state/state-{i}.json     # failure analysis + hypothesis
├── outcome.json             # final exit code + best-iter pointer
└── reports/meta-harness-report.md
```

Global trace store at `~/.claude/traces/` (see Phase 6 + `--crystallize`): `extract-trace.py` (bootstrapped from skill on first run), `evolution-log.md`, `runs/{trace_id}.json`.

Skill package (self-contained — copy this folder to get everything):
```
~/.claude/skills/meta-harness/
├── SKILL.md
├── references/program.md       # crystallize rulebook
└── scripts/extract-trace.py    # source of truth for runtime script
```
