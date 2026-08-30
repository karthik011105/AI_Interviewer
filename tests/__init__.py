"""Test package for unittest discovery.

Importing `backend` here is load-bearing, not cosmetic. The package root
imports sentence-transformers before anything else so that pyarrow's native
extension loads ahead of onnxruntime and ctranslate2 - on Windows the reverse
order segfaults the process. Discovery imports test modules directly, and the
first one to reach a native library other than through `backend` would lose
that ordering and take the whole run down with it.

See backend/__init__.py for the full explanation.
"""

import backend  # noqa: F401
