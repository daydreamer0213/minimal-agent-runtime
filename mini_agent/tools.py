"""Registered, dependency-free tools for the minimal agent runtime."""

import ast
import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .store import SessionStore


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, str, SessionStore], dict]


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        if tool.name in self._tools:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def execute(self, name, arguments, session_id, store):
        tool = self._tools.get(name)
        if tool is None:
            return {
                "ok": False,
                "error": f"未知工具: {name}",
                "available_tools": sorted(self._tools),
            }
        try:
            validate(arguments, tool.parameters)
            return tool.handler(arguments, session_id, store)
        except (ValueError, TypeError, ArithmeticError) as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:
            return {"ok": False, "error": f"工具执行失败: {type(error).__name__}"}


def validate(value: Any, schema: dict, field: str = "arguments") -> None:
    """Validate the small JSON Schema subset accepted by the runtime."""
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise TypeError(f"参数 {field} 必须是 object")
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"缺少必填参数: {name}")
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], name)
    elif schema_type == "string":
        if not isinstance(value, str):
            raise TypeError(f"参数 {field} 必须是 string")
    elif schema_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"参数 {field} 必须是 number")
    elif schema_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"参数 {field} 必须是 integer")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise TypeError(f"参数 {field} 必须是 boolean")
    elif schema_type == "array":
        if not isinstance(value, list):
            raise TypeError(f"参数 {field} 必须是 array")
        item_schema = schema.get("items")
        if item_schema:
            for item in value:
                validate(item, item_schema, field)

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"参数 {field} 必须是以下之一: {schema['enum']}")


_ALLOWED_BINARY_OPERATORS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
    ast.Pow: lambda left, right: left**right,
}

_ALLOWED_UNARY_OPERATORS = {
    ast.UAdd: lambda value: +value,
    ast.USub: lambda value: -value,
}


def _evaluate_expression(expression: str) -> int | float:
    if len(expression) > 200:
        raise ValueError("表达式长度不能超过 200")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError("不支持的表达式") from error

    def require_finite_number(value: Any, message: str) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(message)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("计算结果必须是有限数字")
        return value

    def evaluate(node: ast.AST) -> int | float:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("不支持的常量")
            return require_finite_number(node.value, "不支持的常量")
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY_OPERATORS:
            return require_finite_number(
                _ALLOWED_UNARY_OPERATORS[type(node.op)](evaluate(node.operand)),
                "不支持的计算结果",
            )
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY_OPERATORS:
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 20:
                raise ValueError("指数绝对值不能超过 20")
            return require_finite_number(
                _ALLOWED_BINARY_OPERATORS[type(node.op)](left, right),
                "不支持的计算结果",
            )
        raise ValueError("不支持的表达式")

    return require_finite_number(evaluate(tree.body), "不支持的计算结果")


def _calculator(arguments: dict, _session_id: str, _store: SessionStore) -> dict:
    return {"ok": True, "result": _evaluate_expression(arguments["expression"])}


_SEARCH_DOCUMENTS = [
    {"title": "Agent Runtime", "content": "Agent Runtime 负责协调模型调用、工具执行与上下文。"},
    {"title": "工具注册", "content": "工具通过名称、描述、参数 Schema 和处理函数统一注册。"},
    {"title": "会话存储", "content": "每个会话的消息、待办和追踪记录存储在 SQLite 中。"},
    {"title": "上下文压缩", "content": "长对话会压缩较早轮次，同时保留最近的完整对话。"},
    {"title": "模拟天气", "content": "天气工具使用确定性模拟数据，方便离线演示和测试。"},
    {"title": "安全计算器", "content": "计算器只处理受限算术 AST，不执行名称或函数调用。"},
]


def _search(arguments: dict, _session_id: str, _store: SessionStore) -> dict:
    query = arguments["query"].strip()
    if not query:
        raise ValueError("query 不能为空")
    terms = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
    results = [
        document
        for document in _SEARCH_DOCUMENTS
        if any(term in f"{document['title']} {document['content']}".lower() for term in terms)
    ]
    return {"ok": True, "results": results[:5], "source": "mock"}


def _weather(arguments: dict, _session_id: str, _store: SessionStore) -> dict:
    city = arguments["city"]
    date = arguments["date"]
    digest = hashlib.sha256(f"{city}|{date}".encode()).digest()
    conditions = ("晴", "多云", "阴", "小雨", "雷阵雨")
    return {
        "ok": True,
        "city": city,
        "date": date,
        "weather": conditions[digest[0] % len(conditions)],
        "temperature_c": 15 + digest[1] % 16,
        "source": "mock",
    }


def _todo(arguments: dict, session_id: str, store: SessionStore) -> dict:
    action = arguments["action"]
    if action == "add":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text 不能为空")
        return {"ok": True, "item": store.add_todo(session_id, text)}
    if action == "list":
        return {"ok": True, "items": store.list_todos(session_id)}
    todo_id = arguments.get("id")
    if isinstance(todo_id, bool) or not isinstance(todo_id, int) or todo_id <= 0:
        raise ValueError("id 必须是正整数")
    return {"ok": True, "done": store.finish_todo(session_id, todo_id)}


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            "calculator",
            "计算受限的数学表达式。",
            {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
            _calculator,
        )
    )
    registry.register(
        Tool(
            "search",
            "搜索内置资料。结果为模拟数据。",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            _search,
        )
    )
    registry.register(
        Tool(
            "weather",
            "查询指定城市和日期的模拟天气。",
            {
                "type": "object",
                "properties": {"city": {"type": "string"}, "date": {"type": "string"}},
                "required": ["city", "date"],
            },
            _weather,
        )
    )
    registry.register(
        Tool(
            "todo",
            "添加、列出或完成当前会话的待办事项。",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list", "done"]},
                    "text": {"type": "string"},
                    "id": {"type": "integer"},
                },
                "required": ["action"],
            },
            _todo,
        )
    )
    return registry
