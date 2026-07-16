"""Butler tools package.

Shared tools (such as extraction_queue) live directly in this package.
Butler-specific tools live in ``roster/<name>/tools.py`` (single file) or
``roster/<name>/tools/`` (package directory) and are loaded dynamically via
``_loader.register_all_butler_tools()``.

On first import of this package, all known butler tools are registered in
``sys.modules`` so that ``from butlers.tools.<name> import ...`` works
transparently throughout the codebase.
"""

from butlers.tools._loader import register_all_butler_tools

register_all_butler_tools()
