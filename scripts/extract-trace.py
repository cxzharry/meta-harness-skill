#!/usr/bin/env python3
"""Extract adversarial-dev trace from plan artifacts.

Reads {plan_dir}/outcome.json, feedback/iter-{i}.json
and emits a consolidated trace JSON to ~/.claude/traces/runs/{trace_id}.json.

Supports both new naming (iter-*.json) and legacy (sprint-*-round-*.json).

Usage:
    extract_trace.py <plan_dir> [--id <trace_id>]

Output: absolute path of the trace file written (to stdout).
"""
import sys
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

TRACES_DIR = Path.home() / ".claude" / "traces" / "runs"

# New naming: iter-01.json, iter-04-05-06-bundle.json (use first number)
ITER_RE = re.compile(r"iter-(\d+)")
# Legacy naming: sprint-1-round-2.json
LEGACY_RE = re.compile(r"sprint-(\d+)-round-(\d+)\.json$")


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"WARN: malformed JSON {path}: {e}", file=sys.stderr)
        return None


def parse_args(argv):
    if len(argv) < 2:
        sys.exit("usage: extract_trace.py <plan_dir> [--id <trace_id>]")
    plan_dir = Path(argv[1]).expanduser().resolve()
    if not plan_dir.is_dir():
        sys.exit(f"not a directory: {plan_dir}")
    trace_id = None
    if "--id" in argv:
        idx = argv.index("--id")
        if idx + 1 >= len(argv):
            sys.exit("--id requires a value")
        trace_id = argv[idx + 1]
    if not trace_id:
        trace_id = f"{datetime.now().strftime('%y%m%d-%H%M')}-{plan_dir.name}"
    return plan_dir, trace_id


def collect_feedback_files(feedback_dir: Path):
    """Return sorted list of (iter_num, path) from feedback dir.

    Tries iter-*.json first; falls back to legacy sprint-*-round-*.json if none found.
    Bundle files like iter-04-05-06-bundle.json use the first number (4).
    """
    if not feedback_dir.is_dir():
        return []

    results = []

    # Try new naming first
    new_files = sorted(feedback_dir.glob("iter-*.json"))
    if new_files:
        for f in new_files:
            m = ITER_RE.search(f.name)
            if m:
                results.append((int(m.group(1)), f))
        return results

    # Fallback: legacy sprint-*-round-*.json
    for f in sorted(feedback_dir.glob("sprint-*-round-*.json")):
        m = LEGACY_RE.search(f.name)
        if m:
            # Use sprint number as iter proxy
            results.append((int(m.group(1)) * 100 + int(m.group(2)), f))
    return results


def map_exit_code(exit_code: str) -> str:
    """Map outcome.json exit_code to trace outcome string."""
    if not exit_code:
        return "incomplete"
    code = exit_code.upper()
    if "SUCCESS" in code:
        return "success"
    if "FAIL" in code or "FLOOR" in code:
        return "fail"
    if any(marker in code for marker in ["EXHAUSTED", "PLATEAU", "REGRESSION", "BUDGET", "CANCELED", "ENV"]):
        return "stopped"
    return "incomplete"


def normalize_outcome_value(value) -> str:
    """Normalize legacy/string/dict outcome values into success/fail/incomplete."""
    if isinstance(value, dict):
        return map_exit_code(str(value.get("exit_code") or value.get("status") or ""))
    if isinstance(value, str):
        mapped = map_exit_code(value)
        if mapped != "incomplete":
            return mapped
        lowered = value.lower()
        if "success" in lowered:
            return "success"
        if "fail" in lowered or "floor" in lowered:
            return "fail"
    return "incomplete"


def plan_group_key(plan_dir: Path) -> str:
    """Stable key for grouping repeated trace emissions from the same plan."""
    name = plan_dir.name
    # Strip the common timestamp prefix while preserving descriptive plan identity.
    return re.sub(r"^\d{6}-\d{4}-", "", name)


def compact_text(value, limit=300):
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    elif isinstance(value, list):
        text = "; ".join(compact_text(item, limit=limit) for item in value)
    elif isinstance(value, dict):
        preferred = [
            value.get("issue"),
            value.get("summary"),
            value.get("evidence"),
            value.get("message"),
            value.get("description"),
            value.get("text"),
        ]
        text = "; ".join(compact_text(item, limit=limit) for item in preferred if item)
        if not text:
            text = "; ".join(f"{k}: {compact_text(v, limit=limit)}" for k, v in value.items())
    else:
        text = str(value)
    text = " ".join(text.split())
    return text[:limit]


def first_non_empty(*values, limit=300):
    for value in values:
        text = compact_text(value, limit=limit)
        if text:
            return text
    return ""


def score_and_evidence(value):
    """Return numeric score and compact evidence from a score value."""
    if isinstance(value, (int, float)):
        return value, ""
    if isinstance(value, dict):
        score = value.get("score")
        if not isinstance(score, (int, float)):
            score = None
        evidence = first_non_empty(
            value.get("evidence"),
            value.get("reasoning"),
            value.get("summary"),
            value.get("message"),
            value.get("description"),
            limit=500,
        )
        return score, evidence
    return None, ""


def load_state(plan_dir: Path, iter_num: int):
    state_dir = plan_dir / "state"
    candidates = [
        state_dir / f"state-{iter_num}.json",
        state_dir / f"state-{iter_num - 1}.json",
    ]
    for path in candidates:
        data = load_json(path)
        if data:
            return data
    return {}


def infer_trace_kind(trace_id: str, plan_dir: Path):
    marker = f"{trace_id} {plan_dir.name}".lower()
    return "smoke" if "smoke" in marker else "run"


def infer_orchestration(outcome, rubric):
    """Return explicit orchestration metadata without guessing from filenames."""
    orchestrator = (
        outcome.get("orchestrator")
        or rubric.get("orchestrator")
        or outcome.get("pipeline_owner")
        or rubric.get("pipeline_owner")
        or "unknown"
    )
    used = (
        outcome.get("meta_harness_used")
        or rubric.get("meta_harness_used")
        or orchestrator == "meta-harness"
    )
    return orchestrator, bool(used)


def list_text(value) -> str:
    """Compact arbitrary values into lowercase text for heuristic matching."""
    return compact_text(value, limit=2000).lower()


def infer_learning_attribution(data, state, failure_class, issue):
    """Separate executor from the skill/domain that should learn from a failure.

    `executor` records who ran the work. `learning_owner` records which skill is
    a reasonable crystallize target. Unknown owners are excluded from evolution.
    """
    attribution = data.get("attribution") or state.get("attribution") or {}
    explicit_owner = attribution.get("learning_owner")
    explicit_executor = attribution.get("executor")
    explicit_domain = attribution.get("failure_domain") or attribution.get("domain")
    explicit_confidence = attribution.get("confidence")

    executor = explicit_executor or data.get("executor") or data.get("skill") or "unknown"
    if explicit_owner:
        return {
            "executor": executor,
            "learning_owner": explicit_owner,
            "learning_owner_confidence": explicit_confidence or "high",
            "failure_domain": explicit_domain or "explicit",
            "attribution_reason": attribution.get("reason") or "feedback attribution",
        }

    criteria = data.get("targeted_criteria") or state.get("targeted_criteria") or []
    scores = data.get("scores") or {}
    text = " ".join([
        list_text(criteria),
        list_text(scores.keys()),
        list_text(issue),
        list_text(data.get("evidence")),
        list_text(data.get("summary")),
        list_text(data.get("top_blockers")),
        list_text(state.get("hypothesis")),
        list_text(state.get("what_not_to_retry")),
        list_text(failure_class),
    ])

    if str(failure_class).lower() == "environment" and any(
        needle in text
        for needle in ["runtime_recovery", "stability", "http 500", "dev server", "chunk", "manifest"]
    ):
        return {
            "executor": executor,
            "learning_owner": "debug",
            "learning_owner_confidence": "high",
            "failure_domain": "runtime_recovery",
            "attribution_reason": "environment failure with runtime recovery signal",
        }

    rules = [
        ("meta-harness", "trace_hygiene", "high", [
            "failure_evidence_capture", "retry_failure_evidence_missing",
            "trace_kind", "exclude_from_crystallize", "crystallize",
            "agent_split_gate", "mode question", "parallel prompt",
        ]),
        ("debug", "runtime_recovery", "medium", [
            "runtime_recovery", "stability", "http 500", "dev server",
            "chunk", "manifest", "environment", "stale listener",
        ]),
        ("frontend-design", "ui_visual_design", "medium", [
            "visual_restraint", "visual_hierarchy", "ui_render", "scanability",
            "mobile_layout", "tap_simplicity", "visual_tokens",
            "dashboard", "large red", "semantic badges", "card",
        ]),
        ("copywriting", "content_quality", "medium", [
            "vietnamese_polish", "user_voice_realism", "pronoun",
            "tone", "readability", "english_term_readability",
        ]),
        ("code-review", "verification_quality", "medium", [
            "verification", "data_honesty", "accessibility", "security_and_scope",
            "contract", "compatibility",
        ]),
    ]
    for owner, domain, confidence, needles in rules:
        if any(needle in text for needle in needles):
            return {
                "executor": executor,
                "learning_owner": owner,
                "learning_owner_confidence": confidence,
                "failure_domain": domain,
                "attribution_reason": "heuristic match from feedback criteria/evidence",
            }

    return {
        "executor": executor,
        "learning_owner": "unknown",
        "learning_owner_confidence": "low",
        "failure_domain": explicit_domain or "unknown",
        "attribution_reason": "no reliable owner signal",
    }


def build_learning_signal(attribution, issue, targeted_criteria, what_not_to_retry, criterion_entries):
    """Create a compact rule-change hint for future crystallization."""
    avoid = what_not_to_retry if isinstance(what_not_to_retry, list) else [what_not_to_retry] if what_not_to_retry else []
    evidence = first_non_empty(
        [entry.get("evidence") for entry in criterion_entries if entry.get("evidence")],
        issue,
        limit=500,
    )
    criteria_text = ", ".join(str(c) for c in targeted_criteria or []) or "unspecified criteria"
    owner = attribution["learning_owner"]
    domain = attribution["failure_domain"]
    if owner == "unknown":
        recommendation = "Do not evolve automatically; attribution is unknown."
    else:
        recommendation = f"Improve {owner} rules for {domain}; focus on {criteria_text}."
    return {
        "summary": compact_text(issue, limit=300),
        "evidence": evidence,
        "should_add_rule": recommendation,
        "avoid": avoid,
        "confidence": attribution["learning_owner_confidence"],
    }


def main():
    plan_dir, trace_id = parse_args(sys.argv)

    # Read outcome.json (primary source of truth)
    outcome = load_json(plan_dir / "outcome.json") or {}
    rubric = load_json(plan_dir / "rubric.json") or {}

    feedback_dir = plan_dir / "feedback"
    feedback_entries = collect_feedback_files(feedback_dir)

    # Aggregate per-criterion scores and retries from feedback files
    iters_per_sprint = defaultdict(list)
    retries = []
    scores = defaultdict(list)
    criterion_evidence = defaultdict(list)
    composite_per_iter = {}
    feedback_sources = []

    for iter_num, f in feedback_entries:
        data = load_json(f) or {}
        sprint_n = data.get("sprint", iter_num)
        iters_per_sprint[sprint_n].append(iter_num)
        state = load_state(plan_dir, iter_num)
        feedback_sources.append(str(f.relative_to(plan_dir)))

        source_feedback = str(f.relative_to(plan_dir))

        # Collect criterion scores and evidence.
        for criterion, value in (data.get("scores") or {}).items():
            score, evidence = score_and_evidence(value)
            if isinstance(score, (int, float)):
                scores[criterion].append(score)
                criterion_evidence[criterion].append({
                    "iter": iter_num,
                    "score": score,
                    "evidence": evidence,
                    "source_feedback": source_feedback,
                })

        for criterion, entries in (data.get("criterion_evidence") or {}).items():
            if not isinstance(entries, list):
                entries = [entries]
            for entry in entries:
                score, evidence = score_and_evidence(entry)
                criterion_evidence[criterion].append({
                    "iter": iter_num,
                    "score": score,
                    "evidence": evidence or compact_text(entry, limit=500),
                    "source_feedback": source_feedback,
                })

        # Track composite per iter
        wc = data.get("weighted_composite")
        if wc is not None:
            composite_per_iter[iter_num] = wc

        # Track retries
        passed = data.get("passed", True)
        if not passed:
            failure_class = data.get("failure_class", "")
            failures = data.get("failures") or []
            issue = first_non_empty(
                failures,
                data.get("top_blockers"),
                data.get("evidence"),
                data.get("summary"),
                state.get("hypothesis"),
                limit=500,
            )
            attribution = infer_learning_attribution(data, state, failure_class, issue)
            retry_excluded = not issue or attribution["learning_owner"] == "unknown"
            targeted_criteria = data.get("targeted_criteria") or state.get("targeted_criteria") or []
            what_not_to_retry = state.get("what_not_to_retry") or data.get("what_not_to_retry") or []
            targeted_evidence = []
            for criterion in targeted_criteria:
                targeted_evidence.extend(criterion_evidence.get(criterion, []))
            learning_signal = build_learning_signal(
                attribution,
                issue,
                targeted_criteria,
                what_not_to_retry,
                targeted_evidence,
            )
            retries.append({
                "iter": iter_num,
                "sprint": sprint_n,
                # Backwards compatibility: skill remains the executor, not the
                # crystallize target. New consumers should use learning_owner.
                "skill": attribution["executor"],
                "executor": attribution["executor"],
                "learning_owner": attribution["learning_owner"],
                "learning_owner_confidence": attribution["learning_owner_confidence"],
                "failure_domain": attribution["failure_domain"],
                "attribution_reason": attribution["attribution_reason"],
                "exclude_from_crystallize": retry_excluded,
                "failure_class": failure_class or state.get("failure_class", ""),
                "failure": issue,
                "targeted_criteria": targeted_criteria,
                "criterion_evidence": targeted_evidence[:5],
                "what_not_to_retry": what_not_to_retry,
                "learning_signal": learning_signal,
                "source_feedback": source_feedback,
            })

    # Determine outcome — prefer outcome.json exit_code
    exit_code = outcome.get("exit_code", "")
    if exit_code:
        final_outcome = map_exit_code(exit_code)
    else:
        final_outcome = normalize_outcome_value(outcome.get("outcome") or outcome.get("status"))

    # Total sprints/iterations from outcome.json first, then fallback
    total_sprints = (
        outcome.get("total_sprints")
        or outcome.get("total_iterations")
        or len(iters_per_sprint)
        or 0
    )

    # Retry attribution counts. Executor is who ran work; learning_owner is the
    # skill/domain that should learn from the friction.
    executor_retry_counts = Counter(r["executor"] for r in retries)
    owner_retry_counts = Counter(
        r["learning_owner"]
        for r in retries
        if not r.get("exclude_from_crystallize")
        and r.get("learning_owner")
        and r.get("learning_owner") != "unknown"
    )
    unknown_learning_owner_count = sum(1 for r in retries if r.get("learning_owner") == "unknown")
    highest_retry_executor = executor_retry_counts.most_common(1)[0][0] if executor_retry_counts else None
    highest_learning_owner = owner_retry_counts.most_common(1)[0][0] if owner_retry_counts else None

    # Average criterion scores
    avg_scores = {k: sum(v) / len(v) for k, v in scores.items() if v}
    lowest_avg_criterion = min(avg_scores, key=avg_scores.get) if avg_scores else None

    owner_metrics = {}
    for retry in retries:
        if retry.get("exclude_from_crystallize"):
            continue
        owner = retry["learning_owner"]
        metrics = owner_metrics.setdefault(owner, {
            "retry_count": 0,
            "failure_domains": Counter(),
            "targeted_criteria": Counter(),
            "targeted_scores": [],
            "examples": [],
            "learning_signals": [],
        })
        metrics["retry_count"] += 1
        metrics["failure_domains"][retry["failure_domain"]] += 1
        for criterion in retry.get("targeted_criteria") or []:
            metrics["targeted_criteria"][criterion] += 1
            metrics["targeted_scores"].extend(scores.get(criterion, []))
        metrics["examples"].append({
            "failure": retry.get("failure", ""),
            "source_feedback": retry.get("source_feedback", ""),
        })
        metrics["learning_signals"].append(retry["learning_signal"])

    for owner, metrics in owner_metrics.items():
        targeted_scores = metrics.pop("targeted_scores")
        metrics["failure_domains"] = dict(metrics["failure_domains"])
        metrics["targeted_criteria"] = dict(metrics["targeted_criteria"])
        metrics["avg_targeted_score"] = (
            sum(targeted_scores) / len(targeted_scores)
            if targeted_scores else None
        )
        metrics["examples"] = metrics["examples"][:3]
        metrics["learning_signals"] = metrics["learning_signals"][:3]

    # Composite trajectory: prefer outcome.json, supplement with per-feedback composites
    composite_trajectory = outcome.get("composite_trajectory") or [
        {"iter": k, "composite": v} for k, v in sorted(composite_per_iter.items())
    ]

    # Build outcome_detail (trimmed essentials from outcome.json)
    outcome_detail = None
    if outcome:
        outcome_detail = {
            "exit_code": outcome.get("exit_code"),
            "normalized_outcome": final_outcome,
            "best_iteration": outcome.get("best_iteration"),
            "composite_trajectory": composite_trajectory,
            "total_iterations": outcome.get("total_iterations") or outcome.get("total_sprints"),
        }

    trace_kind = infer_trace_kind(trace_id, plan_dir)
    group_key = plan_group_key(plan_dir)
    orchestrator, meta_harness_used = infer_orchestration(outcome, rubric)
    missing_failure_evidence = sum(1 for r in retries if not r.get("failure"))
    crystallize_eligible_retries = sum(1 for r in retries if not r.get("exclude_from_crystallize"))
    exclude_from_crystallize = (
        trace_kind == "smoke"
        or missing_failure_evidence > 0
        or (len(retries) > 0 and crystallize_eligible_retries == 0)
    )

    trace = {
        "id": trace_id,
        "pipeline": "adversarial-dev",
        "orchestrator": orchestrator,
        "meta_harness_used": meta_harness_used,
        "trace_kind": trace_kind,
        "generated": datetime.now(timezone.utc).isoformat(),
        "plan_dir": str(plan_dir),
        "plan_group_key": group_key,
        "dedupe_key": group_key,
        "feedback_sources": feedback_sources,
        "sprints": {
            "total": total_sprints,
            "iterations_observed": len(feedback_entries),
        },
        "retries": retries,
        "evaluator_scores": {k: v for k, v in scores.items()},
        "evaluator_scores_avg": avg_scores,
        "criterion_evidence": {k: v for k, v in criterion_evidence.items()},
        "owner_metrics": owner_metrics,
        "friction_summary": {
            "highest_retry_skill": highest_retry_executor,
            "highest_retry_executor": highest_retry_executor,
            "highest_learning_owner": highest_learning_owner,
            "lowest_avg_criterion": lowest_avg_criterion,
            "total_retries": len(retries),
            "crystallize_eligible_retries": crystallize_eligible_retries,
            "retry_failure_evidence_missing": missing_failure_evidence,
            "unknown_learning_owner_count": unknown_learning_owner_count,
            "exclude_from_crystallize": exclude_from_crystallize,
            "dedupe_key": group_key,
        },
        "outcome": final_outcome,
        "outcome_detail": outcome_detail,
    }

    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    out = TRACES_DIR / f"{trace_id}.json"
    out.write_text(json.dumps(trace, indent=2))
    print(str(out))


if __name__ == "__main__":
    main()
