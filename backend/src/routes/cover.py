from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config.database import get_db
from ..controllers.dependencies import current_user, roles
from ..constants.roles import Role
from ..schemas.cover import CauseInput, ConflictInput, SearchInput
from ..services.cover_service import (
    add_cause,
    add_conflict,
    get_cover,
    remove_cause,
    remove_conflict,
    reset_search,
    search,
)

router = APIRouter(prefix="/runs/{run_id}/cover", tags=["cover"])


@router.get("")
def show(run_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    return get_cover(db, run_id)


@router.post("/causes", status_code=201)
def create_cause(
    run_id: int,
    data: CauseInput,
    db: Session = Depends(get_db),
    user=Depends(roles(Role.ADMIN, Role.ANALYST)),
):
    return add_cause(db, run_id, data, user)


@router.delete("/causes/{cause_id}")
def delete_cause(
    run_id: int,
    cause_id: int,
    db: Session = Depends(get_db),
    user=Depends(roles(Role.ADMIN, Role.ANALYST)),
):
    return remove_cause(db, run_id, cause_id, user)


@router.post("/conflicts", status_code=201)
def create_conflict(
    run_id: int,
    data: ConflictInput,
    db: Session = Depends(get_db),
    user=Depends(roles(Role.ADMIN, Role.ANALYST)),
):
    return add_conflict(db, run_id, data, user)


@router.delete("/conflicts/{conflict_id}")
def delete_conflict(
    run_id: int,
    conflict_id: int,
    db: Session = Depends(get_db),
    user=Depends(roles(Role.ADMIN, Role.ANALYST)),
):
    return remove_conflict(db, run_id, conflict_id, user)


@router.post("/search")
def run_search(
    run_id: int,
    data: SearchInput,
    db: Session = Depends(get_db),
    user=Depends(roles(Role.ADMIN, Role.ANALYST)),
):
    return search(db, run_id, data.node_budget, user)


@router.delete("/search")
def clear_search(
    run_id: int,
    db: Session = Depends(get_db),
    user=Depends(roles(Role.ADMIN, Role.ANALYST)),
):
    return reset_search(db, run_id, user)
