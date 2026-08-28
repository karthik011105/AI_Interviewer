"""Optional registry for local research models and notebooks.

This module intentionally does not load or execute any model checkpoints or
notebooks. It only indexes them as local research assets so the application can
surface their presence without coupling production interview flows to them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict


class ResearchAssetEntry(TypedDict):
	name: str
	asset_type: str
	relative_path: str
	extension: str
	size_bytes: int
	status: str


def _workspace_root() -> Path:
	return Path(__file__).resolve().parent.parent


def _collect_assets(*, asset_type: str, directory: Path, extensions: set[str]) -> list[ResearchAssetEntry]:
	if not directory.exists() or not directory.is_dir():
		return []

	workspace_root = _workspace_root()
	entries: list[ResearchAssetEntry] = []
	for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
		if not path.is_file() or path.suffix.lower() not in extensions:
			continue
		try:
			relative_path = path.relative_to(workspace_root).as_posix()
		except ValueError:
			relative_path = path.as_posix()
		entries.append(
			ResearchAssetEntry(
				name=path.name,
				asset_type=asset_type,
				relative_path=relative_path,
				extension=path.suffix.lower(),
				size_bytes=path.stat().st_size,
				status="registered_local_asset",
			)
		)
	return entries


@lru_cache(maxsize=1)
def get_research_asset_registry() -> dict[str, Any]:
	workspace_root = _workspace_root()
	model_assets = _collect_assets(
		asset_type="model_checkpoint",
		directory=workspace_root / "models",
		extensions={".pth", ".pt", ".bin", ".onnx", ".safetensors"},
	)
	notebook_assets = _collect_assets(
		asset_type="research_notebook",
		directory=workspace_root / "notebooks",
		extensions={".ipynb"},
	)
	return {
		"available": bool(model_assets or notebook_assets),
		"mode": "optional_registry",
		"status": "standby",
		"model_count": len(model_assets),
		"notebook_count": len(notebook_assets),
		"models": model_assets,
		"notebooks": notebook_assets,
	}


def warmup_research_asset_registry() -> dict[str, Any]:
	get_research_asset_registry.cache_clear()
	return get_research_asset_registry()


def get_research_asset_summary() -> dict[str, Any]:
	registry = get_research_asset_registry()
	return {
		"available": bool(registry.get("available")),
		"mode": str(registry.get("mode") or "optional_registry"),
		"status": str(registry.get("status") or "standby"),
		"model_count": int(registry.get("model_count") or 0),
		"notebook_count": int(registry.get("notebook_count") or 0),
		"registered_model_names": [entry["name"] for entry in registry.get("models") or []],
		"registered_notebook_names": [entry["name"] for entry in registry.get("notebooks") or []],
	}


__all__ = [
	"ResearchAssetEntry",
	"get_research_asset_registry",
	"get_research_asset_summary",
	"warmup_research_asset_registry",
]