"""The architecture rules, enforced (docs/reference-architecture.md §2).

1. Dependencies point inward only: evaluation → L4 edges → L3 adapters → L2 core →
   L1 domain → L0 platform. A module may import its own layer or an inner one.
2. The inner layers (platform, domain, core) import no vendor SDK or web framework.
   Those live in adapters/ and edges/, behind ports.
3. Kernel code never imports product code. Product code lives in a product
   subpackage (``whatsapp``), so the kernel can be lifted into another project as is.
"""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "agent"
# evaluation/ is cross-cutting and outermost: it may import everything, nothing imports it.
LAYER_RANK = {"platform": 0, "domain": 1, "core": 2, "adapters": 3, "edges": 4, "evaluation": 5}
INNER_LAYERS = {"platform", "domain", "core"}
VENDOR_OR_WEB = {
    "anthropic",
    "fastapi",
    "httpx",
    "httpx2",
    "uvicorn",
    "langfuse",
    "sqlalchemy",
    "aiosqlite",
}
PRODUCT = "whatsapp"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _modules() -> list[tuple[str, Path]]:
    return [(layer, p) for layer in LAYER_RANK for p in sorted((PKG / layer).rglob("*.py"))]


def _id(value: object) -> str:
    return str(value.relative_to(PKG)) if isinstance(value, Path) else str(value)


@pytest.mark.parametrize(("layer", "path"), _modules(), ids=_id)
def test_imports_point_inward(layer: str, path: Path):
    for name in _imports(path):
        parts = name.split(".")
        if parts[0] == "agent" and len(parts) > 1 and parts[1] in LAYER_RANK:
            assert LAYER_RANK[parts[1]] <= LAYER_RANK[layer], (
                f"{_id(path)} ({layer}) imports outer layer {name}"
            )


@pytest.mark.parametrize(
    ("layer", "path"), [(lay, p) for lay, p in _modules() if lay in INNER_LAYERS], ids=_id
)
def test_inner_layers_import_no_vendor_or_web_code(layer: str, path: Path):
    bad = {n for n in _imports(path) if n.split(".")[0] in VENDOR_OR_WEB}
    assert not bad, f"{_id(path)} ({layer}) imports {bad}"


@pytest.mark.parametrize(
    ("layer", "path"),
    [(lay, p) for lay, p in _modules() if PRODUCT not in p.relative_to(PKG).parts],
    ids=_id,
)
def test_kernel_never_imports_product_code(layer: str, path: Path):
    bad = {n for n in _imports(path) if PRODUCT in n.split(".")}
    assert not bad, f"kernel module {_id(path)} imports product code {bad}"


def test_core_has_no_product_subpackage():
    assert not (PKG / "core" / PRODUCT).exists(), "core/ is kernel only"
