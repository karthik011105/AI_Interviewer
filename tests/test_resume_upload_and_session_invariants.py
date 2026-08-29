from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from backend import config
from backend.api.auth import AuthenticatedUser
from backend.api import routes_resume
from backend.assessment.question_bank import build_assessment_batch, normalize_assessment_role_key
from backend.database.db_errors import DatabaseClientError
from backend.database.mongo_client import MongoRepository
from backend.nlp.resume_parser import ResumeParseResult


class ResumeUploadValidationTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config.reset_settings()

    def tearDown(self) -> None:
        config.reset_settings()

    async def test_rejects_invalid_pdf_signature(self) -> None:
        upload = UploadFile(
            file=BytesIO(b"not-a-pdf"),
            filename="resume.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        )

        with self.assertRaises(HTTPException) as context:
            await routes_resume._persist_validated_upload_to_temp_pdf(upload)

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.detail, "The uploaded file is not a valid PDF.")
        await upload.close()

    async def test_rejects_oversized_pdf_before_parsing(self) -> None:
        upload = UploadFile(
            file=BytesIO(b"%PDF-" + (b"a" * 1100)),
            filename="resume.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        )

        with patch.dict(os.environ, {"RESUME_MAX_UPLOAD_BYTES": "1024"}, clear=False):
            config.reset_settings()
            with self.assertRaises(HTTPException) as context:
                await routes_resume._persist_validated_upload_to_temp_pdf(upload)

        self.assertEqual(context.exception.status_code, 413)
        self.assertIn("maximum allowed size", context.exception.detail)
        await upload.close()

    async def test_accepts_valid_pdf_within_limit(self) -> None:
        upload = UploadFile(
            file=BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"),
            filename="resume.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        )

        with patch.dict(os.environ, {"RESUME_MAX_UPLOAD_BYTES": "2048"}, clear=False):
            config.reset_settings()
            temp_path = await routes_resume._persist_validated_upload_to_temp_pdf(upload)

        self.assertIsInstance(temp_path, Path)
        self.assertTrue(temp_path.exists())
        self.assertTrue(temp_path.read_bytes().startswith(b"%PDF-"))
        temp_path.unlink(missing_ok=True)
        await upload.close()


class SessionOwnerInvariantTests(TestCase):
    def test_repository_rejects_missing_user_id(self) -> None:
        repository = object.__new__(MongoRepository)

        with self.assertRaises(DatabaseClientError) as context:
            MongoRepository.create_session(repository, user_id="", role_selected=None)

        self.assertEqual(
            str(context.exception),
            "sessions.user_id is required when creating a session.",
        )

    def test_repository_normalizes_user_id_before_insert(self) -> None:
        repository = object.__new__(MongoRepository)
        repository.insert_one = MagicMock(return_value={"id": "session-1"})

        result = MongoRepository.create_session(
            repository,
            user_id=" user-123 ",
            role_selected="machine_learning_engineer",
        )

        self.assertEqual(result, {"id": "session-1"})
        repository.insert_one.assert_called_once()
        _, payload = repository.insert_one.call_args.args
        self.assertEqual(payload["user_id"], "user-123")
        self.assertEqual(payload["role_selected"], "machine_learning_engineer")


class InterviewContextPersistenceTests(TestCase):
    def test_parse_resume_request_treats_context_persistence_failure_as_non_fatal(self) -> None:
        current_user = AuthenticatedUser(
            user_id="user-123",
            email="user@example.com",
            raw_user={"id": "user-123", "email": "user@example.com"},
        )
        request = routes_resume.ResumeParseRequest(
            pdf_path=str(Path(__file__)),
            session_id="session-123",
            persist_resume=True,
            run_role_matching=False,
            persist_interview_contexts=True,
        )
        parse_result = ResumeParseResult(
            session_id="session-123",
            raw_text="Python FastAPI project",
            parsed_resume={"name": "Test User", "skills": ["Python"]},
        )
        mock_parser = MagicMock()
        mock_parser.parse_and_store_resume.return_value = parse_result

        with patch("backend.api.routes_resume._resolve_session", return_value=("session-123", False)), \
             patch("backend.api.routes_resume.ResumeParser", return_value=mock_parser), \
             patch("backend.api.routes_resume.build_interview_round_contexts", return_value={"technical": {"skill_gaps": []}}), \
             patch("backend.api.routes_resume.save_interview_contexts", side_effect=DatabaseClientError("wrapped failure")), \
             patch("backend.api.routes_resume.score_resume_as_dict", return_value={"overall_score": 0.5}), \
             patch("backend.api.routes_resume._LOGGER") as mock_logger:
            response = routes_resume._parse_resume_request(
                request,
                Path(__file__),
                current_user=current_user,
            )

        self.assertEqual(response["session_id"], "session-123")
        self.assertTrue(response["persisted_resume"])
        self.assertFalse(response["persisted_interview_contexts"])
        self.assertEqual(response["interview_contexts"], {"technical": {"skill_gaps": []}})
        mock_logger.warning.assert_called_once()


class AssessmentBatchTests(TestCase):
    def test_build_batch_honors_requested_total_questions(self) -> None:
        batch = build_assessment_batch(
            session_id="session-123",
            role_key="backend_python_developer",
            total_questions=4,
        )

        self.assertEqual(batch.total_questions, 4)
        self.assertEqual(len(batch.questions), 4)
        self.assertEqual(batch.role_key, "backend_python_developer")
        self.assertEqual(len({question.question_id for question in batch.questions}), 4)

    def test_normalize_assessment_role_key_maps_generated_adjacent_roles(self) -> None:
        self.assertEqual(
            normalize_assessment_role_key("cloud_data_engineer"),
            "data_engineer",
        )
        self.assertEqual(
            normalize_assessment_role_key("business_intelligence_developer"),
            "business_intelligence_analyst",
        )
        self.assertEqual(
            normalize_assessment_role_key("real_time_analytics_engineer"),
            "data_engineer",
        )
        # AI / intelligent-systems / NLP / CV Groq-generated variants
        self.assertEqual(
            normalize_assessment_role_key("intelligent_systems_developer"),
            "machine_learning_engineer",
        )
        self.assertEqual(
            normalize_assessment_role_key("nlp_engineer"),
            "machine_learning_engineer",
        )
        self.assertEqual(
            normalize_assessment_role_key("computer_vision_engineer"),
            "machine_learning_engineer",
        )
        self.assertEqual(
            normalize_assessment_role_key("llm_engineer"),
            "ai_engineer",
        )
        self.assertEqual(
            normalize_assessment_role_key("generative_ai_engineer"),
            "ai_engineer",
        )

    def test_build_batch_accepts_generated_adjacent_role_variants(self) -> None:
        batch = build_assessment_batch(
            session_id="session-generated-role",
            role_key="real_time_analytics_engineer",
            total_questions=4,
        )

        self.assertEqual(batch.role_key, "data_engineer")
        self.assertEqual(batch.total_questions, 4)
        self.assertEqual(len(batch.questions), 4)
