from pydantic import BaseModel, Field, field_validator


class CoverSignal(BaseModel):
    sequence: int = Field(ge=1)
    kind: str = Field(pattern="^(individual|moving_range|ewma)$")


class CauseInput(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    cost: int = Field(gt=0, le=1_000_000)
    signals: list[CoverSignal] = Field(min_length=1, max_length=2000)


class ConflictInput(BaseModel):
    cause_a_id: int
    cause_b_id: int

    @field_validator("cause_b_id")
    @classmethod
    def _different(cls, value, info):
        if value == info.data.get("cause_a_id"):
            raise ValueError("互斥关系需要两条不同的候选原因")
        return value


class SearchInput(BaseModel):
    node_budget: int = Field(default=20000, gt=0, le=200000)
