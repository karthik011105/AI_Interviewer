"""Import an additional DSA problem pack into the local bank directory.

Use this for licensed or user-authored problem sources. Do not import content
unless you have the right to redistribute and store the full problem text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from backend.dsa.problem_selector import (
	CertifiedProblemBankError,
	list_problem_bank_files,
	load_problem_banks_from_paths,
)


PROBLEM_BANK_DIR = PROJECT_ROOT / "backend" / "data" / "problems"


def _slugify_pack_name(value: str) -> str:
	parts: list[str] = []
	for character in str(value or "").strip().casefold():
		if character.isalnum():
			parts.append(character)
		elif character in {"-", "_", " ", "."}:
			parts.append("_")
	slug = "".join(parts).strip("_")
	while "__" in slug:
		slug = slug.replace("__", "_")
	if not slug:
		raise ValueError("Pack name must contain at least one alphanumeric character.")
	if not slug.endswith("_bank"):
		slug = f"{slug}_bank"
	return slug


def _load_json_file(path: Path) -> dict[str, object]:
	try:
		payload = json.loads(path.read_text(encoding="utf-8"))
	except FileNotFoundError as exc:
		raise FileNotFoundError(f"Input pack was not found: {path.as_posix()}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"Input pack contains invalid JSON: {path.as_posix()}") from exc
	if not isinstance(payload, dict):
		raise ValueError("Input pack root must be a JSON object.")
	return payload


def _validate_candidate_pack(payload: dict[str, object], *, output_path: Path) -> None:
	existing_paths = tuple(path for path in list_problem_bank_files(PROBLEM_BANK_DIR) if path.resolve() != output_path.resolve())
	with TemporaryDirectory() as temp_dir:
		temp_path = Path(temp_dir) / output_path.name
		temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
		load_problem_banks_from_paths((*existing_paths, temp_path))


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("input_path", help="Path to the JSON problem pack to import.")
	parser.add_argument(
		"--name",
		help="Optional output pack name. Defaults to the input file stem and always writes as *_bank.json.",
	)
	parser.add_argument(
		"--replace",
		action="store_true",
		help="Allow overwriting an existing output pack.",
	)
	parser.add_argument(
		"--validate-only",
		action="store_true",
		help="Validate the pack against the current bank files without writing it.",
	)
	args = parser.parse_args(argv)

	input_path = Path(args.input_path).expanduser().resolve()
	payload = _load_json_file(input_path)

	try:
		output_stem = _slugify_pack_name(args.name or input_path.stem)
	except ValueError as exc:
		print(str(exc), file=sys.stderr)
		return 1

	output_path = PROBLEM_BANK_DIR / f"{output_stem}.json"
	if output_path.exists() and not args.replace and not args.validate_only:
		print(
			f"Output pack already exists at {output_path.as_posix()}. Use --replace to overwrite it.",
			file=sys.stderr,
		)
		return 1

	try:
		_validate_candidate_pack(payload, output_path=output_path)
	except (CertifiedProblemBankError, FileNotFoundError, ValueError) as exc:
		print(f"Problem pack validation failed: {exc}", file=sys.stderr)
		return 1

	if args.validate_only:
		problem_count = len(payload.get("problems") or [])
		print(f"Validated {problem_count} problems for {output_path.name} without writing the pack.")
		return 0

	PROBLEM_BANK_DIR.mkdir(parents=True, exist_ok=True)
	output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
	problem_count = len(payload.get("problems") or [])
	print(f"Imported {problem_count} problems into {output_path.as_posix()}.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
