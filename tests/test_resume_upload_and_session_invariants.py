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


class MalformedPdfHandlingTests(TestCase):
    """The upload guard only inspects the first five bytes, so anything
    starting ``%PDF-`` reaches PyMuPDF.

    PyMuPDF raises its own ``FileDataError`` on a file it cannot parse, which is
    not in this module's hierarchy, so untranslated it escaped every handler in
    routes_resume.py and became an unhandled 500. A nine-byte body of
    ``b"%PDF-1.4\n"`` was enough to trigger it, and the original message
    embedded the absolute temp-file path.
    """

    def setUp(self) -> None:
        config.reset_settings()

    def tearDown(self) -> None:
        config.reset_settings()

    def _extract(self, body: bytes) -> str:
        from tempfile import NamedTemporaryFile

        from backend.nlp.resume_parser import ResumeParser

        with NamedTemporaryFile(delete=False, suffix=".pdf") as handle:
            handle.write(body)
            path = Path(handle.name)
        try:
            return ResumeParser().extract_text(path)
        finally:
            path.unlink(missing_ok=True)

    def test_an_unopenable_pdf_raises_a_domain_error(self) -> None:
        from backend.nlp.resume_parser import ResumeExtractionError

        bodies = {
            "header only": b"%PDF-1.4\n",
            "truncated object": b"%PDF-1.4\n1 0 obj<</Type/Catalog",
            "nulls after the header": b"%PDF-" + b"\x00" * 4096,
        }
        for label, body in bodies.items():
            with self.subTest(body=label):
                with self.assertRaises(ResumeExtractionError):
                    self._extract(body)

    def test_the_error_does_not_leak_the_temp_file_path(self) -> None:
        """PyMuPDF's own message contains the absolute path it tried to open.
        Surfacing that to the caller would disclose the server's filesystem
        layout, so the replacement message is not interpolated from it."""

        from backend.nlp.resume_parser import ResumeExtractionError

        with self.assertRaises(ResumeExtractionError) as context:
            self._extract(b"%PDF-1.4\n")

        message = str(context.exception)
        self.assertNotIn("Temp", message)
        self.assertNotIn(".pdf", message)
        self.assertNotIn("Failed to open file", message)

    def test_a_structurally_valid_pdf_with_no_text_is_still_rejected(self) -> None:
        """This one already worked, and is kept so the fix above cannot be
        'simplified' into swallowing the low-text check as well."""

        from backend.nlp.resume_parser import ResumeExtractionError

        empty_pages_pdf = (
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n"
        )
        with self.assertRaises(ResumeExtractionError):
            self._extract(empty_pages_pdf)

    def test_the_repository_sample_resume_still_parses(self) -> None:
        """A happy-path anchor. Every other case here is a rejection, so
        without this the fix could reject everything and still pass."""

        from backend.nlp.resume_parser import ResumeParser

        sample = Path(__file__).resolve().parent.parent / "sample_resume.pdf"
        if not sample.is_file():
            self.skipTest("sample_resume.pdf is not present")

        text = ResumeParser().extract_text(sample)

        self.assertGreater(len(text), 200)


class UploadFormBoundsTests(TestCase):
    """``parse_resume_upload`` builds a ResumeParseRequest from loose Form
    values inside its own body.

    A pydantic ValidationError raised in a handler body is not the
    request-parsing error FastAPI converts into a 422 — it propagates as an
    unhandled exception. So although ResumeParseRequest declares
    ``Field(ge=1, le=10)`` for max_roles, sending 0 or 99999 produced a 500
    until the same bounds were declared on the Form parameter too.
    """

    def test_max_roles_is_bounded_on_the_form_parameter_itself(self) -> None:
        import inspect

        from fastapi import params

        signature = inspect.signature(routes_resume.parse_resume_upload)
        default = signature.parameters["max_roles"].default

        self.assertIsInstance(default, params.Form)
        # This pydantic version keeps the constraints as annotated-types
        # markers in `metadata` rather than as `.ge` / `.le` attributes, so
        # read them by class name instead of guessing at the accessor.
        bounds = {
            type(item).__name__: getattr(item, attr)
            for item in default.metadata
            for attr in ("ge", "le")
            if hasattr(item, attr)
        }
        self.assertEqual(bounds, {"Ge": 1, "Le": 10})
        self.assertEqual(default.default, 5)

    def test_the_model_keeps_its_own_bounds_as_a_backstop(self) -> None:
        """Callers that construct ResumeParseRequest directly, including
        /resume/parse when the local-path API is enabled, must still be
        constrained."""

        import pydantic

        with self.assertRaises(pydantic.ValidationError):
            routes_resume.ResumeParseRequest(pdf_path="x.pdf", max_roles=0)
        with self.assertRaises(pydantic.ValidationError):
            routes_resume.ResumeParseRequest(pdf_path="x.pdf", max_roles=11)

        accepted = routes_resume.ResumeParseRequest(pdf_path="x.pdf", max_roles=10)
        self.assertEqual(accepted.max_roles, 10)


class LocalPathApiGatingTests(TestCase):
    """``/resume/parse`` takes an arbitrary filesystem path with no containment
    check, so it must not exist unless explicitly enabled."""

    def tearDown(self) -> None:
        config.reset_settings()

    def _resume_route_paths(self, enabled: bool) -> set[str]:
        import importlib

        with patch.dict(
            os.environ,
            {"ENABLE_LOCAL_RESUME_PATH_API": "true" if enabled else "false"},
            clear=False,
        ):
            config.reset_settings()
            module = importlib.reload(routes_resume)
            try:
                return {
                    route.path
                    for route in module.router.routes
                    if hasattr(route, "path")
                }
            finally:
                # Leave the module in its default state for every other test.
                config.reset_settings()
                importlib.reload(module)

    def test_the_route_is_absent_by_default(self) -> None:
        self.assertNotIn("/resume/parse", self._resume_route_paths(enabled=False))

    def test_the_route_appears_only_when_explicitly_enabled(self) -> None:
        self.assertIn("/resume/parse", self._resume_route_paths(enabled=True))

    def test_the_setting_defaults_to_false(self) -> None:
        """A deployment that simply does not mention the flag must not get the
        arbitrary-path endpoint."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_LOCAL_RESUME_PATH_API", None)
            config.reset_settings()
            self.assertFalse(config.get_settings().allow_local_resume_path_api)
