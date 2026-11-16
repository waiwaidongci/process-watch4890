"""Cause-cover investigation planner.

A completed run exposes a finite set of out-of-control signals.  An analyst can
register candidate causes; each cause has a positive integer investigation cost
and a set of (original sequence, signal kind) pairs it can explain.  Some causes
are mutually exclusive.  The planner picks causes so that every signal is
explained and no exclusive pair is selected.

The objective is lexicographic:

    (total cost, number of selected causes, sorted selected cause ids)

The search is a deterministic depth-first branch and bound.  All of its state is
JSON serialisable, so an episode can stop at a caller supplied node budget and a
later request (or process) can continue from exactly the same frontier.

Soundness notes
---------------
* Branching: pick an uncovered signal and branch on every available cause that
  covers it.  Every feasible completion must choose one of those causes, so no
  solution is skipped.
* Lower bound: a feasible solution of the dual of the remaining weighted set
  cover relaxation.  Mutually exclusive causes are only removed from the
  available set, which can only raise the true optimum; ignoring the pair
  constraints in the dual therefore keeps the bound valid.
* ``status == "optimal"`` is emitted only after the frontier is exhausted.  A
  lower bound equal to the incumbent proves optimal *cost*, but secondary
  tie-breaking may still be unfinished and is reported separately.
"""
from __future__ import annotations

import hashlib
import json

SOLVER_VERSION = "cause-cover-v1"

STATUS_DRAFT = "draft"
STATUS_PAUSED = "paused"
STATUS_OPTIMAL = "optimal"
STATUS_INFEASIBLE = "infeasible"

KIND_ORDER = {"individual": 0, "moving_range": 1, "ewma": 2}
INF = 10 ** 40


def signal_key(sequence, kind):
    return f"{int(sequence)}:{kind}"


def normalize_signals(signals):
    """De-duplicate and order signals by original sequence, then kind."""
    table = {}
    for raw in signals or []:
        sequence = int(raw["sequence"])
        kind = str(raw["kind"])
        if kind not in KIND_ORDER:
            raise ValueError(f"未知信号种类：{kind}")
        key = signal_key(sequence, kind)
        table.setdefault(key, {"sequence": sequence, "kind": kind, "value": raw.get("value")})
    return sorted(table.values(), key=lambda x: (x["sequence"], KIND_ORDER[x["kind"]], x["kind"]))


def normalize_coverage(coverage):
    keys = []
    seen = set()
    for raw in coverage or []:
        key = signal_key(raw["sequence"], raw["kind"])
        if key not in seen:
            seen.add(key)
            keys.append(key)
    if not keys:
        raise ValueError("候选原因至少需要解释一条信号")
    return sorted(keys)


def rules_hash(problem):
    payload = {
        "solver": SOLVER_VERSION,
        "run_id": problem["run_id"],
        "run_input_hash": problem["run_input_hash"],
        "signals": problem["keys"],
        "causes": [
            {"id": problem["cause_ids"][i], "cost": problem["costs"][i], "covers": problem["cover_keys"][i]}
            for i in range(len(problem["cause_ids"]))
        ],
        "conflicts": _conflict_pairs(problem),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _conflict_pairs(problem):
    pairs = []
    for i in range(len(problem["cause_ids"])):
        for j in range(i + 1, len(problem["cause_ids"])):
            if problem["conflicts"][i] >> j & 1:
                a, b = sorted((problem["cause_ids"][i], problem["cause_ids"][j]))
                pairs.append([a, b])
    return sorted(pairs)


def build_problem(run_id, run_input_hash, signals, causes, conflicts):
    """Compile database rows into bit-mask arrays.

    ``causes``: iterable of mappings with ``id``, ``cost`` and ``signals``.
    ``conflicts``: iterable of ``(cause_id_a, cause_id_b)`` pairs.
    """
    signals = normalize_signals(signals)
    keys = [signal_key(x["sequence"], x["kind"]) for x in signals]
    index = {key: i for i, key in enumerate(keys)}

    cause_list = sorted(causes, key=lambda x: int(x["id"]))
    cause_ids = [int(c["id"]) for c in cause_list]
    costs = [int(c["cost"]) for c in cause_list]
    if any(cost <= 0 for cost in costs):
        raise ValueError("候选原因代价必须为正整数")
    if len(set(cause_ids)) != len(cause_ids):
        raise ValueError("候选原因编号重复")
    cause_index = {cid: i for i, cid in enumerate(cause_ids)}

    covers, cover_keys = [], []
    for cause in cause_list:
        raw_keys = normalize_coverage(cause.get("signals") or [])
        unknown = [key for key in raw_keys if key not in index]
        if unknown:
            raise ValueError(f"候选原因 {cause['id']} 覆盖了不存在于该运行结果的信号：{unknown[0]}")
        mask = 0
        for key in raw_keys:
            mask |= 1 << index[key]
        covers.append(mask)
        cover_keys.append(raw_keys)

    conflicts_masks = [0] * len(cause_ids)
    for a, b in conflicts or []:
        a, b = int(a), int(b)
        if a == b:
            raise ValueError("同一条候选原因不能与自己互斥")
        if a not in cause_index or b not in cause_index:
            raise ValueError("互斥关系引用了不存在的候选原因")
        i, j = cause_index[a], cause_index[b]
        conflicts_masks[i] |= 1 << j
        conflicts_masks[j] |= 1 << i

    full = (1 << len(keys)) - 1
    signal_causes = [0] * len(keys)
    for i, mask in enumerate(covers):
        for bit in _bits(mask):
            signal_causes[bit] |= 1 << i

    return {
        "run_id": int(run_id),
        "run_input_hash": str(run_input_hash),
        "signals": signals,
        "keys": keys,
        "index": index,
        "cause_ids": cause_ids,
        "cause_index": cause_index,
        "costs": costs,
        "covers": covers,
        "cover_keys": cover_keys,
        "conflicts": conflicts_masks,
        "signal_causes": signal_causes,
        "all_causes": (1 << len(cause_ids)) - 1,
        "full": full,
    }


def _bits(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _mask_to_hex(mask):
    return format(mask, "x")


def _hex_to_mask(value):
    return int(value, 16) if value else 0


def _solution(cost, selected, problem):
    ids = sorted(problem["cause_ids"][i] for i in selected)
    return {"cost": int(cost), "cause_ids": ids, "cause_count": len(ids)}


def _solution_key(solution):
    return (solution["cost"], solution["cause_count"], tuple(solution["cause_ids"]))


def _dual_bound(problem, uncovered, forbidden):
    """Sound integer lower bound for the remaining weighted cover (see module doc)."""
    if uncovered == 0:
        return 0
    available = problem["all_causes"] & ~forbidden
    residual = list(problem["costs"])
    signal_causes = problem["signal_causes"]
    total = 0
    remaining = uncovered
    while remaining:
        chosen = -1
        chosen_count = None
        for s in _bits(remaining):
            count = (signal_causes[s] & available).bit_count()
            if count == 0:
                return None
            if chosen_count is None or count < chosen_count or (count == chosen_count and s < chosen):
                chosen, chosen_count = s, count
        candidates = signal_causes[chosen] & available
        raise_by = min(residual[i] for i in _bits(candidates))
        total += raise_by
        for i in _bits(candidates):
            residual[i] -= raise_by
        remaining &= ~(1 << chosen)
    return total


def _make_node(problem, uncovered, forbidden, selected, cost):
    bound = _dual_bound(problem, uncovered, forbidden)
    return {
        "u": _mask_to_hex(uncovered),
        "f": _mask_to_hex(forbidden),
        "sel": list(selected),
        "cost": int(cost),
        "lb": None if bound is None else int(bound),
    }


def _decode_node(problem, node):
    return _hex_to_mask(node["u"]), _hex_to_mask(node["f"]), tuple(node["sel"]), int(node["cost"])


def _prune_frontier(state):
    best = state.get("best")
    if not best:
        return
    kept = []
    for node in state["stack"]:
        if node["lb"] is None:
            continue
        if node["cost"] > best["cost"]:
            continue
        if node["cost"] + node["lb"] > best["cost"]:
            continue
        kept.append(node)
    state["stack"] = kept


def _consider(state, problem, cost, selected):
    candidate = _solution(cost, selected, problem)
    best = state.get("best")
    if best is None or _solution_key(candidate) < _solution_key(best):
        state["best"] = candidate
        _prune_frontier(state)


def _greedy_incumbent(problem):
    """Cheap feasible seed.  Never used for proof, only as an incumbent."""
    uncovered = problem["full"]
    forbidden = 0
    selected = []
    cost = 0
    while uncovered:
        chosen = -1
        chosen_count = None
        for s in _bits(uncovered):
            count = (problem["signal_causes"][s] & problem["all_causes"] & ~forbidden).bit_count()
            if count and (chosen_count is None or count < chosen_count):
                chosen, chosen_count = s, count
        if chosen < 0:
            return None
        candidates = problem["signal_causes"][chosen] & problem["all_causes"] & ~forbidden
        best_choice = None
        for i in _bits(candidates):
            gain = (problem["covers"][i] & uncovered).bit_count()
            rank = (-gain, problem["costs"][i], problem["cause_ids"][i])
            if best_choice is None or rank < best_choice[0]:
                best_choice = (rank, i)
        i = best_choice[1]
        selected.append(i)
        cost += problem["costs"][i]
        uncovered &= ~problem["covers"][i]
        forbidden |= problem["conflicts"][i] | (1 << i)
    return _solution(cost, selected, problem)


def new_search(problem):
    """Create a fresh checkpoint.  Returns a plain, JSON-safe dict."""
    base = {
        "version": 1,
        "solver": SOLVER_VERSION,
        "rules_hash": rules_hash(problem),
        "stack": [],
        "best": None,
        "nodes": 0,
        "episodes": 0,
        "infeasible_reason": "",
        "uncoverable_signals": [],
    }
    if problem["full"] == 0:
        base["best"] = {"cost": 0, "cause_ids": [], "cause_count": 0}
        return base

    coverable = 0
    for mask in problem["covers"]:
        coverable |= mask
    missing = problem["full"] & ~coverable
    if missing:
        labels = [
            {"sequence": problem["signals"][s]["sequence"], "kind": problem["signals"][s]["kind"]}
            for s in _bits(missing)
        ]
        base["infeasible_reason"] = "存在没有任何候选原因能够解释的信号；无论怎样选择都无法覆盖全部信号。"
        base["uncoverable_signals"] = labels
        return base

    base["best"] = _greedy_incumbent(problem)
    base["stack"] = [_make_node(problem, problem["full"], 0, (), 0)]
    _prune_frontier(base)
    return base


def run_episode(problem, state, node_budget):
    """Expand at most ``node_budget`` nodes.  Mutates and returns ``state``."""
    if node_budget <= 0:
        raise ValueError("单次搜索量必须为正整数")
    if not state.get("stack"):
        return state
    state["episodes"] = int(state.get("episodes", 0)) + 1
    costs = problem["costs"]
    covers = problem["covers"]
    conflicts = problem["conflicts"]
    for _ in range(int(node_budget)):
        if not state["stack"]:
            break
        node = state["stack"].pop()
        state["nodes"] = int(state.get("nodes", 0)) + 1
        best = state.get("best")
        if node["lb"] is None:
            continue
        if best and node["cost"] > best["cost"]:
            continue
        if best and node["cost"] + node["lb"] > best["cost"]:
            continue

        uncovered, forbidden, selected, cost = _decode_node(problem, node)
        if uncovered == 0:
            _consider(state, problem, cost, selected)
            continue

        chosen = -1
        chosen_count = None
        for s in _bits(uncovered):
            count = (problem["signal_causes"][s] & problem["all_causes"] & ~forbidden).bit_count()
            if count and (chosen_count is None or count < chosen_count or (count == chosen_count and s < chosen)):
                chosen, chosen_count = s, count
        if chosen < 0:
            continue

        candidates = problem["signal_causes"][chosen] & problem["all_causes"] & ~forbidden
        order = sorted(_bits(candidates), key=lambda i: (costs[i], problem["cause_ids"][i]))
        # LIFO stack: push the least promising branch first so the cheapest one
        # is expanded first.
        for i in reversed(order):
            child_cost = cost + costs[i]
            best = state.get("best")
            if best and child_cost > best["cost"]:
                continue
            child_uncovered = uncovered & ~covers[i]
            child_forbidden = forbidden | conflicts[i] | (1 << i)
            if child_uncovered == 0:
                _consider(state, problem, child_cost, selected + (i,))
                continue
            bound = _dual_bound(problem, child_uncovered, child_forbidden)
            if bound is None:
                continue
            best = state.get("best")
            if best and child_cost + bound > best["cost"]:
                continue
            state["stack"].append({
                "u": _mask_to_hex(child_uncovered),
                "f": _mask_to_hex(child_forbidden),
                "sel": list(selected + (i,)),
                "cost": int(child_cost),
                "lb": int(bound),
            })
    return state


def search_status(state):
    if state.get("stack"):
        return STATUS_PAUSED
    if state.get("best") is not None:
        return STATUS_OPTIMAL
    return STATUS_INFEASIBLE


def frontier_bound(state):
    """Smallest cost+lower-bound over open nodes, or None when no node is open.

    Each open node stands for at least one feasible completion whose total cost
    is at least ``cost + lb``; the minimum over the frontier is therefore a
    lower bound on the optimum of the *remaining* problem, and because open
    nodes already account for the cost spent to reach them it is also a lower
    bound on the global optimum.
    """
    bounds = [
        int(node["cost"]) + int(node["lb"])
        for node in state.get("stack") or []
        if node.get("lb") is not None
    ]
    return min(bounds) if bounds else None


def snapshot(problem, state):
    status = search_status(state)
    best = state.get("best")
    open_bound = frontier_bound(state)
    if status == STATUS_INFEASIBLE:
        lower_bound = None
    elif open_bound is None:
        lower_bound = best["cost"] if best is not None else None
    elif best is None:
        lower_bound = open_bound
    else:
        # Every open node with cost+lb >= best.cost can only yield solutions
        # at least as expensive as the incumbent; the incumbent itself is a
        # feasible solution, so the optimal cost can be pinned to best.cost.
        lower_bound = min(open_bound, best["cost"])

    cost_proven = (
        best is not None
        and lower_bound is not None
        and lower_bound >= best["cost"]
    )
    reason = state.get("infeasible_reason") or ""
    if status == STATUS_INFEASIBLE and not reason:
        reason = "搜索已穷尽：所有候选组合都因覆盖缺口或互斥约束而不可行。"
    return {
        "status": status,
        "best": best,
        "lower_bound": lower_bound,
        "cost_proven": bool(cost_proven),
        "proof": status == STATUS_OPTIMAL,
        "ties_pending": bool(cost_proven and status != STATUS_OPTIMAL),
        "nodes": int(state.get("nodes", 0)),
        "episodes": int(state.get("episodes", 0)),
        "frontier": len(state.get("stack") or []),
        "rules_hash": state.get("rules_hash", ""),
        "infeasible_reason": reason if status == STATUS_INFEASIBLE else "",
        "uncoverable_signals": state.get("uncoverable_signals", []),
    }


def explain_signals(problem, best):
    """Map every original signal to its candidate causes and selected explainers."""
    selected_mask = 0
    if best is not None:
        for cid in best["cause_ids"]:
            if cid in problem["cause_index"]:
                selected_mask |= 1 << problem["cause_index"][cid]
    rows = []
    for s, signal in enumerate(problem["signals"]):
        candidates = sorted(problem["cause_ids"][i] for i in _bits(problem["signal_causes"][s]))
        explained_by = sorted(
            problem["cause_ids"][i]
            for i in _bits(problem["signal_causes"][s] & selected_mask)
        )
        rows.append({
            "sequence": signal["sequence"],
            "kind": signal["kind"],
            "value": signal.get("value"),
            "candidate_cause_ids": candidates,
            "explained_by": explained_by,
        })
    return rows
