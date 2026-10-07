"""Smoke test: el ambiente y el paquete están bien montados."""

import sys


def test_python_is_312():
    assert sys.version_info[:2] == (3, 12)


def test_core_deps_import():
    import anthropic  # noqa: F401
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
    import pydantic_settings  # noqa: F401


def test_agent_package_importable():
    import agent

    assert agent.__version__ == "0.1.0"
