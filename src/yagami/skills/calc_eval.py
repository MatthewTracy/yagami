"""Safe math evaluator — handles arithmetic without spawning Python's full eval.

Uses Python's `ast.parse` to walk a literal expression tree, allowing only
numbers, basic operators, and a small whitelist of math functions. Refuses
attribute access, function calls outside the whitelist, names, comprehensions,
generators — anything that could escape into the wider runtime.
"""

from __future__ import annotations

import ast
import math
import operator

from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FUNCS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "factorial": math.factorial,
    "min": min,
    "max": max,
    "pow": pow,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"operator {type(node.op).__name__} not allowed")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unary {type(node.op).__name__} not allowed")
        return op(_safe_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"name {node.id!r} not allowed")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError(
                f"call to {ast.dump(node.func)} not allowed; whitelist: {sorted(_FUNCS)}"
            )
        if node.keywords:
            raise ValueError("keyword args not allowed")
        args = [_safe_eval(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ValueError(f"AST node {type(node).__name__} not allowed")


class CalcEval:
    name = "calc.eval"
    description = (
        "Evaluate a math expression. Supports +, -, *, /, //, %, **, "
        "and functions: sqrt, log, log2, log10, exp, sin, cos, tan, abs, "
        "floor, ceil, round, factorial, min, max, pow. Constants: pi, e, tau. "
        "Returns the numeric result as a string."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate, e.g. 'sqrt(2) * pi' or '14!'",
            }
        },
        "required": ["expression"],
    }
    requires_network = False
    sensitivity_ceiling = Sensitivity.PHI_MEDICAL  # math is fine in any session

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        expr = args.get("expression", "")
        if not expr:
            return SkillResult(ok=False, error="missing 'expression'")
        try:
            tree = ast.parse(expr, mode="eval")
            value = _safe_eval(tree)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
            return SkillResult(ok=False, error=str(exc))
        return SkillResult(ok=True, content=str(value))


def build() -> Skill:
    return CalcEval()
