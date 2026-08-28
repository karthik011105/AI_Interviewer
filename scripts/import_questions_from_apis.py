"""Multi-source question importer.

Pulls questions from:
  1. Open Trivia DB  — free, no key, Computer Science category
  2. QuizAPI.io      — free tier (500 req/month), API key required
                       Sign up at https://quizapi.io  (free, no credit card)
                       Set env var QUIZ_API_KEY=<your_key> before running

Normalises every question to the local question_bank.json schema and merges
with the existing bank (deduplicates by SHA-256 of the normalised question text).

Usage:
    # Open Trivia DB only (no key needed):
    python scripts/import_questions_from_apis.py

    # Both sources:
    set QUIZ_API_KEY=<your_key>
    python scripts/import_questions_from_apis.py
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is not installed. Run: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BANK_PATH = Path("backend/data/assessments/question_bank.json")
QUIZ_API_KEY = os.environ.get("QUIZ_API_KEY", "").strip()

OPENTDB_URL = "https://opentdb.com/api.php"

# ---------------------------------------------------------------------------
# Topic / family mapping tables
# ---------------------------------------------------------------------------

# Open Trivia DB: category 18 = Science: Computers
# Their difficulty: easy / medium / hard  (already matches our schema)

OPENTDB_TOPIC_KEYWORDS: list[tuple[list[str], str, list[str]]] = [
    # (keywords in question text, our topic, our role_families)
    (["python", "django", "flask"],              "python",           ["backend", "data", "ai_ml"]),
    (["javascript", "node", "es6", "es2015"],    "javascript",       ["frontend", "backend"]),
    (["java", "jvm", "spring"],                  "java",             ["backend"]),
    (["sql", "mysql", "postgresql", "database",
      "query", "select", "table", "join"],       "sql",              ["backend", "data"]),
    (["html", "css", "dom"],                     "html_css",         ["frontend"]),
    (["algorithm", "sort", "search", "binary",
      "bubble", "merge", "quick", "complexity"], "logic",            ["software_foundations", "backend", "data"]),
    (["data structure", "linked list", "stack",
      "queue", "tree", "graph", "heap", "hash"], "data_structures",  ["software_foundations", "backend"]),
    (["oop", "object-oriented", "class",
      "inherit", "polymorphism", "encapsulat"],  "oops",             ["backend", "software_foundations"]),
    (["network", "tcp", "udp", "http", "ip",
      "dns", "protocol"],                        "networking",       ["backend", "devops"]),
    (["os", "operating system", "process",
      "thread", "memory", "cpu", "scheduler"],   "operating_systems",["software_foundations"]),
    (["linux", "unix", "bash", "shell"],         "linux",            ["devops", "backend"]),
    (["git", "version control"],                 "tools",            ["software_foundations", "backend"]),
    (["docker", "container", "kubernetes"],      "tools",            ["devops", "backend"]),
    (["security", "encrypt", "hash", "ssl",
      "tls", "xss", "injection"],               "web_security",     ["security", "backend"]),
    (["machine learning", "neural", "ai",
      "deep learning", "model", "training"],     "ml_basics",        ["ai_ml", "data"]),
]

QUIZAPI_CATEGORY_MAP: dict[str, tuple[str, list[str], list[str]]] = {
    # QuizAPI v2 category → (our topic, role_families, domains)
    "programming":  ("logic",        ["software_foundations", "backend", "data"], ["software"]),
    "devops":       ("ci_cd",        ["devops"],                                  ["devops"]),
    "science":      ("logic",        ["software_foundations"],                    ["software"]),
    "mathematics":  ("logic",        ["software_foundations", "data"],            ["software"]),
    "general":      ("logic",        ["software_foundations"],                    ["software"]),
}

QUIZAPI_DIFFICULTY_MAP = {
    "EASY":   "easy",
    "MEDIUM": "medium",
    "HARD":   "hard",
    "EXPERT": "hard",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_fingerprint(text: str) -> str:
    """SHA-256 of lower-cased, whitespace-collapsed text — used for dedup."""
    normalised = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalised.encode()).hexdigest()


def _safe_id(prefix: str, text: str) -> str:
    """Short collision-resistant question_id from a text fingerprint."""
    return f"{prefix}_{_text_fingerprint(text)[:10]}"


def _infer_topic_from_text(text: str) -> tuple[str, list[str], list[str]]:
    """Return (topic, role_families, domains) by scanning question text."""
    lower = text.lower()
    for keywords, topic, families in OPENTDB_TOPIC_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return topic, families, ["software"]
    return "logic", ["software_foundations", "backend"], ["software"]


def _clean_html(text: str) -> str:
    """Decode HTML entities and strip tags."""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _letter_ids(n: int) -> list[str]:
    return [chr(ord("a") + i) for i in range(n)]


# ---------------------------------------------------------------------------
# Source 1: Open Trivia DB
# ---------------------------------------------------------------------------

OPENTDB_BATCH = 50  # max per request

def _fetch_opentdb(amount: int = 50, difficulty: str | None = None) -> list[dict]:
    """Fetch Computer Science questions from opentdb.com."""
    params: dict = {
        "amount":   amount,
        "category": 18,       # Science: Computers
        "type":     "multiple",
        "encode":   "url3986",
    }
    if difficulty:
        params["difficulty"] = difficulty

    resp = requests.get(OPENTDB_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("response_code") != 0:
        print(f"  OpenTDB response_code={data.get('response_code')} — skipping batch")
        return []

    return data.get("results", [])


def _opentdb_to_schema(raw: dict) -> dict | None:
    """Convert one OpenTDB question dict to our schema. Returns None if invalid."""
    prompt = _clean_html(
        requests.utils.unquote(raw.get("question", ""))
    )
    correct_raw = _clean_html(
        requests.utils.unquote(raw.get("correct_answer", ""))
    )
    incorrect_raw = [
        _clean_html(requests.utils.unquote(x))
        for x in raw.get("incorrect_answers", [])
    ]

    if not prompt or not correct_raw or len(incorrect_raw) < 3:
        return None

    # Build shuffled options a/b/c/d
    all_answers = [correct_raw] + incorrect_raw[:3]
    random.shuffle(all_answers)
    letter_ids = _letter_ids(len(all_answers))
    options = [{"id": lid, "text": ans} for lid, ans in zip(letter_ids, all_answers)]
    correct_option_id = next(
        o["id"] for o in options if o["text"] == correct_raw
    )

    difficulty = raw.get("difficulty", "medium")
    topic, families, domains = _infer_topic_from_text(prompt)

    return {
        "question_id":       _safe_id("otdb", prompt),
        "prompt":            prompt,
        "options":           options,
        "correct_option_id": correct_option_id,
        "explanation":       f"Correct answer: {correct_raw}",
        "role_families":     families,
        "domains":           domains,
        "topic":             topic,
        "difficulty":        difficulty,
        "skill_tag":         topic,
        "question_type":     "mcq_single",
        "is_core":           False,
        "active":            True,
    }


def pull_opentdb(total: int = 150) -> list[dict]:
    """Pull up to `total` CS questions from Open Trivia DB across all difficulties."""
    print(f"\n[Open Trivia DB] Pulling up to {total} Computer Science questions…")
    results: list[dict] = []

    for difficulty in ("easy", "medium", "hard"):
        per_call = min(OPENTDB_BATCH, total // 3)
        print(f"  Fetching {per_call} × {difficulty}…", end=" ", flush=True)
        try:
            raw_batch = _fetch_opentdb(amount=per_call, difficulty=difficulty)
            # OpenTDB rate-limits: wait 5 s between calls
            time.sleep(5)
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue

        converted = [_opentdb_to_schema(r) for r in raw_batch]
        valid = [q for q in converted if q is not None]
        results.extend(valid)
        print(f"got {len(valid)}")

    print(f"  Total from OpenTDB: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# Source 2: QuizAPI.io  (v2 API)
# ---------------------------------------------------------------------------

# Browse questions across published quizzes — MULTIPLE_CHOICE only
QUIZAPI_BROWSE_URL = "https://quizapi.io/api/v1/questions"
QUIZAPI_CATEGORIES_V2 = "Programming,DevOps"
QUIZAPI_BATCH = 50  # max per request (free tier)
QUIZAPI_PAGES  = 4  # pull up to 4 pages × 50 = 200 questions


def _quizapi_to_schema(raw: dict) -> dict | None:
    """Convert one QuizAPI v2 question to our schema."""
    prompt = _clean_html(raw.get("text", ""))
    if not prompt:
        return None

    answers: list[dict] = raw.get("answers", [])
    if not answers:
        return None

    # Build options list (up to 4) and find the correct one
    options: list[dict] = []
    correct_option_id: str | None = None
    letter_ids = _letter_ids(4)

    for idx, ans in enumerate(answers[:4]):
        lid = letter_ids[idx]
        text = _clean_html(ans.get("text", ""))
        if not text:
            continue
        options.append({"id": lid, "text": text})
        if ans.get("isCorrect"):
            correct_option_id = lid

    if not options or correct_option_id is None:
        return None

    # Category → topic mapping (lower-case lookup)
    category = (raw.get("category") or "programming").lower().strip()
    topic_info = QUIZAPI_CATEGORY_MAP.get(category, ("logic", ["software_foundations"], ["software"]))
    # Refine topic using question text keywords
    q_topic, families, domains = _infer_topic_from_text(prompt)
    if q_topic != "logic":
        # keyword match wins over category mapping
        topic, families, domains = q_topic, families, domains
    else:
        topic = topic_info[0]
        families = topic_info[1]
        domains = topic_info[2]

    raw_diff = (raw.get("difficulty") or "MEDIUM").strip().upper()
    difficulty = QUIZAPI_DIFFICULTY_MAP.get(raw_diff, "medium")

    explanation = _clean_html(raw.get("explanation") or "")
    if not explanation:
        correct_text = next((o["text"] for o in options if o["id"] == correct_option_id), "")
        explanation = f"Correct answer: {correct_text}"

    return {
        "question_id":       _safe_id("qapi", prompt),
        "prompt":            prompt,
        "options":           options,
        "correct_option_id": correct_option_id,
        "explanation":       explanation,
        "role_families":     families,
        "domains":           domains,
        "topic":             topic,
        "difficulty":        difficulty,
        "skill_tag":         topic,
        "question_type":     "mcq_single",
        "is_core":           False,
        "active":            True,
    }


def pull_quizapi() -> list[dict]:
    """Pull MULTIPLE_CHOICE questions from QuizAPI.io v2 browse endpoint."""
    if not QUIZ_API_KEY:
        print("\n[QuizAPI.io] QUIZ_API_KEY not set — skipping.")
        print("  Set it with:  $env:QUIZ_API_KEY='<your_key>'  then re-run.")
        return []

    print(f"\n[QuizAPI.io v2] Pulling questions (key=…{QUIZ_API_KEY[-6:]})")
    results: list[dict] = []
    headers = {"Authorization": f"Bearer {QUIZ_API_KEY}"}

    for page in range(QUIZAPI_PAGES):
        offset = page * QUIZAPI_BATCH
        print(f"  Page {page + 1} (offset={offset})…", end=" ", flush=True)
        try:
            resp = requests.get(
                QUIZAPI_BROWSE_URL,
                params={
                    "category": QUIZAPI_CATEGORIES_V2,
                    "type":     "MULTIPLE_CHOICE",
                    "limit":    QUIZAPI_BATCH,
                    "offset":   offset,
                    "random":   "true",
                },
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            time.sleep(1)
        except Exception as exc:
            print(f"ERROR: {exc}")
            break

        raw_batch = body.get("data", [])
        if not raw_batch:
            print("no more data")
            break

        converted = [_quizapi_to_schema(r) for r in raw_batch]
        valid = [q for q in converted if q is not None]
        results.extend(valid)
        print(f"got {len(valid)}")

        # Stop if we've exhausted all pages
        meta = body.get("meta", {})
        if meta.get("page") >= meta.get("lastPage", 1):
            break

    print(f"  Total from QuizAPI: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# Merge + dedup
# ---------------------------------------------------------------------------

def merge_into_bank(new_questions: list[dict]) -> None:
    data = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    existing = data["questions"]

    # Build fingerprint set from existing questions
    existing_fp: set[str] = {
        _text_fingerprint(q["prompt"]) for q in existing
    }
    existing_ids: set[str] = {q["question_id"] for q in existing}

    added = 0
    skipped_dup_text = 0
    skipped_dup_id = 0

    for q in new_questions:
        fp = _text_fingerprint(q["prompt"])
        qid = q["question_id"]

        if fp in existing_fp:
            skipped_dup_text += 1
            continue
        if qid in existing_ids:
            # ID collision but different text — rename
            q["question_id"] = qid + "_b"
            if q["question_id"] in existing_ids:
                skipped_dup_id += 1
                continue

        existing.append(q)
        existing_fp.add(fp)
        existing_ids.add(q["question_id"])
        added += 1

    data["questions"] = existing
    BANK_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    all_qs = data["questions"]
    print(f"\n=== Merge complete ===")
    print(f"  Added:              {added}")
    print(f"  Skipped (same Q):   {skipped_dup_text}")
    print(f"  Skipped (dup ID):   {skipped_dup_id}")
    print(f"  Total in bank:      {len(all_qs)}")
    print(f"  Difficulty:         {dict(Counter(q['difficulty'] for q in all_qs))}")
    print(f"  Type:               {dict(Counter(q['question_type'] for q in all_qs))}")
    print(f"  Topics:             {dict(Counter(q['topic'] for q in all_qs).most_common(10))}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    random.seed(42)

    all_new: list[dict] = []

    # Source 1: Open Trivia DB (always)
    all_new.extend(pull_opentdb(total=150))

    # Source 2: QuizAPI.io (if key present)
    all_new.extend(pull_quizapi())

    if not all_new:
        print("\nNo questions fetched. Check your network or API key.")
        sys.exit(0)

    merge_into_bank(all_new)
    print("\nDone. Run the test suite to confirm no regressions.")
