"""Cheap guards against mistakes that otherwise fail silently."""
import ast
from pathlib import Path

import pytest

MODULES = sorted((Path(__file__).resolve().parent.parent / "tidyarr").glob("*.py"))


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_function_or_class_is_defined_twice_at_the_top_level(module):
    """A second definition silently replaces the first, so the first one's tests and callers get the wrong code."""
    names = [node.name for node in ast.parse(module.read_text()).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"{module.name} defines {duplicates} more than once"
