"""MongoDB client helpers for persistent application state."""

from __future__ import annotations

import uuid
from typing import Any, Mapping, Sequence

import pymongo
from pymongo.collection import ReturnDocument

from backend.config import MongoSettings

from .db_errors import (
	ConcurrentUpdateError,
	DatabaseClientError,
	DatabaseConfigurationError,
	DatabaseDependencyError,
	DSAStage,
	JudgeStatus,
	RecordNotFoundError,
	_utcnow_iso,
	build_default_candidate_model,
	build_default_dsa_state,
)


class MongoRepository:
	"""A repository providing persistence via MongoDB."""

	def __init__(self, settings: MongoSettings) -> None:
		self.settings = settings
		try:
			self.client = pymongo.MongoClient(
				self.settings.uri,
				serverSelectionTimeoutMS=5000,
				uuidRepresentation="standard"
			)
			self.db = self.client[self.settings.database_name]
			self._ensure_indexes()
		except Exception as exc:
			raise DatabaseDependencyError(
				f"Could not connect to MongoDB at {self.settings.uri}: {exc}"
			) from exc

	def _ensure_indexes(self) -> None:
		"""Create necessary indexes if they don't exist."""
		self.db.users.create_index("email", unique=True)
		self.db.sessions.create_index("user_id")
		self.db.resume_data.create_index("session_id", unique=True)
		self.db.role_matches.create_index("session_id")
		self.db.interview_round_contexts.create_index(
			[("session_id", 1), ("round", 1)], unique=True
		)
		self.db.interview_responses.create_index(
			[("session_id", 1), ("round", 1)]
		)
		self.db.assessment_sessions.create_index("session_id", unique=True)
		self.db.dsa_sessions.create_index(
			[("session_id", 1), ("question_number", 1)], unique=True
		)
		self.db.dsa_sessions.create_index("session_id")
		self.db.final_reports.create_index("session_id", unique=True)
		# Revoked access tokens, keyed by the JWT's jti claim. expires_at carries
		# the token's own exp, and the TTL index below lets MongoDB drop each
		# entry once the token would have expired anyway — so the collection
		# stays bounded without a cleanup job. expireAfterSeconds=0 means "expire
		# at the time stored in this field".
		self.db.revoked_tokens.create_index("jti", unique=True)
		self.db.revoked_tokens.create_index("expires_at", expireAfterSeconds=0)
		self.db.interview_round_sessions.create_index(
			[("session_id", 1), ("round", 1)], unique=True
		)

	@staticmethod
	def _map_id(doc: dict[str, Any] | None) -> dict[str, Any] | None:
		if not doc:
			return None
		# Map MongoDB _id to standard string id if not already present
		if "_id" in doc:
			doc["id"] = str(doc["_id"])
			del doc["_id"]
		return doc

	@staticmethod
	def _map_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
		mapped = dict(filters)
		if "id" in mapped:
			mapped["_id"] = mapped.pop("id")
		return mapped

	@staticmethod
	def _generate_id() -> str:
		return str(uuid.uuid4())

	def fetch_one(
		self,
		collection_name: str,
		*,
		filters: Mapping[str, Any],
	) -> dict[str, Any] | None:
		"""Fetch a single record by matching filters."""
		doc = self.db[collection_name].find_one(self._map_filters(filters))
		return self._map_id(doc)

	def insert_one(self, collection_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
		"""Insert a single record."""
		doc = dict(payload)
		if "id" in doc:
			doc["_id"] = doc.pop("id")
		else:
			doc["_id"] = self._generate_id()

		self.db[collection_name].insert_one(doc)
		return self._map_id(doc)

	def insert_many(
		self,
		collection_name: str,
		payloads: Sequence[Mapping[str, Any]],
	) -> list[dict[str, Any]]:
		"""Insert multiple records."""
		docs = []
		for p in payloads:
			doc = dict(p)
			if "id" in doc:
				doc["_id"] = doc.pop("id")
			else:
				doc["_id"] = self._generate_id()
			docs.append(doc)
			
		if not docs:
			return []
			
		self.db[collection_name].insert_many(docs)
		return [self._map_id(doc) for doc in docs]

	def update_one(
		self,
		collection_name: str,
		*,
		values: Mapping[str, Any],
		filters: Mapping[str, Any],
		not_found_message: str | None = None,
	) -> dict[str, Any]:
		"""Update a single record by filters."""
		doc = self.db[collection_name].find_one_and_update(
			self._map_filters(filters),
			{"$set": dict(values)},
			return_document=ReturnDocument.AFTER
		)
		if doc is None:
			raise RecordNotFoundError(
				not_found_message or f"No record matched filters for '{collection_name}'."
			)
		return self._map_id(doc)

	def delete_many(
		self,
		collection_name: str,
		*,
		filters: Mapping[str, Any],
	) -> int:
		"""Delete matching records."""
		result = self.db[collection_name].delete_many(self._map_filters(filters))
		return result.deleted_count

	def revoke_token(self, *, jti: str, user_id: str, expires_at: Any) -> None:
		"""Record an access token as revoked.

		Idempotent: revoking an already-revoked token is a no-op rather than an
		error, so a client retrying a logout does not get a 500.
		"""
		self.db.revoked_tokens.update_one(
			{"jti": jti},
			{
				"$set": {
					"jti": jti,
					"user_id": user_id,
					"expires_at": expires_at,
					"revoked_at": _utcnow_iso(),
				}
			},
			upsert=True,
		)

	def is_token_revoked(self, *, jti: str) -> bool:
		"""Return True when this token has been revoked.

		MongoDB's TTL monitor only runs about once a minute, so an entry can
		outlive its expires_at briefly. That direction is harmless — a token
		past its own exp is already rejected by signature verification.
		"""
		return self.db.revoked_tokens.find_one({"jti": jti}, {"_id": 1}) is not None

	def create_session(
		self,
		*,
		user_id: str,
		role_selected: str | None = None,
		status: str = "created",
		created_at: str | None = None,
	) -> dict[str, Any]:
		normalized_user_id = str(user_id or "").strip()
		if not normalized_user_id:
			raise DatabaseClientError(
				"sessions.user_id is required when creating a session."
			)

		payload = {
			"user_id": normalized_user_id,
			"role_selected": role_selected,
			"status": status,
			"created_at": created_at or _utcnow_iso(),
		}
		return self.insert_one("sessions", payload)

	def get_session(self, session_id: str) -> dict[str, Any] | None:
		return self.fetch_one("sessions", filters={"id": session_id})

	def update_session_status(
		self,
		*,
		session_id: str,
		status: str,
	) -> dict[str, Any]:
		return self.update_one(
			"sessions",
			values={"status": status},
			filters={"id": session_id},
			not_found_message="The supplied session_id does not exist.",
		)

	def update_session_role_selected(
		self,
		*,
		session_id: str,
		role_selected: str,
	) -> dict[str, Any]:
		"""Persist the user's chosen interview role on the session record."""
		return self.update_one(
			"sessions",
			values={"role_selected": role_selected},
			filters={"id": session_id},
			not_found_message="The supplied session_id does not exist.",
		)

	def save_resume_data(
		self,
		*,
		session_id: str,
		raw_text: str,
		parsed_json: Mapping[str, Any],
		created_at: str | None = None,
	) -> dict[str, Any]:
		payload = {
			"session_id": session_id,
			"raw_text": raw_text,
			"parsed_json": dict(parsed_json),
			"created_at": created_at or _utcnow_iso(),
		}
		# Use update_one with upsert-like behavior since session_id is unique
		doc = self.db["resume_data"].find_one_and_update(
			{"session_id": session_id},
			{"$set": payload},
			upsert=True,
			return_document=ReturnDocument.AFTER
		)
		return self._map_id(doc)

	def save_interview_contexts(
		self,
		*,
		session_id: str,
		contexts: Mapping[str, Any],
		created_at: str | None = None,
	) -> list[dict[str, Any]]:
		"""Upsert per-round interview contexts (hr, technical, project_discussion)."""
		ts = created_at or _utcnow_iso()
		results = []
		for round_key, context in contexts.items():
			if isinstance(context, dict):
				payload = {
					"session_id": session_id,
					"round": round_key,
					"context_json": dict(context),
					"created_at": ts,
				}
				doc = self.db["interview_round_contexts"].find_one_and_update(
					{"session_id": session_id, "round": round_key},
					{"$set": payload},
					upsert=True,
					return_document=ReturnDocument.AFTER
				)
				results.append(self._map_id(doc))
		return results

	def get_interview_context(
		self,
		*,
		session_id: str,
		round: str,
	) -> dict[str, Any] | None:
		"""Fetch the stored context dict for one interview round."""
		row = self.fetch_one(
			"interview_round_contexts",
			filters={"session_id": session_id, "round": round},
		)
		if row is None:
			return None
		return row.get("context_json")

	def get_all_interview_contexts(
		self,
		*,
		session_id: str,
	) -> dict[str, Any]:
		"""Fetch all stored round contexts for a session keyed by round name."""
		cursor = self.db["interview_round_contexts"].find({"session_id": session_id})
		return {doc["round"]: doc["context_json"] for doc in cursor if "round" in doc}

	@staticmethod
	def _normalize_interview_response_row(row: Mapping[str, Any]) -> dict[str, Any]:
		normalized = dict(row)
		dimension_scores = dict(normalized.get("dimension_scores") or {})
		normalized.setdefault("final_score", normalized.get("score", 0.0))
		normalized.setdefault("groq_score", dimension_scores.get("groq", normalized.get("score", 0.0)))
		normalized.setdefault("sbert_score", dimension_scores.get("sbert", normalized.get("sbert_similarity", 0.0)))
		normalized.setdefault("communication_score", dimension_scores.get("communication", 0.0))
		normalized.setdefault("evaluation_mode", dimension_scores.get("evaluation_mode"))
		normalized.setdefault("fallback_reason", dimension_scores.get("fallback_reason"))
		normalized.setdefault("local_score", dimension_scores.get("local_score", normalized.get("score", 0.0)))
		if "concept_coverage" not in normalized and isinstance(dimension_scores.get("concept_coverage"), Mapping):
			normalized["concept_coverage"] = dict(dimension_scores["concept_coverage"])
		if "scoring_profile" not in normalized and isinstance(dimension_scores.get("scoring_profile"), Mapping):
			normalized["scoring_profile"] = dict(dimension_scores["scoring_profile"])
		if "rubric" not in normalized and isinstance(dimension_scores.get("rubric"), Mapping):
			normalized["rubric"] = dict(dimension_scores["rubric"])
		if "communication" not in normalized and isinstance(dimension_scores.get("communication_detail"), Mapping):
			normalized["communication"] = dict(dimension_scores["communication_detail"])
		return normalized

	def save_interview_response(self, payload: Mapping[str, Any]) -> dict[str, Any]:
		row = self.insert_one("interview_responses", payload)
		return self._normalize_interview_response_row(row)

	def list_interview_responses(
		self,
		*,
		session_id: str,
		round: str,
	) -> list[dict[str, Any]]:
		cursor = self.db["interview_responses"].find(
			{"session_id": session_id, "round": round}
		).sort("created_at", 1)
		
		data = [self._map_id(doc) for doc in cursor]
		
		return [
			self._normalize_interview_response_row(row)
			for row in data
			if isinstance(row, Mapping)
		]

	def save_final_report(self, payload: Mapping[str, Any]) -> dict[str, Any]:
		final_payload = dict(payload)
		timestamp = final_payload.get("created_at") or _utcnow_iso()
		final_payload.setdefault("created_at", timestamp)
		final_payload.setdefault("updated_at", timestamp)
		return self.insert_one("final_reports", final_payload)

	def get_final_report(self, *, session_id: str) -> dict[str, Any] | None:
		return self.fetch_one("final_reports", filters={"session_id": session_id})

	def upsert_final_report(
		self,
		*,
		session_id: str,
		overall_score: float | int | None = None,
		round_scores: Mapping[str, Any] | None = None,
		dimension_scores: Mapping[str, Any] | None = None,
		report_json: Mapping[str, Any] | None = None,
		created_at: str | None = None,
	) -> dict[str, Any]:
		existing = self.get_final_report(session_id=session_id)
		timestamp = _utcnow_iso()
		payload = {
			"session_id": session_id,
			"overall_score": overall_score,
			"round_scores": dict(round_scores or {}),
			"dimension_scores": dict(dimension_scores or {}),
			"report_json": dict(report_json or {}),
			"created_at": created_at or (existing.get("created_at") if isinstance(existing, Mapping) else None) or timestamp,
			"updated_at": timestamp,
		}
		
		doc = self.db["final_reports"].find_one_and_update(
			{"session_id": session_id},
			{"$set": payload},
			upsert=True,
			return_document=ReturnDocument.AFTER
		)
		return self._map_id(doc)

	def create_assessment_session(
		self,
		*,
		session_id: str,
		role_key: str,
		total_questions: int,
		batch_json: Mapping[str, Any],
		state_json: Mapping[str, Any],
		status: str = "in_progress",
		answered_count: int = 0,
		correct_count: int = 0,
		score_percent: float = 0.0,
		started_at: str | None = None,
		completed_at: str | None = None,
	) -> dict[str, Any]:
		"""Create or replace the persisted assessment state for one interview session."""
		timestamp = _utcnow_iso()
		payload = {
			"session_id": session_id,
			"role_key": role_key,
			"status": status,
			"total_questions": total_questions,
			"answered_count": answered_count,
			"correct_count": correct_count,
			"score_percent": score_percent,
			"batch_json": dict(batch_json),
			"state_json": dict(state_json),
			"started_at": started_at or timestamp,
			"completed_at": completed_at,
			"updated_at": timestamp,
		}
		doc = self.db["assessment_sessions"].find_one_and_update(
			{"session_id": session_id},
			{"$set": payload},
			upsert=True,
			return_document=ReturnDocument.AFTER
		)
		return self._map_id(doc)

	def get_assessment_session(self, *, session_id: str) -> dict[str, Any] | None:
		return self.fetch_one("assessment_sessions", filters={"session_id": session_id})

	def delete_assessment_session(self, *, session_id: str) -> int:
		return self.delete_many("assessment_sessions", filters={"session_id": session_id})

	def persist_assessment_session(
		self,
		*,
		session_id: str,
		state_json: Mapping[str, Any],
		status: str,
		answered_count: int,
		correct_count: int,
		score_percent: float,
		completed_at: str | None = None,
	) -> dict[str, Any]:
		"""Persist updated assessment progress for an existing assessment session."""
		return self.update_one(
			"assessment_sessions",
			values={
				"state_json": dict(state_json),
				"status": status,
				"answered_count": answered_count,
				"correct_count": correct_count,
				"score_percent": score_percent,
				"completed_at": completed_at,
				"updated_at": _utcnow_iso(),
			},
			filters={"session_id": session_id},
			not_found_message="Assessment session not found for the supplied session_id.",
		)

	def create_dsa_session(
		self,
		*,
		session_id: str,
		problem_id: str,
		question_number: int,
		stage: DSAStage | str = DSAStage.PROBLEM_SETUP,
		state_json: Mapping[str, Any] | None = None,
		deadline_at: str | None = None,
		approach_text: str | None = None,
		current_code_draft: str | None = None,
	) -> dict[str, Any]:
		state = dict(
			state_json
			or build_default_dsa_state(stage=stage, deadline_at=deadline_at)
		)
		stage_value = state.get("stage", str(stage))
		payload = {
			"session_id": session_id,
			"problem_id": problem_id,
			"question_number": question_number,
			"stage": stage_value,
			"state_version": int(state.get("state_version", 1)),
			"state_json": state,
			"stage_started_at": state.get("stage_started_at") or _utcnow_iso(),
			"deadline_at": deadline_at or state.get("deadline_at"),
			"approach_text": approach_text,
			"current_code_draft": current_code_draft,
			"all_code_submissions": [],
			"last_submission_id": state.get("last_submission_id"),
			"last_judge_status": state.get(
				"last_judge_status",
				JudgeStatus.NOT_RUN.value,
			),
			"updated_at": _utcnow_iso(),
		}
		return self.insert_one("dsa_sessions", payload)

	def get_dsa_session(
		self,
		*,
		session_id: str,
		question_number: int,
	) -> dict[str, Any] | None:
		return self.fetch_one(
			"dsa_sessions",
			filters={
				"session_id": session_id,
				"question_number": question_number,
			},
		)

	def list_dsa_sessions(self, *, session_id: str) -> list[dict[str, Any]]:
		cursor = self.db["dsa_sessions"].find(
			{"session_id": session_id}
		).sort("question_number", 1)
		return [self._map_id(doc) for doc in cursor]

	def delete_dsa_sessions(self, *, session_id: str) -> int:
		return self.delete_many(
			"dsa_sessions",
			filters={"session_id": session_id},
		)

	def require_dsa_session(self, *, session_id: str, question_number: int) -> dict[str, Any]:
		record = self.get_dsa_session(
			session_id=session_id,
			question_number=question_number,
		)
		if record is None:
			raise RecordNotFoundError(
				"DSA session not found for the supplied session_id and question_number."
			)
		return record

	def persist_dsa_state(
		self,
		*,
		session_id: str,
		question_number: int,
		state_json: Mapping[str, Any],
		expected_state_version: int | None = None,
		stage: DSAStage | str | None = None,
		approach_text: str | None = None,
	) -> dict[str, Any]:
		"""Persist the canonical DSA state with optimistic concurrency."""
		state = dict(state_json)
		current_version = int(
			expected_state_version
			if expected_state_version is not None
			else state.get("state_version", 0)
		)
		next_version = current_version + 1
		stage_value = str(stage or state.get("stage", DSAStage.PROBLEM_SETUP.value))
		state["stage"] = stage_value
		state["state_version"] = next_version

		payload = {
			"stage": stage_value,
			"state_version": next_version,
			"state_json": state,
			"stage_started_at": state.get("stage_started_at") or _utcnow_iso(),
			"deadline_at": state.get("deadline_at"),
			"approach_text": approach_text,
			"last_submission_id": state.get("last_submission_id"),
			"last_judge_status": state.get("last_judge_status"),
			"updated_at": _utcnow_iso(),
		}

		filters = {"session_id": session_id, "question_number": question_number}
		if expected_state_version is not None:
			filters["state_version"] = expected_state_version
			
		doc = self.db["dsa_sessions"].find_one_and_update(
			filters,
			{"$set": payload},
			return_document=ReturnDocument.AFTER
		)
		
		if doc is None and expected_state_version is not None:
			raise ConcurrentUpdateError(
				"DSA session state update rejected because the state_version is stale."
			)
		if doc is None:
			raise RecordNotFoundError(
				"DSA session not found while attempting to persist state."
			)
		return self._map_id(doc)

	def append_dsa_submission(
		self,
		*,
		session_id: str,
		question_number: int,
		submission: Mapping[str, Any],
		current_code_draft: str | None = None,
		last_submission_id: str | None = None,
		last_judge_status: str | None = None,
		execution_results: Mapping[str, Any] | None = None,
		state_json: Mapping[str, Any] | None = None,
	) -> dict[str, Any]:
		"""Append a code submission and advance the DSA state version."""
		record = self.require_dsa_session(
			session_id=session_id,
			question_number=question_number,
		)
		current_version = int(record.get("state_version", 0))
		state = dict(state_json) if state_json is not None else dict(record.get("state_json") or {})
		state["last_submission_id"] = last_submission_id
		state["last_judge_status"] = (
			last_judge_status or state.get("last_judge_status") or JudgeStatus.NOT_RUN.value
		)
		state["awaiting_user_input"] = True

		submissions = list(record.get("all_code_submissions") or [])
		submissions.append(dict(submission))
		next_version = current_version + 1
		state["state_version"] = next_version

		payload = {
			"all_code_submissions": submissions,
			"current_code_draft": current_code_draft,
			"last_submission_id": last_submission_id,
			"last_judge_status": state["last_judge_status"],
			"execution_results": dict(execution_results) if execution_results else None,
			"state_version": next_version,
			"state_json": state,
			"updated_at": _utcnow_iso(),
		}

		doc = self.db["dsa_sessions"].find_one_and_update(
			{
				"session_id": session_id,
				"question_number": question_number,
				"state_version": current_version,
			},
			{"$set": payload},
			return_document=ReturnDocument.AFTER
		)
		
		if doc is None:
			raise ConcurrentUpdateError(
				"DSA submission append failed because the session changed concurrently."
			)
		return self._map_id(doc)

	def complete_dsa_session(
		self,
		*,
		session_id: str,
		question_number: int,
		final_code: str | None,
		execution_results: Mapping[str, Any] | None,
		dimension_scores: Mapping[str, Any] | None,
		total_score: float | int | None,
		completed_at: str | None = None,
	) -> dict[str, Any]:
		"""Mark a DSA session complete and persist the final scoring payload."""
		record = self.require_dsa_session(
			session_id=session_id,
			question_number=question_number,
		)
		current_version = int(record.get("state_version", 0))
		state = dict(record.get("state_json") or {})
		state["stage"] = DSAStage.COMPLETE.value
		state["awaiting_user_input"] = False
		state["state_version"] = current_version + 1

		payload = {
			"stage": DSAStage.COMPLETE.value,
			"state_version": current_version + 1,
			"state_json": state,
			"final_code": final_code,
			"execution_results": dict(execution_results) if execution_results else None,
			"dimension_scores": dict(dimension_scores) if dimension_scores else None,
			"total_score": total_score,
			"completed_at": completed_at or _utcnow_iso(),
			"updated_at": _utcnow_iso(),
		}

		doc = self.db["dsa_sessions"].find_one_and_update(
			{
				"session_id": session_id,
				"question_number": question_number,
				"state_version": current_version,
			},
			{"$set": payload},
			return_document=ReturnDocument.AFTER
		)
		if doc is None:
			raise ConcurrentUpdateError(
				"DSA session completion failed because the session changed concurrently."
			)
		return self._map_id(doc)

	def create_interview_round_session(
		self,
		*,
		session_id: str,
		round: str,
		role_key: str,
		questions_json: Mapping[str, Any] | None = None,
		started_at: str | None = None,
		deadline_at: str | None = None,
	) -> dict[str, Any]:
		"""Upsert a new interview round session (hr / technical / project_discussion)."""
		timestamp = _utcnow_iso()
		payload = {
			"session_id": session_id,
			"round": round,
			"role_key": role_key,
			"status": "in_progress",
			"questions_json": dict(questions_json) if questions_json is not None else None,
			"current_question_index": 0,
			"difficulty_signal": 0.5,
			"total_score": None,
			"started_at": started_at or timestamp,
			"deadline_at": deadline_at,
			"state_version": 1,
			"created_at": timestamp,
		}
		
		doc = self.db["interview_round_sessions"].find_one_and_update(
			{"session_id": session_id, "round": round},
			{"$set": payload},
			upsert=True,
			return_document=ReturnDocument.AFTER
		)
		return self._map_id(doc)

	def get_interview_round_session(
		self,
		*,
		session_id: str,
		round: str,
	) -> dict[str, Any] | None:
		"""Fetch a single interview round session record by session_id and round."""
		return self.fetch_one(
			"interview_round_sessions",
			filters={"session_id": session_id, "round": round},
		)

	def delete_interview_round_session(
		self,
		*,
		session_id: str,
		round: str,
	) -> int:
		return self.delete_many(
			"interview_round_sessions",
			filters={"session_id": session_id, "round": round},
		)

	def delete_interview_responses(
		self,
		*,
		session_id: str,
		round: str,
	) -> int:
		return self.delete_many(
			"interview_responses",
			filters={"session_id": session_id, "round": round},
		)

	def delete_final_report(self, *, session_id: str) -> int:
		return self.delete_many(
			"final_reports",
			filters={"session_id": session_id},
		)

	def advance_interview_question(
		self,
		*,
		session_id: str,
		round: str,
		new_index: int,
		difficulty_signal: float,
		questions_json: Mapping[str, Any] | None,
		expected_state_version: int,
	) -> dict[str, Any]:
		"""Advance the current question index with optimistic concurrency."""
		next_version = expected_state_version + 1
		values = {
			"current_question_index": new_index,
			"difficulty_signal": difficulty_signal,
			"state_version": next_version,
		}
		if questions_json is not None:
			values["questions_json"] = dict(questions_json)
			
		doc = self.db["interview_round_sessions"].find_one_and_update(
			{
				"session_id": session_id,
				"round": round,
				"state_version": expected_state_version,
			},
			{"$set": values},
			return_document=ReturnDocument.AFTER
		)
		if doc is None:
			raise ConcurrentUpdateError(
				"Interview round question advance rejected — state_version is stale."
			)
		return self._map_id(doc)

	def complete_interview_round(
		self,
		*,
		session_id: str,
		round: str,
		total_score: float,
		completed_at: str | None = None,
	) -> dict[str, Any]:
		"""Mark the round complete and persist the final aggregated score."""
		return self.update_one(
			"interview_round_sessions",
			values={
				"status": "complete",
				"total_score": total_score,
				"completed_at": completed_at or _utcnow_iso(),
			},
			filters={"session_id": session_id, "round": round},
			not_found_message=(
				"Interview round session not found for the supplied session_id and round."
			),
		)

_repository: MongoRepository | None = None

def get_repository() -> MongoRepository:
	"""Return a process-wide repository instance for backend use."""
	global _repository
	if _repository is None:
		from backend.config import get_settings
		_repository = MongoRepository(get_settings().mongo)
	return _repository

def reset_repository() -> None:
	"""Clear the cached repository instance."""
	global _repository
	_repository = None

