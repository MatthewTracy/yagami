from __future__ import annotations

from dataclasses import dataclass
from collections import Counter

from ..router.fast_path import _has_phi, _has_secret
from ..router.schema import Sensitivity
from .transform import detect_entity_types


@dataclass(frozen=True)
class OutputInspection:
    sensitivity: Sensitivity
    entity_counts: dict[str, int]

    def summary(self) -> dict:
        return {
            "sensitivity": self.sensitivity.value,
            "entity_counts": dict(sorted(self.entity_counts.items())),
        }


def inspect_output(text: str) -> OutputInspection:
    entity_counts = dict(Counter(detect_entity_types(text)))
    entity_types = set(entity_counts)
    if _has_secret(text) or entity_types.intersection({"API_KEY", "AWS_KEY", "JWT"}):
        sensitivity = Sensitivity.SECRET
    elif _has_phi(text) or entity_types:
        sensitivity = Sensitivity.PHI
    else:
        sensitivity = Sensitivity.NONE
    return OutputInspection(sensitivity=sensitivity, entity_counts=entity_counts)
