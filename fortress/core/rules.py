"""Rules engine — fast path for common patterns (no LLM needed)."""

import ast
import logging
from dataclasses import dataclass, field

from fortress.core.event_bus import Event

logger = logging.getLogger("fortress.rules")

# Allowed AST node types for safe condition evaluation
_SAFE_NODES = (
    ast.Expression, ast.Call, ast.Attribute, ast.Name, ast.Constant,
    ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.IfExp,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
    ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.Load,
)

# Block dangerous builtins even if they appear as names
_BLOCKED_NAMES = {"exec", "eval", "compile", "open", "input", "__import__",
                  "getattr", "setattr", "delattr", "globals", "locals", "vars"}

_SAFE_ATTRS = {"get", "endswith", "startswith", "lower", "upper", "strip", "replace"}
_SAFE_NAMES = {"payload", "event", "e", "True", "False", "None", "len", "str", "int", "float", "bool"}


def _safe_eval(condition: str, event: Event) -> bool:
    """Evaluate condition via AST validation — no arbitrary code execution."""
    if not condition:
        return True

    try:
        tree = ast.parse(condition, mode="eval")
    except SyntaxError:
        logger.warning(f"Invalid condition: {condition}")
        return False

    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODES):
            logger.warning(f"Blocked: {type(node).__name__}")
            return False
        if isinstance(node, ast.Attribute) and node.attr not in _SAFE_ATTRS:
            logger.warning(f"Blocked attr: {node.attr}")
            return False
        if isinstance(node, ast.Name) and (node.id not in _SAFE_NAMES or node.id in _BLOCKED_NAMES):
            logger.warning(f"Blocked name: {node.id}")
            return False

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, {"event": event, "payload": event.payload, "e": event})
        return bool(result)
    except Exception as e:
        logger.warning(f"Condition error: {e}")
        return False


@dataclass
class Action:
    """An action to execute."""

    type: str         # "move", "notify", "ha_call", "execute_bash", "log"
    params: dict = field(default_factory=dict)


@dataclass
class Rule:
    """A rule that matches events and produces actions."""

    name: str
    event_pattern: str
    condition: str = ""
    action: Action = field(default_factory=lambda: Action(type="log"))
    enabled: bool = True
    priority: int = 0

    def matches(self, event: Event) -> bool:
        if not self.enabled:
            return False
        if not self._match_pattern(event.type):
            return False
        if self.condition and not _safe_eval(self.condition, event):
            return False
        return True

    def _match_pattern(self, event_type: str) -> bool:
        if self.event_pattern == "*":
            return True
        if "*" not in self.event_pattern:
            return self.event_pattern == event_type
        return event_type.startswith(self.event_pattern.rstrip("*"))


class RulesEngine:
    """Fast path: match events to rules without LLM."""

    def __init__(self):
        self.rules: list[Rule] = []

    def add_rule(self, rule: Rule) -> None:
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)

    def load_from_config(self, rules_config: list) -> None:
        for rc in rules_config:
            # Support both dict and Pydantic model
            if hasattr(rc, "model_dump"):
                rc = rc.model_dump()
            action_data = rc.get("action", {})
            if isinstance(action_data, dict):
                action = Action(type=action_data.get("type", "log"), params={k: v for k, v in action_data.items() if k != "type"})
            else:
                action = Action(type="log")
            self.rules.append(Rule(
                name=rc.get("name", "unnamed"), event_pattern=rc.get("event", rc.get("event_pattern", "*")),
                condition=rc.get("condition", ""), action=action,
                enabled=rc.get("enabled", True), priority=rc.get("priority", 0),
            ))
        # Sort once after all rules loaded
        self.rules.sort(key=lambda r: r.priority, reverse=True)

    def match(self, event: Event) -> Action | None:
        for rule in self.rules:
            if rule.matches(event):
                logger.info(f"Rule matched: {rule.name}")
                return rule.action
        return None

    def list_rules(self) -> list[dict]:
        return [{"name": r.name, "event": r.event_pattern, "condition": r.condition,
                 "action": r.action.type, "enabled": r.enabled} for r in self.rules]
