"""Isaac Sim USD Importer.

Imports Isaac Sim / Omniverse USD stages into Blender with their MDL materials
translated to Blender node graphs.

Runs entirely in-process: Blender bundles OpenUSD's Python bindings, so no
wheels, no subprocess and no external Python are required. (Injecting a second
OpenUSD build into Blender does NOT work -- it collides with the one already
loaded -- so always use the bundled `pxr`.)
"""

from . import operators


def register():
    operators.register()


def unregister():
    operators.unregister()
