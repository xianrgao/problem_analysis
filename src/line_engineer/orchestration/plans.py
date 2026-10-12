from __future__ import annotations
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Step(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,60}$")
    kind: Literal["skill", "tool"] = "skill"
    skill: str | None = None
    tool: str | None = None
    title: str
    description: str
    expected_output: str = ""
    depends_on: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    progress_message: str = ""

    @model_validator(mode="after")
    def target(self):
        if not (self.skill if self.kind == "skill" else self.tool):
            raise ValueError("节点缺少技能或工具名称")
        return self


class PlanDraft(BaseModel):
    title: str
    summary: str
    stage: str = "investigation"
    nodes: list[Step] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def acyclic(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("计划步骤 ID 重复")
        done = set()
        for node in self.nodes:
            if set(node.depends_on) - ids:
                raise ValueError("计划依赖不存在")
        while len(done) < len(ids):
            ready = [n.id for n in self.nodes if n.id not in done and set(n.depends_on) <= done]
            if not ready:
                raise ValueError("计划包含循环依赖")
            done.update(ready)
        return self


def fingerprint(plan: dict):
    value = {key: plan.get(key) for key in ("id", "session_id", "revision", "title", "summary", "stage", "nodes", "skill_versions", "input_versions")}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
