"""HUB-BOT module system — baked-in modules discovered under this package.

Framework files: ``api`` (contract), ``loader`` (discovery/load). Everything
else that is a sub-package with a ``manifest.py`` is a module.

Uploaded (external) modules live in :data:`EXTERNAL_MODULES_DIR`, a shared
writable volume. That directory is spliced onto this package's ``__path__``
*after* the built-in location, so ``import src.bot.modules.<name>`` resolves
uploads too, while built-ins always win a name clash. Both the loader's
``discover()`` and ``config_registry`` pick uploads up with no extra wiring.
"""

import os as _os
from pathlib import Path as _Path

EXTERNAL_MODULES_DIR = _os.environ.get("HUB_EXT_MODULES_DIR", "/app/ext_modules")

try:  # best-effort: never let a missing/odd volume break module import
    _ext = _Path(EXTERNAL_MODULES_DIR)
    if _ext.is_dir() and str(_ext) not in __path__:
        __path__.append(str(_ext))
except Exception:  # pragma: no cover - defensive
    pass
