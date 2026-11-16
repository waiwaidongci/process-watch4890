import copy

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from ..models import ControlRun, CoverCause, CoverConflict, CoverSearch
from ..utils.clock import now
from ..utils.serialize import record
from . import cover_solver as solver
from .audit_service import log

MAX_CAUSES = 200


def _completed_run(db, run_id):
    run = db.get(ControlRun, run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    if run.status != "completed":
        raise HTTPException(409, "只有已完成的运行才能登记排查原因")
    result = run.result or {}
    if "signals" not in result:
        raise HTTPException(409, "运行结果缺少信号清单，无法建立排查组合")
    return run


def _cause_rows(db, run_id):
    return db.query(CoverCause).filter_by(run_id=run_id).order_by(CoverCause.id).all()


def _conflict_rows(db, run_id):
    return db.query(CoverConflict).filter_by(run_id=run_id).order_by(CoverConflict.id).all()


def _problem(db, run):
    causes = _cause_rows(db, run.id)
    conflicts = _conflict_rows(db, run.id)
    payload = [
        {"id": c.id, "cost": c.cost, "signals": c.signals or []}
        for c in causes
    ]
    pairs = [(c.cause_a_id, c.cause_b_id) for c in conflicts]
    try:
        problem = solver.build_problem(
            run.id,
            run.result.get("input_hash", ""),
            run.result.get("signals", []),
            payload,
            pairs,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return problem, causes, conflicts


def _search_row(db, run_id):
    return db.query(CoverSearch).filter_by(run_id=run_id).first()


def _invalidate(db, run_id, actor, reason):
    row = _search_row(db, run_id)
    if not row:
        return False
    db.delete(row)
    db.flush()
    log(db, actor, "cover.search.invalidated", reason)
    return True


def _serialise_state(row):
    # SQLAlchemy does not track in-place mutation of JSON columns, so every read
    # and write goes through a deep copy; otherwise a resumed episode would be
    # computed in memory and silently dropped on commit.
    return copy.deepcopy(row.state or {})


def _empty_payload(run, problem, causes, conflicts, state, snapshot, explanations):
    return {
        "run_id": run.id,
        "run_input_hash": run.result.get("input_hash", ""),
        "signal_total": len(problem["signals"]),
        "signals": explanations,
        "causes": [
            {
                "id": c.id,
                "title": c.title,
                "cost": c.cost,
                "signals": c.signals or [],
                "owner": c.owner,
                "created_at": c.created_at,
            }
            for c in causes
        ],
        "conflicts": [
            {"id": c.id, "cause_a_id": c.cause_a_id, "cause_b_id": c.cause_b_id}
            for c in conflicts
        ],
        "search": snapshot,
        "rules_hash": problem and solver.rules_hash(problem),
        "max_causes": MAX_CAUSES,
    }


def get_cover(db, run_id):
    run = _completed_run(db, run_id)
    problem, causes, conflicts = _problem(db, run)
    row = _search_row(db, run_id)
    current = solver.rules_hash(problem)
    if row and row.rules_hash != current:
        # Defensive: a rule change that slipped past invalidation must never
        # reuse a checkpoint built for different rules.
        db.delete(row)
        db.commit()
        row = None
    if row:
        state = _serialise_state(row)
        snap = solver.snapshot(problem, state)
        snap["status"] = solver.STATUS_PAUSED if state.get("stack") else snap["status"]
    else:
        state = solver.new_search(problem)
        snap = solver.snapshot(problem, state)
        if problem["full"] == 0:
            # No signals: the empty combination is genuinely optimal and needs
            # no search, so do not dress it up as an unstarted draft.
            snap["status"] = solver.STATUS_OPTIMAL
        else:
            snap["status"] = solver.STATUS_DRAFT
            snap["best"] = None
            snap["lower_bound"] = None
            snap["cost_proven"] = False
            snap["proof"] = False
            snap["ties_pending"] = False
            snap["nodes"] = 0
            snap["episodes"] = 0
            snap["frontier"] = 0
    best = snap.get("best")
    explanations = solver.explain_signals(problem, best)
    return _empty_payload(run, problem, causes, conflicts, state, snap, explanations)


def add_cause(db, run_id, data, user):
    run = _completed_run(db, run_id)
    if len(_cause_rows(db, run_id)) >= MAX_CAUSES:
        raise HTTPException(422, f"单个运行最多登记 {MAX_CAUSES} 条候选原因")
    if db.query(CoverCause).filter_by(run_id=run_id, title=data.title).first():
        raise HTTPException(409, "该运行已登记同名候选原因")
    result_keys = {solver.signal_key(s["sequence"], s["kind"]) for s in run.result["signals"]}
    for item in data.signals:
        if solver.signal_key(item.sequence, item.kind) not in result_keys:
            raise HTTPException(422, f"该运行不存在信号 #{item.sequence} / {item.kind}")
    row = CoverCause(
        run_id=run_id,
        title=data.title,
        cost=data.cost,
        signals=[{"sequence": s.sequence, "kind": s.kind} for s in data.signals],
        owner=user.name,
    )
    db.add(row)
    db.flush()
    invalidated = _invalidate(db, run_id, user.email, f"run={run_id} cause={row.id}")
    log(db, user.email, "cover.cause.add", row.id)
    db.commit()
    return {"id": row.id, "invalidated": invalidated}


def remove_cause(db, run_id, cause_id, user):
    _completed_run(db, run_id)
    row = db.get(CoverCause, cause_id)
    if not row or row.run_id != run_id:
        raise HTTPException(404, "候选原因不存在")
    db.query(CoverConflict).filter(
        CoverConflict.run_id == run_id,
        (CoverConflict.cause_a_id == cause_id) | (CoverConflict.cause_b_id == cause_id),
    ).delete(synchronize_session=False)
    db.delete(row)
    db.flush()
    invalidated = _invalidate(db, run_id, user.email, f"run={run_id} cause-removed={cause_id}")
    log(db, user.email, "cover.cause.remove", cause_id)
    db.commit()
    return {"invalidated": invalidated}


def add_conflict(db, run_id, data, user):
    _completed_run(db, run_id)
    a = db.get(CoverCause, data.cause_a_id)
    b = db.get(CoverCause, data.cause_b_id)
    if not a or not b or a.run_id != run_id or b.run_id != run_id:
        raise HTTPException(422, "互斥关系只能连接同一运行下的两条候选原因")
    existing = (
        db.query(CoverConflict)
        .filter_by(run_id=run_id, cause_a_id=data.cause_a_id, cause_b_id=data.cause_b_id)
        .first()
        or db.query(CoverConflict)
        .filter_by(run_id=run_id, cause_a_id=data.cause_b_id, cause_b_id=data.cause_a_id)
        .first()
    )
    if existing:
        raise HTTPException(409, "这两条候选原因已登记互斥")
    row = CoverConflict(
        run_id=run_id,
        cause_a_id=data.cause_a_id,
        cause_b_id=data.cause_b_id,
        owner=user.name,
    )
    db.add(row)
    db.flush()
    invalidated = _invalidate(db, run_id, user.email, f"run={run_id} conflict={row.id}")
    log(db, user.email, "cover.conflict.add", row.id)
    db.commit()
    return {"id": row.id, "invalidated": invalidated}


def remove_conflict(db, run_id, conflict_id, user):
    _completed_run(db, run_id)
    row = db.get(CoverConflict, conflict_id)
    if not row or row.run_id != run_id:
        raise HTTPException(404, "互斥关系不存在")
    db.delete(row)
    db.flush()
    invalidated = _invalidate(db, run_id, user.email, f"run={run_id} conflict-removed={conflict_id}")
    log(db, user.email, "cover.conflict.remove", conflict_id)
    db.commit()
    return {"invalidated": invalidated}


def reset_search(db, run_id, user):
    _completed_run(db, run_id)
    row = _search_row(db, run_id)
    if not row:
        return {"reset": False}
    db.delete(row)
    log(db, user.email, "cover.search.reset", run_id)
    db.commit()
    return {"reset": True}


def search(db, run_id, node_budget, user):
    run = _completed_run(db, run_id)
    problem, causes, conflicts = _problem(db, run)
    if problem["full"] and not _cause_rows(db, run_id):
        raise HTTPException(409, "请先登记至少一条候选原因")
    current = solver.rules_hash(problem)
    row = _search_row(db, run_id)
    started = False
    if row and row.rules_hash != current:
        db.delete(row)
        db.commit()
        row = None
        log(db, user.email, "cover.search.invalidated", f"run={run_id} stale-hash")
    if not row:
        state = solver.new_search(problem)
        row = CoverSearch(
            run_id=run_id,
            rules_hash=current,
            status=solver.STATUS_DRAFT,
            state=state,
        )
        db.add(row)
        db.flush()
        started = True
    else:
        state = _serialise_state(row)
    if state.get("stack"):
        solver.run_episode(problem, state, node_budget)
    snap = solver.snapshot(problem, state)
    row.state = copy.deepcopy(state)
    # JSON columns keep the Python object by reference; without an explicit
    # flag SQLAlchemy may compare the new value against an aliased old value and
    # skip the UPDATE, silently losing a resumed episode.
    flag_modified(row, "state")
    row.rules_hash = current
    row.status = snap["status"]
    row.updated_at = now()
    log(db, user.email, "cover.search.run", run_id)
    db.commit()
    best = snap.get("best")
    explanations = solver.explain_signals(problem, best)
    payload = _empty_payload(run, problem, causes, conflicts, state, snap, explanations)
    payload["started"] = started
    return payload
