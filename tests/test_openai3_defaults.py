from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def function_return_dict(tree: ast.Module, function_name: str) -> dict:
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return_node = next(
        node for node in ast.walk(function) if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    )
    result = {}
    for key, value in zip(return_node.value.keys, return_node.value.values):
        if isinstance(key, ast.Constant) and isinstance(value, ast.BoolOp):
            result[key.value] = ast.literal_eval(value.values[-1])
    return result


def dict_literal_value(node: ast.Dict, wanted_key: str):
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == wanted_key:
            return ast.literal_eval(value)
    raise KeyError(wanted_key)


class OpenAI3DefaultGroupTests(unittest.TestCase):
    def test_webapp_defaults_to_outlook_default_group(self) -> None:
        tree = parse(ROOT / "tools/openai3/webapp.py")
        start_req = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "StartReq"
        )
        source_field = next(
            node
            for node in start_req.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "mail_source_group"
        )
        self.assertEqual(ast.literal_eval(source_field.value), "默认分组")

        load_config = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "load_config"
        )
        defaults_assignment = next(
            node
            for node in load_config.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "defaults"
        )
        self.assertEqual(
            dict_literal_value(defaults_assignment.value, "mail_source_group"),
            "默认分组",
        )
        self.assertEqual(
            dict_literal_value(defaults_assignment.value, "sub2api_group"),
            "auto",
        )
        keys = [key.value for key in defaults_assignment.value.keys if isinstance(key, ast.Constant)]
        self.assertNotIn("mail_base", keys)

    def test_mail_bridge_defaults_to_outlook_default_group(self) -> None:
        tree = parse(ROOT / "tools/openai3/mail_bridge.py")
        defaults = function_return_dict(tree, "load_cfg")
        self.assertEqual(defaults["mail_source_group"], "默认分组")


if __name__ == "__main__":
    unittest.main()
