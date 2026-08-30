"""FastAPI application entrypoint for the AI Interview Simulator."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.api.routes_assessment import router as assessment_router
from backend.api.routes_auth import router as auth_router
from backend.api.routes_dsa import router as dsa_router
from backend.api.routes_interview import router as interview_router
from backend.api.routes_report import router as report_router
from backend.api.routes_resume import router as resume_router
from backend.api.routes_workflow import router as workflow_router
from backend.api.routes_voice import router as voice_router
from backend.api.ws_interview import router as ws_interview_router
from backend.nlp.role_matcher import get_semantic_backend_status, warmup_semantic_encoder
from backend.research_assets import get_research_asset_summary, warmup_research_asset_registry
from backend.voice.stt import warmup_stt


@asynccontextmanager
async def _lifespan(_: FastAPI):
	# Load Whisper off the event loop so the first candidate to speak does not
	# pay the model load inside their turn. Startup continues if it fails.
	warmup_task = asyncio.create_task(asyncio.to_thread(warmup_stt))
	try:
		yield
	finally:
		warmup_task.cancel()
		with contextlib.suppress(asyncio.CancelledError, Exception):
			await warmup_task


def create_app() -> FastAPI:
	# Warm up before the event loop starts: importing sentence-transformers'
	# native extensions (pyarrow/datasets) while asyncio's loop is already
	# running triggers an intermittent access violation on Windows.
	warmup_semantic_encoder()
	warmup_research_asset_registry()

	app = FastAPI(title="AI Interview Simulator", version="0.1.0", lifespan=_lifespan)
	app.add_middleware(
		CORSMiddleware,
		allow_origins=[
			"http://127.0.0.1:5173",
			"http://localhost:5173",
		],
		allow_origin_regex=r"http://(127\.0\.0\.1|localhost):(517[0-9]|3000)",
		allow_credentials=True,
		allow_methods=["*"],
		allow_headers=["*"],
	)
	app.include_router(auth_router)
	app.include_router(assessment_router)
	app.include_router(dsa_router)
	app.include_router(interview_router)
	app.include_router(report_router)
	app.include_router(workflow_router)
	app.include_router(ws_interview_router)
	app.include_router(voice_router)
	app.include_router(resume_router)

	@app.get("/health")
	def health() -> dict[str, object]:
		settings = get_settings()
		groq_settings = settings.groq
		return {
			"status": "ok",
			"semantic_matching": get_semantic_backend_status(),
			"research_assets": get_research_asset_summary(),
			"groq": {
				"configured": groq_settings is not None,
				"model": groq_settings.resume_parser_model if groq_settings is not None else None,
				"max_retries": groq_settings.max_retries if groq_settings is not None else 0,
				"backoff_base_seconds": (
					groq_settings.backoff_base_seconds if groq_settings is not None else 0.0
				),
			},
		}

	return app


app = create_app()


__all__ = ["app", "create_app"]
