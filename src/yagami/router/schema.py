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
