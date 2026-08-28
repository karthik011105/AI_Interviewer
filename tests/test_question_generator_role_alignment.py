from __future__ import annotations

from unittest import TestCase

from backend.nlp.question_generator import _resolve_role_topics


class TechnicalQuestionRoleAlignmentTests(TestCase):
	def test_vlsi_role_uses_hardware_topics_not_uiux_fallback(self) -> None:
		topics = _resolve_role_topics(
			"vlsi_design_engineer",
			"VLSI Design Engineer",
			["Verilog"],
			["FPGA"],
		)

		self.assertIn("Digital logic design and Boolean reasoning", topics)
		self.assertIn("Verification, testbenches, and waveform-based debugging", topics)
		self.assertNotIn("User-centred design process", topics)
		self.assertNotIn("Data structures and algorithmic reasoning", topics)

	def test_site_reliability_role_avoids_generic_dsa_padding(self) -> None:
		topics = _resolve_role_topics(
			"site_reliability_engineer",
			"Site Reliability Engineer",
			["Linux", "Monitoring"],
			["Kubernetes"],
		)

		self.assertIn("Service level indicators and objectives (SLI / SLO / SLA)", topics)
		self.assertIn("Monitoring, alerting, and observability fundamentals", topics)
		self.assertNotIn("Data structures and algorithmic reasoning", topics)
		self.assertNotIn("Time and space complexity basics", topics)

	def test_firmware_role_uses_low_level_embedded_subjects(self) -> None:
		topics = _resolve_role_topics(
			"firmware_engineer",
			"Firmware Engineer",
			["C"],
			["UART"],
		)

		self.assertIn("Boot flow, startup code, and hardware bring-up", topics)
		self.assertIn("Interrupts, timers, and deterministic execution", topics)
		self.assertNotIn("API and client-server fundamentals", topics)