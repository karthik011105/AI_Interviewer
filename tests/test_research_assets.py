from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import create_app
from backend import research_assets


class ResearchAssetRegistryTests(TestCase):
	def tearDown(self) -> None:
		research_assets.get_research_asset_registry.cache_clear()

	def test_registry_indexes_local_model_and_notebook_assets(self) -> None:
		with TemporaryDirectory() as temp_dir:
			root = Path(temp_dir)
			models_dir = root / "models"
			notebooks_dir = root / "notebooks"
			models_dir.mkdir()
			notebooks_dir.mkdir()
			(models_dir / "parser_model.pth").write_bytes(b"1234")
			(models_dir / "job_matcher_model.pth").write_bytes(b"5678")
			(notebooks_dir / "Parser.ipynb").write_text("{}", encoding="utf-8")

			with patch("backend.research_assets._workspace_root", return_value=root):
				research_assets.get_research_asset_registry.cache_clear()
				summary = research_assets.get_research_asset_summary()

		self.assertTrue(summary["available"])
		self.assertEqual(summary["model_count"], 2)
		self.assertEqual(summary["notebook_count"], 1)
		self.assertEqual(summary["registered_model_names"], ["job_matcher_model.pth", "parser_model.pth"])
		self.assertEqual(summary["registered_notebook_names"], ["Parser.ipynb"])

	def test_health_exposes_research_asset_summary_without_changing_runtime_contract(self) -> None:
		with patch("backend.main.warmup_semantic_encoder"), \
			 patch("backend.main.warmup_research_asset_registry"), \
			 patch("backend.main.get_settings", return_value=SimpleNamespace(groq=None)), \
			 patch("backend.main.get_semantic_backend_status", return_value={"ready": True}), \
			 patch("backend.main.get_research_asset_summary", return_value={
				"available": True,
				"mode": "optional_registry",
				"status": "standby",
				"model_count": 3,
				"notebook_count": 3,
				"registered_model_names": ["job_matcher_model.pth", "parser_model.pth", "t5_model.pth"],
				"registered_notebook_names": ["Job_Matcher.ipynb", "Parser.ipynb", "T5_Question_Generation_FineTuning (2).ipynb"],
			}):
			app = create_app()
			with TestClient(app) as client:
				response = client.get("/health")

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(payload["status"], "ok")
		self.assertIn("semantic_matching", payload)
		self.assertIn("groq", payload)
		self.assertIn("research_assets", payload)
		self.assertEqual(payload["research_assets"]["model_count"], 3)
		self.assertEqual(payload["research_assets"]["registered_model_names"][2], "t5_model.pth")