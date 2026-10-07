"""Backend package for the AI Interview Simulator.

sentence-transformers is imported here, before any submodule, on purpose.

It pulls in pyarrow through datasets, and on Windows that native extension
segfaults when it is loaded *after* the other native libraries this project
uses (onnxruntime and ctranslate2, both via faster-whisper). Importing it
lazily - which is what `get_semantic_encoder` used to do - crashes the process
reproducibly; importing it first is reproducibly clean.

This has to live in the package root rather than in an entry point, because
the tests and scripts import `backend.*` directly and would otherwise hit the
lazy path and the crash with it.

It costs roughly 13 seconds of process start. Set DISABLE_SEMANTIC_ENCODER=1
to skip it, which falls back to token-overlap matching and a weaker
`sbert_score` term in answer evaluation.
"""

import contextlib as _contextlib
import os as _os

if _os.getenv("DISABLE_SEMANTIC_ENCODER", "").strip().lower() not in {"1", "true", "yes"}:
	with _contextlib.suppress(ImportError):
		import sentence_transformers  # noqa: F401
