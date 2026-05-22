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


def main():
    plan_dir, trace_id = parse_args(sys.argv)

    # Read outcome.json (primary source of truth)
    outcome = load_json(plan_dir / "outcome.json") or {}

    feedback_dir = plan_dir / "feedback"
    feedback_entries = collect_feedback_files(feedback_dir)

    # Aggregate per-criterion scores and retries from feedback files
    iters_per_sprint = defaultdict(list)
    retries = []
    scores = defaultdict(list)
    composite_per_iter = {}
    feedback_sources = []

    for iter_num, f in feedback_entries:
        data = load_json(f) or {}
        sprint_n = data.get("sprint", iter_num)
        iters_per_sprint[sprint_n].append(iter_num)
        state = load_state(plan_dir, iter_num)
        feedback_sources.append(str(f.relative_to(plan_dir)))

        # Collect criterion scores (supports both flat int/float and nested dict)
        for criterion, value in (data.get("scores") or {}).items():
            if isinstance(value, (int, float)):
                scores[criterion].append(value)
            elif isinstance(value, dict):
                s = value.get("score")
                if isinstance(s, (int, float)):
                    scores[criterion].append(s)

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
            retries.append({
                "iter": iter_num,
                "sprint": sprint_n,
                "skill": data.get("skill") or "fullstack-developer",
                "failure_class": failure_class or state.get("failure_class", ""),
                "failure": issue,
                "targeted_criteria": data.get("targeted_criteria") or state.get("targeted_criteria") or [],
                "what_not_to_retry": state.get("what_not_to_retry") or data.get("what_not_to_retry") or [],
                "source_feedback": str(f.relative_to(plan_dir)),
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

    # Retry skill counts
    skill_retry_counts = Counter(r["skill"] for r in retries)
    highest_retry_skill = skill_retry_counts.most_common(1)[0][0] if skill_retry_counts else None

    # Average criterion scores
    avg_scores = {k: sum(v) / len(v) for k, v in scores.items() if v}
    lowest_avg_criterion = min(avg_scores, key=avg_scores.get) if avg_scores else None

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

    trace = {
        "id": trace_id,
        "pipeline": "adversarial-dev",
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
        "friction_summary": {
            "highest_retry_skill": highest_retry_skill,
            "lowest_avg_criterion": lowest_avg_criterion,
            "total_retries": len(retries),
            "retry_failure_evidence_missing": sum(1 for r in retries if not r.get("failure")),
            "exclude_from_crystallize": trace_kind == "smoke",
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
