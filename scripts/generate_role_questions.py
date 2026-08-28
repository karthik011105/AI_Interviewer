"""
Generate role-specific interview MCQ questions using Groq.

For each role in role_catalog.json, Groq generates questions that are
actually asked in real technical interviews for that role. Questions are
merged into question_bank.json with deduplication.

Usage:
    python scripts/generate_role_questions.py
    python scripts/generate_role_questions.py --roles backend_python_developer,data_engineer
    python scripts/generate_role_questions.py --roles all --per-topic 4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    from groq import Groq
except ImportError:
    print("ERROR: groq package not installed. Run: pip install groq")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BANK_PATH    = Path("backend/data/assessments/question_bank.json")
CATALOG_PATH = Path("backend/data/assessments/role_catalog.json")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    # Try loading from .env
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                GROQ_API_KEY = line.split("=", 1)[1].strip()
                break

if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not found. Set it in .env or environment.")
    sys.exit(1)

# How many questions to generate per topic per role (default, overridable)
DEFAULT_PER_TOPIC = 3
# Groq model to use
GROQ_MODEL = "llama-3.3-70b-versatile"
# Rate limit: Groq free tier ~30 req/min — stay safe
GROQ_DELAY_SECS = 3

# ---------------------------------------------------------------------------
# Topic descriptions — used to tell Groq what kinds of questions to generate
# ---------------------------------------------------------------------------

TOPIC_CONTEXT: dict[str, str] = {
    "python":             "Python programming: syntax, data types, list/dict/set comprehensions, decorators, generators, OOP in Python, common built-ins, error handling",
    "java":               "Core Java: OOP, JVM internals, Collections, Generics, Multithreading, Exception handling, Stream API, Spring basics",
    "javascript":         "JavaScript: ES6+, closures, prototypes, event loop, async/await, promises, DOM, common patterns",
    "react":              "React.js: hooks (useState/useEffect/useMemo/useCallback), component lifecycle, props vs state, context, virtual DOM, performance optimisation",
    "sql":                "SQL: SELECT, JOIN types, GROUP BY, subqueries, indexes, ACID, transactions, query optimisation, window functions",
    "dbms":               "Database Management Systems: normalisation, ER diagrams, ACID properties, indexing, query execution plans, CAP theorem",
    "api":                "API design and HTTP: REST principles, status codes, authentication (JWT/OAuth), rate limiting, versioning, pagination",
    "backend":            "Backend development: caching (Redis), message queues, microservices, session management, connection pooling",
    "oops":               "Object-Oriented Programming: encapsulation, inheritance, polymorphism, abstraction, SOLID principles, design patterns",
    "operating_systems":  "Operating Systems: processes vs threads, scheduling, memory management, deadlocks, virtual memory, IPC",
    "linux":              "Linux/Shell: common commands, file permissions, process management, cron, pipes, scripting, grep/awk/sed",
    "networking":         "Computer Networking: TCP/IP, HTTP/HTTPS, DNS, load balancing, CDN, WebSockets, network security",
    "data_structures":    "Data Structures: arrays, linked lists, stacks, queues, trees, graphs, hash maps — operations and complexity",
    "logic":              "Algorithms: sorting, searching, recursion, dynamic programming, greedy, graph traversal — Big-O analysis",
    "ci_cd":              "CI/CD and DevOps: pipelines, Jenkins/GitHub Actions, Docker, deployment strategies, rollback, IaC",
    "containers":         "Containers and orchestration: Docker images/layers, Docker Compose, Kubernetes pods/services/deployments",
    "cloud":              "Cloud computing: AWS/GCP/Azure services, IAM, serverless, object storage, managed databases, cost optimisation",
    "monitoring":         "Observability: metrics, logs, traces, alerting, SLA/SLO/SLI, tools like Prometheus/Grafana/Datadog",
    "security_basics":    "Security fundamentals: OWASP Top 10, encryption, hashing, TLS, secrets management, least privilege",
    "auth":               "Authentication and authorisation: JWT, OAuth 2.0, sessions, SSO, MFA, RBAC",
    "web_security":       "Web security: XSS, CSRF, SQL injection, CORS, CSP, secure cookies, HTTPS best practices",
    "ml_basics":          "Machine Learning fundamentals: supervised/unsupervised learning, bias-variance tradeoff, overfitting, common algorithms",
    "llm_basics":         "Large Language Models: transformer architecture, tokenisation, fine-tuning, RAG, embeddings, hallucination",
    "prompt_engineering": "Prompt engineering: zero-shot, few-shot, chain-of-thought, system prompts, temperature, top-p",
    "vector_search":      "Vector databases and semantic search: embeddings, cosine similarity, ANN, FAISS, Pinecone, chunking strategies",
    "preprocessing":      "ML data preprocessing: normalisation, encoding, feature engineering, handling missing data, train-test split",
    "metrics":            "ML evaluation metrics: accuracy, precision, recall, F1, ROC-AUC, RMSE, confusion matrix",
    "experimentation":    "ML experimentation: A/B testing, hypothesis testing, p-values, statistical significance, experiment tracking",
    "statistics":         "Statistics for data: mean/median/mode, standard deviation, distributions, correlation, hypothesis testing",
    "pandas":             "Pandas/NumPy: DataFrame operations, groupby, merge, pivot, vectorised operations, handling nulls",
    "visualization":      "Data visualisation: chart selection, Matplotlib/Seaborn, dashboarding principles, storytelling with data",
    "data_pipelines":     "Data pipelines: ETL vs ELT, batch vs streaming, Apache Spark/Kafka basics, data quality, orchestration",
    "data_modeling":      "Data modeling: star/snowflake schema, data warehousing, dimensional modeling, slowly changing dimensions",
    "dashboarding":       "BI dashboarding: KPI design, report vs dashboard, interactivity, data refresh, Tableau/Power BI basics",
    "testing":            "Software testing: unit, integration, E2E, TDD, BDD, mocking, test coverage, test pyramid",
    "automation":         "Test automation: Selenium, Playwright, Cypress basics, test frameworks, CI integration",
    "api_testing":        "API testing: Postman, REST Assured, contract testing, mocking APIs, boundary value testing",
    "test_design":        "Test design techniques: equivalence partitioning, boundary value analysis, decision tables, exploratory testing",
    "bug_reporting":      "Bug lifecycle and defect management: severity vs priority, reproduction steps, JIRA workflow, regression testing",
    "html_css":           "HTML5/CSS3: semantic HTML, box model, flexbox, grid, responsive design, accessibility (ARIA)",
    "css_layout":         "CSS layout and positioning: flexbox, grid, z-index, stacking context, media queries, CSS variables",
    "browser":            "Browser internals: event loop, rendering pipeline, repaint vs reflow, Web APIs, localStorage, service workers",
    "mobile":             "Mobile development: Android/iOS architecture basics, React Native, app lifecycle, push notifications",
    "state_management":   "State management: Redux, Zustand, Context API, MobX — when to use, common patterns",
    "async_programming":  "Asynchronous programming: event loop, callbacks, promises, async/await, concurrency vs parallelism",
    "incident_response":  "Incident management: on-call, runbooks, post-mortems, RCA, MTTR/MTTD",
    "c_programming":      "C programming: pointers, memory management, malloc/free, structs, bitwise operations, undefined behaviour",
    "cplusplus":          "C++ essentials: RAII, smart pointers, STL containers, templates, move semantics",
    "microcontrollers":   "Microcontrollers: GPIO, interrupts, timers, ADC/DAC, bare-metal vs RTOS, memory-mapped I/O",
    "digital_logic":      "Digital logic: combinational circuits, flip-flops, FSM, Boolean algebra, truth tables",
    "protocols":          "Embedded protocols: UART, SPI, I2C, CAN, USB — framing, baud rate, arbitration",
    "memory":             "Embedded memory: stack vs heap, ROM/RAM, flash, memory-mapped registers, DMA",
    "sensors":            "Sensors and actuators: ADC sampling, sensor fusion, PWM, calibration, noise filtering",
    "control_systems":    "Control systems: PID controllers, feedback loops, stability, Laplace transforms basics",
    "vlsi":               "VLSI design: CMOS logic, synthesis, place and route, timing closure, DRC/LVS",
    "verilog":            "Verilog/SystemVerilog: RTL coding, testbenches, always blocks, simulation vs synthesis",
    "timing":             "Timing analysis: setup/hold time, clock skew, metastability, timing paths, STA",
    "verification":       "Hardware verification: UVM, functional coverage, assertion-based verification, constrained random",
    "semiconductor_basics": "Semiconductor basics: MOSFET operation, PN junction, CMOS inverter, power dissipation",
    "programming":        "General programming concepts: variables, control flow, functions, recursion, complexity, I/O",
    "code_output":        "Code output prediction: read a short Python code snippet and identify the printed output — tests deep language knowledge",
}

# For code_output topics, generate code-snippet questions instead of MCQ theory
CODE_OUTPUT_ROLES_CONTEXT: dict[str, str] = {
    "backend_python_developer": "Python 3: list slicing, mutable defaults, closures, generators, dict operations, class inheritance",
    "backend_java_developer":   "Java: String pool, static members, autoboxing, Collections, try-with-resources, inheritance",
    "backend_node_developer":   "JavaScript: closure, hoisting, Promise chaining, async/await, event loop order",
    "frontend_react_developer": "JavaScript/React: closure, typeof, equality, array methods, spread operator",
    "full_stack_developer":     "JavaScript/Python: closure, mutable defaults, list comprehension, Promise order",
    "software_engineer":        "Python/Java: OOP, recursion trace, data structure mutation, type coercion",
    "data_analyst":             "Python/Pandas: DataFrame operations, list comprehension, numpy broadcasting",
    "data_engineer":            "Python: generators, context managers, list operations, class properties",
    "machine_learning_engineer":"Python: numpy operations, list slicing, decorator output, class methods",
    "ai_engineer":              "Python: comprehensions, generators, closures, string formatting edge cases",
    "data_scientist":           "Python: statistical operations, list/dict comprehension, class behaviour",
    "devops_engineer":          "Bash/Python: shell expansion, string ops, exit codes, list manipulation",
    "cloud_engineer":           "Python/Bash: string formatting, list operations, boolean edge cases",
    "cybersecurity_analyst":    "Python: string operations, bitwise ops, hash output, list mutation",
    "qa_automation_engineer":   "Python: list operations, class behaviour, exception handling flow",
    "mobile_app_developer":     "JavaScript: closure, array methods, async order, type coercion",
    "robotics_software_engineer":"Python/C++: pointer-like behaviour, list mutation, class methods",
    "iot_engineer":             "Python: bitwise operations, bytes, list slicing, generator behaviour",
}

# ---------------------------------------------------------------------------
# Groq call
# ---------------------------------------------------------------------------

_groq_client: Groq | None = None

def _get_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def _build_prompt(role_title: str, topic: str, topic_context: str, n: int, is_code_output: bool) -> str:
    if is_code_output:
        return f"""You are a senior technical interviewer creating a technical assessment for a "{role_title}" role.

Generate exactly {n} code-output prediction questions in JSON format.
Each question shows a SHORT code snippet (4-8 lines, Python 3) and asks what will be printed.
Focus on: {topic_context}

Return ONLY a JSON array. No markdown, no explanation. Each object must have:
- "prompt": the full question text including the code snippet inside triple backticks
- "options": array of exactly 4 objects with "id" ("a","b","c","d") and "text" fields
- "correct_option_id": "a", "b", "c", or "d"
- "explanation": brief explanation of why the answer is correct
- "difficulty": "medium" or "hard"

Example format:
[
  {{
    "prompt": "What is the output of the following Python code?\\n```python\\nx = [1, 2, 3]\\ny = x\\ny.append(4)\\nprint(x)\\n```",
    "options": [{{"id":"a","text":"[1, 2, 3]"}},{{"id":"b","text":"[1, 2, 3, 4]"}},{{"id":"c","text":"Error"}},{{"id":"d","text":"[4]"}}],
    "correct_option_id": "b",
    "explanation": "y = x makes y a reference to the same list. Appending to y also modifies x.",
    "difficulty": "medium"
  }}
]"""
    else:
        return f"""You are a senior technical interviewer creating a technical assessment for a "{role_title}" role.

Generate exactly {n} multiple-choice questions that are commonly asked in real technical interviews for this role.
Focus on this topic: {topic} — specifically: {topic_context}

Requirements:
- Questions must be factual, unambiguous, and clearly correct
- Mix of easy, medium, and hard difficulty
- Real-world interview style (not trivial textbook definitions)
- All 4 options must be plausible (no obviously wrong distractors)
- At least 1 hard question per batch

Return ONLY a JSON array. No markdown, no explanation. Each object must have:
- "prompt": the question text (no code unless short inline)
- "options": array of exactly 4 objects with "id" ("a","b","c","d") and "text" fields  
- "correct_option_id": "a", "b", "c", or "d"
- "explanation": brief explanation (1-2 sentences) of why the answer is correct
- "difficulty": "easy", "medium", or "hard"

[
  {{
    "prompt": "Sample question?",
    "options": [{{"id":"a","text":"Option A"}},{{"id":"b","text":"Option B"}},{{"id":"c","text":"Option C"}},{{"id":"d","text":"Option D"}}],
    "correct_option_id": "a",
    "explanation": "Explanation here.",
    "difficulty": "medium"
  }}
]"""


def _call_groq(prompt: str, role_key: str, topic: str) -> list[dict] | None:
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=4096,
        )
        raw = response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"    Groq error: {exc}")
        return None

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    # Extract JSON array
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        print(f"    No JSON array found in response for {role_key}/{topic}")
        return None

    try:
        return json.loads(match.group())
    except json.JSONDecodeError as exc:
        print(f"    JSON parse error for {role_key}/{topic}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Schema normalisation
# ---------------------------------------------------------------------------

def _text_fingerprint(text: str) -> str:
    normalised = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalised.encode()).hexdigest()


def _safe_id(role_key: str, topic: str, text: str) -> str:
    short_role = role_key[:6]
    short_topic = topic[:6]
    fp = _text_fingerprint(text)[:8]
    return f"grq_{short_role}_{short_topic}_{fp}"


def _normalise(raw: dict, role_key: str, role_title: str, role_families: list[str], domains: list[str], topic: str, is_code_output: bool) -> dict | None:
    prompt = (raw.get("prompt") or "").strip()
    options = raw.get("options", [])
    correct = (raw.get("correct_option_id") or "").strip().lower()
    explanation = (raw.get("explanation") or "").strip()
    difficulty = (raw.get("difficulty") or "medium").strip().lower()

    if not prompt or len(options) < 2 or not correct:
        return None
    if not any(o.get("id") == correct for o in options):
        return None
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"

    # Normalise options
    normed_options = []
    for o in options[:4]:
        oid = str(o.get("id", "")).strip().lower()
        text = str(o.get("text", "")).strip()
        if oid and text:
            normed_options.append({"id": oid, "text": text})

    if len(normed_options) < 2:
        return None

    return {
        "question_id":       _safe_id(role_key, topic, prompt),
        "prompt":            prompt,
        "options":           normed_options,
        "correct_option_id": correct,
        "explanation":       explanation,
        "role_families":     role_families,
        "domains":           domains,
        "topic":             "code_output" if is_code_output else topic,
        "difficulty":        difficulty,
        "skill_tag":         topic,
        "question_type":     "code_output" if is_code_output else "mcq_single",
        "is_core":           False,
        "active":            True,
    }


# ---------------------------------------------------------------------------
# Bank merge
# ---------------------------------------------------------------------------

def merge_into_bank(new_questions: list[dict]) -> int:
    data = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    existing = data["questions"]

    existing_fp: set[str] = {_text_fingerprint(q["prompt"]) for q in existing}
    existing_ids: set[str] = {q["question_id"] for q in existing}

    added = 0
    for q in new_questions:
        fp = _text_fingerprint(q["prompt"])
        if fp in existing_fp:
            continue
        qid = q["question_id"]
        if qid in existing_ids:
            q["question_id"] = qid + "_x"
            if q["question_id"] in existing_ids:
                continue
        existing.append(q)
        existing_fp.add(fp)
        existing_ids.add(q["question_id"])
        added += 1

    data["questions"] = existing
    BANK_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return added


# ---------------------------------------------------------------------------
# Main generation loop
# ---------------------------------------------------------------------------

def generate_for_role(role: dict, per_topic: int) -> list[dict]:
    role_key    = role["role_key"]
    role_title  = role.get("title", role_key)
    role_fam    = role.get("role_families", [])
    domains     = role.get("domains", ["software"])
    reqs        = role.get("targeted_requirements", [])

    all_questions: list[dict] = []

    for req in reqs:
        topic = req.get("topic", "")
        if not topic:
            continue

        is_code_output = (topic == "code_output")

        if is_code_output:
            context = CODE_OUTPUT_ROLES_CONTEXT.get(role_key, "Python 3: common patterns and gotchas")
        else:
            context = TOPIC_CONTEXT.get(topic, topic.replace("_", " ") + " concepts commonly tested in interviews")

        print(f"    topic={topic} ({per_topic} Qs)…", end=" ", flush=True)

        prompt = _build_prompt(role_title, topic, context, per_topic, is_code_output)
        raw_batch = _call_groq(prompt, role_key, topic)
        time.sleep(GROQ_DELAY_SECS)

        if not raw_batch:
            print("SKIP")
            continue

        normalised = [
            _normalise(r, role_key, role_title, role_fam, domains, topic, is_code_output)
            for r in raw_batch
        ]
        valid = [q for q in normalised if q is not None]
        all_questions.extend(valid)
        print(f"got {len(valid)}")

    return all_questions


def main(target_roles: list[str] | None, per_topic: int) -> None:
    rc = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    roles: list[dict] = rc["roles"]

    if target_roles:
        roles = [r for r in roles if r["role_key"] in target_roles]
        if not roles:
            print(f"No matching roles found for: {target_roles}")
            return

    total_added = 0
    topic_stats: dict[str, int] = defaultdict(int)

    for role in roles:
        rk = role["role_key"]
        print(f"\n[{rk}] — {role.get('title', rk)}")
        qs = generate_for_role(role, per_topic)
        for q in qs:
            topic_stats[q["topic"]] += 1
        added = merge_into_bank(qs)
        total_added += added
        print(f"  -> {len(qs)} generated, {added} new added to bank")

    # Final summary
    data = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    all_qs = data["questions"]
    from collections import Counter
    print("\n=== Generation complete ===")
    print(f"  Total new questions added: {total_added}")
    print(f"  Bank total:                {len(all_qs)}")
    print(f"  Difficulty breakdown:      {dict(Counter(q['difficulty'] for q in all_qs))}")
    print(f"  Type breakdown:            {dict(Counter(q['question_type'] for q in all_qs))}")
    print(f"\n  Topics generated:")
    for topic, count in sorted(topic_stats.items(), key=lambda x: -x[1]):
        print(f"    {topic}: {count}")
    print("\nRun tests: .venv\\Scripts\\python.exe -m unittest tests\\test_resume_upload_and_session_invariants.py -v")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate role-specific interview questions via Groq")
    parser.add_argument(
        "--roles",
        default="all",
        help="Comma-separated role_keys to generate for, or 'all' (default: all)",
    )
    parser.add_argument(
        "--per-topic",
        type=int,
        default=DEFAULT_PER_TOPIC,
        help=f"Questions per topic per role (default: {DEFAULT_PER_TOPIC})",
    )
    args = parser.parse_args()

    target = None if args.roles.strip().lower() == "all" else [r.strip() for r in args.roles.split(",")]
    main(target_roles=target, per_topic=args.per_topic)
