"""A dropped socket must not destroy an answer that is already being committed.

``_transcribe_captured_audio`` joins the buffered frames and immediately calls
``reset_audio_capture()``, so once it is running the audio is gone. The socket's
``finally`` called ``_cancel_turn_commit``, which cancelled unconditionally — so
a disconnect mid-commit lost the candidate's answer with nothing to retry from,
and a cancel landing between persisting the response and advancing the question
index left them re-asked a question that already had a recorded answer.

Cancelling is still correct *before* transcription begins, because nothing has
been consumed yet. These tests pin both halves of that distinction.
"""

from __future__ import annotations

import asyncio
from unittest import IsolatedAsyncioTestCase

from backend.api.interview_runtime import _cancel_turn_commit


class _FakeRuntime:
    """Only the two attributes _cancel_turn_commit touches."""

    def __init__(self) -> None:
        self.turn_commit_task: asyncio.Task | None = None
        self.turn_commit_started = False


class TurnCommitDurabilityTests(IsolatedAsyncioTestCase):
    async def test_a_started_commit_is_allowed_to_finish(self) -> None:
        """The case that was losing answers."""

        runtime = _FakeRuntime()
        committed: list[str] = []

        async def commit() -> None:
            await asyncio.sleep(0.05)
            committed.append("saved")

        runtime.turn_commit_task = asyncio.create_task(commit())
        runtime.turn_commit_started = True
        await asyncio.sleep(0)  # let it start

        await _cancel_turn_commit(runtime)

        self.assertEqual(committed, ["saved"])
        self.assertIsNone(runtime.turn_commit_task)

    async def test_a_commit_that_has_not_started_is_cancelled(self) -> None:
        """Before transcription nothing has been consumed, so cancelling is
        free — and it must stay that way, or every disconnect would wait out
        the grace period."""

        runtime = _FakeRuntime()
        committed: list[str] = []

        async def commit() -> None:
            await asyncio.sleep(5)
            committed.append("saved")

        runtime.turn_commit_task = asyncio.create_task(commit())
        runtime.turn_commit_started = False
        await asyncio.sleep(0)

        await _cancel_turn_commit(runtime)

        self.assertEqual(committed, [])
        self.assertIsNone(runtime.turn_commit_task)

    async def test_a_slow_started_commit_survives_the_drain_timeout(self) -> None:
        """If the commit outlasts the bound, teardown stops waiting but the
        shield keeps the task alive, so the answer still lands rather than being
        thrown away."""

        runtime = _FakeRuntime()
        committed: list[str] = []

        async def commit() -> None:
            await asyncio.sleep(0.2)
            committed.append("saved")

        runtime.turn_commit_task = asyncio.create_task(commit())
        runtime.turn_commit_started = True
        await asyncio.sleep(0)

        import backend.api.interview_runtime as runtime_module

        original = runtime_module._TURN_COMMIT_DRAIN_SEC
        runtime_module._TURN_COMMIT_DRAIN_SEC = 0.01
        try:
            await _cancel_turn_commit(runtime)
        finally:
            runtime_module._TURN_COMMIT_DRAIN_SEC = original

        self.assertEqual(committed, [], "drain should have given up waiting")
        task = [t for t in asyncio.all_tasks() if t.get_coro().__name__ == "commit"]
        self.assertTrue(task, "the shielded commit must still be running")
        await asyncio.sleep(0.25)
        self.assertEqual(committed, ["saved"], "it should finish in the background")

    async def test_a_failing_commit_does_not_break_teardown(self) -> None:
        """Teardown runs on the disconnect path; an exception there would mask
        whatever actually caused the disconnect."""

        runtime = _FakeRuntime()

        async def commit() -> None:
            raise RuntimeError("evaluation blew up")

        runtime.turn_commit_task = asyncio.create_task(commit())
        runtime.turn_commit_started = True
        await asyncio.sleep(0)

        await _cancel_turn_commit(runtime)  # must not raise

        self.assertIsNone(runtime.turn_commit_task)

    async def test_no_task_is_a_no_op(self) -> None:
        runtime = _FakeRuntime()
        await _cancel_turn_commit(runtime)
        self.assertIsNone(runtime.turn_commit_task)
