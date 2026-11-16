from sqlalchemy import ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..config.database import Base
from ..utils.clock import now


class CoverCause(Base):
    __tablename__ = "cover_causes"
    __table_args__ = (UniqueConstraint("run_id", "title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("control_runs.id", ondelete="CASCADE"))
    title: Mapped[str]
    cost: Mapped[int]
    signals: Mapped[list] = mapped_column(JSON, default=list)
    owner: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)


class CoverConflict(Base):
    __tablename__ = "cover_conflicts"
    __table_args__ = (UniqueConstraint("run_id", "cause_a_id", "cause_b_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("control_runs.id", ondelete="CASCADE"))
    cause_a_id: Mapped[int] = mapped_column(ForeignKey("cover_causes.id", ondelete="CASCADE"))
    cause_b_id: Mapped[int] = mapped_column(ForeignKey("cover_causes.id", ondelete="CASCADE"))
    owner: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)


class CoverSearch(Base):
    __tablename__ = "cover_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("control_runs.id", ondelete="CASCADE"), unique=True)
    rules_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="draft")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)
