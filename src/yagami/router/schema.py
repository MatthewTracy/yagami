from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Intent(str, Enum):
    SIMPLE_QA = "simple_qa"
    COMPLEX_REASONING = "complex_reasoning"
    CODE = "code"
    CREATIVE = "creative"
    IMAGE = "image"


class Sensitivity(str, Enum):
    NONE = "none"
    PHI = "phi"
    PHI_MEDICAL = "phi_medical"
    SECRET = "secret"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Classification(BaseModel):
    intent: Intent = Intent.SIMPLE_QA
    sensitivity: Sensitivity = Sensitivity.NONE
    complexity: Complexity = Complexity.LOW
    # v0.2.14: opt-in tool use. Classifier sets True for prompts that name
    # arithmetic / lookups / "fetch" / "calculate" / etc. Routing then
    # branches into the tool_loop when the chosen backend has TOOLS capability.
    needs_tools: bool = False
    # v0.2.16: opt-in cross-session recall. Classifier sets True for prompts
    # that REFER to prior conversations ("what did we discuss", "what was my
    # dog's name", "remember when I mentioned X"). Retriever then fetches
    # top-K observations and the policy injects them as system messages.
    needs_recall: bool = False
