"""Update role_catalog.json so every role:
  - has core_count = 2 (freeing 1 slot)
  - adds difficulties: ["easy","medium","hard"] to ALL existing requirements
  - adds a code_output requirement (1 question, medium|hard)

Run from repo root:
    python scripts/update_role_catalog.py
"""

from __future__ import annotations
import json
from pathlib import Path

CATALOG_PATH = Path("backend/data/assessments/role_catalog.json")

# All remaining roles are software/IT/AI oriented — no hardware exclusions needed

def main() -> None:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    for role in data["roles"]:
        # 1. Reduce core to 2
        role["core_count"] = 2

        # 2. Allow hard difficulty on every existing targeted requirement
        for req in role.get("targeted_requirements", []):
            existing = req.get("difficulties", ["easy", "medium"])
            if "hard" not in existing:
                existing.append("hard")
            req["difficulties"] = existing

        # 3. Add code_output requirement unless already present or hardware role
        role_key = role.get("role_key", "")
        has_code_output = any(
            r.get("topic") == "code_output"
            for r in role.get("targeted_requirements", [])
        )
        if not has_code_output:
            role["targeted_requirements"].append(
                {
                    "topic": "code_output",
                    "count": 1,
                    "difficulties": ["medium", "hard"],
                }
            )

    CATALOG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Print a quick summary
    for role in data["roles"]:
        req_total = sum(r.get("count", 1) for r in role["targeted_requirements"])
        total = role["core_count"] + req_total
        print(
            f"{role['role_key']:40s}  core={role['core_count']}  req_slots={req_total}  total={total}"
        )

if __name__ == "__main__":
    main()
