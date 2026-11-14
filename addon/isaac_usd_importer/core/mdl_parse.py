"""Minimal text parser for the *collected* UE4-converted MDL modules that ship
alongside an Omniverse "Collect"-ed USD stage.

These modules are the only place the default texture bindings live: a shader
prim in the USD often authors *no* inputs at all (e.g. `M_TrafficCone`), so the
albedo/normal/ORM maps exist solely as MDL parameter defaults or as inline
`texture_2d("../textures/X.png", ...)` literals in the material body.

Scope is deliberately narrow: we extract parameter defaults and texture
literals. We do not evaluate MDL. Graph topology (lerps, masks) is recognised
separately by `material_spec.py` using the module name.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# `export material Foo(` ... `)` up to the ` = ` that starts the body.
_MATERIAL_HEAD = re.compile(
    r"export\s+material\s+(?P<name>\w+)\s*\((?P<params>.*?)\)\s*\n\s*=\s*\n",
    re.DOTALL,
)
_TEXTURE_2D = re.compile(
    r'texture_2d\s*\(\s*"(?P<path>[^"]+)"\s*,\s*::tex::gamma_(?P<gamma>\w+)\s*\)'
)
_PARAM_TEX = re.compile(
    r"uniform\s+texture_2d\s+(?P<name>\w+)\s*=\s*"
    r'texture_2d\s*\(\s*"(?P<path>[^"]*)"\s*,\s*::tex::gamma_(?P<gamma>\w+)\s*\)'
)
_PARAM_FLOAT4 = re.compile(
    r"float4\s+(?P<name>\w+)\s*=\s*float4\s*\((?P<vals>[^)]*)\)"
)
_PARAM_FLOAT3 = re.compile(
    r"float3\s+(?P<name>\w+)\s*=\s*float3\s*\((?P<vals>[^)]*)\)"
)
_PARAM_FLOAT = re.compile(
    r"(?<!\w)float\s+(?P<name>\w+)\s*=\s*(?P<val>-?[\d.]+(?:[eE][-+]?\d+)?)"
)
_PARAM_COLOR = re.compile(r"color\s+(?P<name>\w+)\s*=\s*color\s*\((?P<vals>[^)]*)\)")


@dataclass
class MdlModule:
    """Parsed defaults for one `export material` block."""

    name: str
    path: Path
    textures: dict[str, tuple[str, str]] = field(default_factory=dict)
    """param name -> (asset path as authored, gamma: 'srgb' | 'linear')"""
    scalars: dict[str, float] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)
    inline_textures: list[tuple[str, str]] = field(default_factory=list)
    """(path, gamma) literals found in the body, i.e. not exposed as params"""

    def resolve(self, asset_path: str) -> Path | None:
        """Anchor an MDL-relative asset path against this module's directory."""
        try:
            return (self.path.parent / asset_path).resolve(strict=True)
        except (OSError, ValueError):
            return None


def _floats(text: str) -> list[float]:
    out: list[float] = []
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            return []
    return out


def parse_mdl(mdl_path: Path) -> dict[str, MdlModule]:
    """Parse every `export material` block in one .mdl file.

    Returns a mapping of material name -> MdlModule. Never raises on malformed
    input; a parse miss just yields fewer defaults, and the caller falls back to
    USD-authored values or the base-material LUT.
    """
    try:
        text = mdl_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log.warning("cannot read MDL %s", mdl_path)
        return {}

    modules: dict[str, MdlModule] = {}
    for head in _MATERIAL_HEAD.finditer(text):
        name = head.group("name")
        params = head.group("params")
        mod = MdlModule(name=name, path=mdl_path)

        for m in _PARAM_TEX.finditer(params):
            if m.group("path"):
                mod.textures[m.group("name")] = (m.group("path"), m.group("gamma"))
        for m in _PARAM_FLOAT4.finditer(params):
            vals = _floats(m.group("vals"))
            if vals:
                mod.vectors[m.group("name")] = vals
        for m in _PARAM_FLOAT3.finditer(params):
            vals = _floats(m.group("vals"))
            if vals:
                mod.vectors[m.group("name")] = vals
        for m in _PARAM_COLOR.finditer(params):
            vals = _floats(m.group("vals"))
            if vals:
                mod.vectors[m.group("name")] = vals
        for m in _PARAM_FLOAT.finditer(params):
            try:
                mod.scalars[m.group("name")] = float(m.group("val"))
            except ValueError:
                pass

        # Body = from the end of the head to the next `export material` or EOF.
        body_start = head.end()
        nxt = _MATERIAL_HEAD.search(text, body_start)
        body = text[body_start : nxt.start() if nxt else len(text)]
        param_paths = {p for p, _ in mod.textures.values()}
        for m in _TEXTURE_2D.finditer(body):
            if m.group("path") and m.group("path") not in param_paths:
                mod.inline_textures.append((m.group("path"), m.group("gamma")))

        modules[name] = mod

    if not modules:
        log.debug("no `export material` block found in %s", mdl_path)
    return modules


def load_mdl_dir(mdl_dir: Path) -> dict[str, MdlModule]:
    """Parse every .mdl in a directory, keyed by material name."""
    out: dict[str, MdlModule] = {}
    if not mdl_dir.is_dir():
        return out
    for f in sorted(mdl_dir.glob("*.mdl")):
        try:
            out.update(parse_mdl(f))
        except Exception:  # a bad module must not kill the export
            log.exception("failed parsing %s", f)
    return out
