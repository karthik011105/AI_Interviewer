"""Download the pretrained models PrepForge runs locally (one-time, ~520 MB).

- all-MiniLM-L6-v2: sentence embeddings for role ranking and role match.
- jjzha/jobbert_knowledge_extraction: JobBERT hard-skill extraction.

Usage: python scripts/download_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import snapshot_download  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

from backend.nlp.skill_extractor import SKILL_MODEL_FILES, SKILL_MODEL_NAME  # noqa: E402


def main() -> None:
	print("Downloading all-MiniLM-L6-v2 ...")
	SentenceTransformer("all-MiniLM-L6-v2")
	print(f"Downloading {SKILL_MODEL_NAME} ...")
	path = snapshot_download(SKILL_MODEL_NAME, allow_patterns=SKILL_MODEL_FILES)
	print(f"Done. Skill model at {path}")


if __name__ == "__main__":
	main()
