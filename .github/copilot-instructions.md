# AI Interview Simulator - Architecture v2

## What We Are Building
An end-to-end AI-powered technical interview simulation system for fresher roles.
The system simulates a hiring process with resume analysis, role matching, assessment, voice-led interview rounds, a DSA round, and a final report.

This version of the architecture is designed for reality, not demo slides.

Operating assumptions for MVP:
- One active interview session per local machine
- Low-latency turn-based voice, not full-duplex real-time conversation
- Python-only DSA round in MVP
- Windows host with WSL2 Ubuntu for backend and Docker workloads
- Zero-cost core stack: Groq free tier, local/offline STT and TTS, self-hosted Judge0

Two goals simultaneously:
- Strong NLP/AI academic project submission
- Portfolio-grade product demo with reliable scoring and recoverable session state

Core principles:
- Zero cost wherever possible
- Reliability over feature count
- Honest latency targets over marketing claims
- No silent failures; every subsystem needs a fallback path
- AI should respond to what the user actually said and coded, not template assumptions

---

## Developer Context
Name      : Venkata Karthik
Degree    : B.Tech ECE
College   : Amrita School of Engineering, Bangalore
Python    : Intermediate
Workspace : E:\interview_simulator
Host OS   : Windows
Backend   : WSL2 Ubuntu
Constraint: No paid APIs for core functionality
LLM       : Groq free tier
Code Exec : Judge0 self-hosted via Docker in WSL2

---

## Technology Stack
Backend         : FastAPI (async orchestration)
Database        : Supabase (authoritative state, storage, auth-ready)
Resume parsing  : PyMuPDF text extraction + Groq structured JSON extraction
Role matching   : SBERT all-MiniLM-L6-v2
LLM calls       : Groq free tier with batching, retry, and backoff
Answer eval     : Groq rubric + SBERT similarity + lightweight communication metrics
Voice STT       : faster-whisper base.en, int8 CPU, VAD enabled
Voice TTS       : Piper TTS (local, offline)
Code execution  : Judge0 self-hosted in WSL2 Docker
Frontend        : React.js + Monaco Editor + Recharts + WebSocket
DSA languages   : Python only in MVP
Fine-tuned model: BERT answer scorer, optional post-MVP
CodeT5+ 220M    : Optional post-MVP for Tier 2 problem generation only after sandboxed validation exists

---

## Local Runtime Constraints
Recommended minimum machine for full local mode:
- 16 GB RAM
- 6 logical CPU cores
- SSD
- Stable internet for Groq and Supabase

Operational capacity in MVP:
- 1 active interview session per machine
- 1 STT job at a time
- 1 Judge0 execution at a time
- Voice rounds are turn-based, not overlapping TTS and STT

Runtime layout:
- Windows host: browser, VS Code, microphone, speaker
- WSL2 Ubuntu: FastAPI, STT worker, TTS worker, Judge0 Docker stack
- Supabase cloud: session persistence and reports
- Groq API: question generation and evaluation

Resource policy:
- Cap WSL2/Docker memory to 3-4 GB
- Run FastAPI and STT worker outside Judge0 containers
- Health check Judge0 before DSA coding starts
- If STT latency exceeds threshold twice in a round, switch that round to typed answers

---

## Complete System Flow
User uploads PDF resume
-> Text extraction with PyMuPDF
-> Validation gate for encrypted, scanned, or low-text PDFs
-> Groq structured JSON extraction
-> Parsed resume JSON saved to Supabase
-> Role matching using SBERT + Groq-generated role profiles
-> Assessment round with backend-scored MCQs
-> HR interview round (turn-based voice)
-> Technical interview round (turn-based voice)
-> Project discussion round (turn-based voice)
-> DSA round (problem prompt + Monaco + optional voice debrief)
-> Final report built from persisted session data

Voice round interaction flow:
- Prompt text displayed immediately
- Optional TTS playback using Piper
- User records answer
- VAD check
- faster-whisper transcription
- Groq rubric evaluation + local metrics
- Next prompt prepared

DSA round interaction flow:
- Problem state loaded from Supabase
- Prompt displayed immediately
- Optional TTS playback for instructions only
- User discusses approach
- User codes in Monaco
- Judge0 executes a single harnessed run per submission
- Results persisted after every run
- Debrief via voice first, typed fallback always available
- Final score and report persisted on completion

---

## Interview Round Order
Order must remain:
Technical -> DSA -> Project Discussion -> HR -> Final Report

The frontend flow should use separate pages for Technical, DSA, Project Discussion, and HR instead of grouping the interview rounds into one shared page.
The DSA page is role-conditional in MVP: include it only for roles that normally receive an algorithmic or coding screen, and skip directly from Technical to Project Discussion for roles where a DSA round is not standard.
Q1 and Q2 problems are selected before the DSA round begins.
Q2 may be pre-fetched during earlier rounds, but the system must have a fallback to a cached Tier 1 problem if pre-fetch is late or unavailable.

---

## Module 1 - Resume Parsing

### Why LLM Instead of Custom NER
A custom NER pipeline is overkill for the current constraints.
It requires labeled data, training cycles, ongoing maintenance, and still performs poorly on diverse resume formats.

Groq-based structured extraction is the right MVP choice because it:
- Handles varied phrasing better than static regex or small custom NER
- Requires zero training data to start
- Produces downstream-friendly structured JSON
- Keeps iteration speed high for a solo project

### Resume Parsing Reality Constraints
LLM extraction does not make document ingestion magically reliable.
The parser must explicitly handle:
- Encrypted PDFs
- Image-only or scanned PDFs with no text layer
- Broken multi-column ordering
- Very low-information resumes

### Resume Parsing Flow
PDF uploaded by user
-> PyMuPDF extracts raw text
-> Validation gate:
   - encrypted PDF -> reject with clear error
   - extracted text below minimum threshold -> reject as scanned or unsupported PDF
   - extraction succeeds -> continue
-> Groq prompt requests strict JSON only
-> JSON cleanup and schema validation
-> Parsed resume JSON saved to Supabase with session_id
-> Data reused in role matching, question generation, project discussion, and final report

### Extraction Schema
```json
{
  "session_id": "uuid",
  "name": "Candidate Name",
  "email": "candidate@example.com",
  "phone": "+91...",
  "location": "City, Country",
  "summary": "Short candidate summary",
  "interests": ["machine learning", "cloud"],
  "skills": ["Python", "C++", "SQL"],
  "technologies": ["PyTorch", "TensorFlow", "AWS"],
  "projects": [
    {
      "title": "Project title",
      "description": "Project summary",
      "tech_stack": ["Python", "AWS"],
      "outcomes": ["reduced false positives"],
      "role": "Built and deployed end to end"
    }
  ],
  "education": [
    {
      "institution": "University",
      "degree": "Degree",
      "cgpa": "8.5",
      "year": "2023-Present"
    }
  ],
  "certifications": [
    {
      "title": "Certification",
      "platform": "Provider",
      "status": "Completed"
    }
  ],
  "achievements": [
    {
      "title": "Achievement",
      "details": "Short details"
    }
  ]
}
```

### Non-Negotiable Parsing Rule
Every Groq response that is expected to be JSON must go through a common cleanup + validation layer before `json.loads` and before persistence.

---

## Module 2 - Role Matching

### Flow
Resume JSON fetched from Supabase
-> Candidate text built from summary, skills, technologies, projects, and certifications
-> Groq generates a small set of candidate-fit role profiles
-> SBERT encodes candidate text and role descriptions
-> Weighted score per role:
   - skill_overlap: 40%
   - semantic_match: 35%
   - project_relevance: 15%
   - bonus_skills: 10%
-> Roles ranked and saved to Supabase

### Feasibility Rules
- Role generation should happen in one Groq call, not one call per role
- If role scores are too tightly clustered, warn the user that the resume lacks enough signal for accurate ranking
- Role matching is advisory, not a hard classifier

---

## Module 3 - Question Generation

### HR Questions
Input:
- candidate name, summary, interests
- selected role

Output:
- 5 personalized HR questions
- ideal answer points
- generated in one batch Groq call

### Technical Questions
Input:
- candidate skills
- skill gaps for selected role
- difficulty signal from prior responses

Output:
- 6 technical questions
- ideal answer points
- follow-up prompt
- difficulty label

### Project Discussion Questions
Input:
- actual projects from parsed resume JSON

Output:
- 3 technical questions per project
- each question must reference the stated stack, outcomes, or design choices from the user's own resume

### Generation Rules
- Generate question batches, not one question per API call
- Cache the batch once generated
- If a round restarts after disconnect, reuse cached questions instead of regenerating

---

## Module 4 - Answer Evaluation

### Per Answer Pipeline
User speaks
-> VAD gate verifies speech exists
-> faster-whisper transcribes to text
-> Groq evaluates with structured rubric
-> SBERT compares user answer to ideal answer points
-> Lightweight communication metrics run locally
-> final score = Groq rubric 70% + SBERT similarity 20% + communication 10%
-> score and feedback saved to Supabase

### Voice Reality Rules
- HR and Technical answers capped at 20 seconds
- Project and DSA debrief answers capped at 30 seconds
- If no speech is detected, ask the user to retry without consuming a Groq call
- If STT fails twice in the same round or exceeds latency threshold twice, switch the rest of that round to typed input

### Adaptive Difficulty
Use running average from recent answered questions.
Practical MVP rule:
- HR and Technical: running average of last 3
- Project Discussion: inherit difficulty signal from Technical round, then adapt using last 2 answers

---

## Module 5 - DSA Round Engine

### Architecture Overview
Six sub-systems:
- Problem Bank Engine
- Code Execution Engine
- Interview Brain
- Analysis Engine
- Evaluator Engine
- Report Engine

### MVP Scope Guardrail
The DSA round is Python-only in MVP.
Judge0 may support more languages internally, but language-aware analysis and feedback are only guaranteed for Python until dedicated analyzers exist.

### Sub-System 1 - Problem Bank

#### Tier 1 - Curated Core
A hand-verified core pool is still the right starting point.
Each problem stores:
- statement, constraints, examples
- topic, difficulty, roles
- reference solution
- brute-force solution
- optimal time and space
- hints
- follow-up questions
- starter code
- test cases

#### Tier 2 - Generated but Sandboxed
Generated problems are allowed only if validation is sandboxed.
Updated flow:
- Groq generates problem + reference solution A
- Groq generates independent solution B
- Random and adversarial inputs are generated locally
- Both solutions are executed inside Judge0 sandbox, never with backend `exec()`
- If outputs agree across the validation set, store in draft pool
- Human spot-check required before promotion to certified pool

#### Tier 3 - Open Source Imports
External problem sources remain acceptable if their test quality is strong.
Sources may include Codeforces, USACO archives, and open LeetCode datasets.
Imported problems still need normalization and metadata cleanup before entering the local bank.

#### Problem Selection Logic
User selects role
-> system queries curated bank for matching topics and difficulty
-> exclude user_seen_problems
-> choose Q1
-> pre-fetch Q2 in background
-> if pre-fetch misses the time window, fall back to adjacent-difficulty Tier 1 problem

### Sub-System 2 - Code Execution

#### Why Judge0 Still Wins
Judge0 remains the correct choice because it gives:
- sandboxed execution
- time and memory limits
- language runtime isolation
- structured compile/runtime results

#### Updated Execution Flow
User submits code
-> safety gate rejects dangerous imports and unsupported patterns
-> code harness builder wraps the user solution and all test cases
-> one Judge0 submission executes the full harness and returns per-test JSON
-> result parser stores pass/fail counts, timing, memory, and visible failure details
-> all results persisted to Supabase

#### Execution Rules
- Never send one Judge0 job per hidden test case
- Never run untrusted user code in the backend process
- Sample runs are rate-limited to protect the local machine
- Judge0 availability must be checked before entering coding phase

### Sub-System 3 - Interview Brain

#### Single Source of Truth
The backend state machine is authoritative.
WebSocket is transport only.
State must persist to Supabase after every meaningful transition.

#### Persisted State Requirements
Store in `dsa_sessions.state_json`:
- stage
- state_version
- stage_started_at
- deadline_at
- clarification_count
- approach_exchange_count
- hint_level
- optimization_used
- last_submission_id
- last_judge_status
- awaiting_user_input
- candidate_model

#### Candidate Mental Model
Use explicit types instead of ambiguous booleans where uncertainty exists.

Candidate model fields:
- understands_problem: low | medium | high
- identified_correct_approach: bool
- mentioned_optimal_data_structure: bool
- considered_edge_cases_in_approach: bool
- actual_approach_from_code: string | null
- code_handles_edge_cases: bool
- is_solution_optimal: yes | no | unknown
- complexity_stated_correctly: bool
- explanation_quality: vague | partial | clear
- seems_stuck: bool
- time_pressure_visible: bool

#### 5 Stage Flow Per Problem
Stage 1 - PROBLEM_SETUP
- problem text is displayed immediately
- TTS is optional and skippable
- user may ask up to 3 clarifying questions or spend up to 2 minutes here
- then transition to APPROACH_DISCUSSION

Stage 2 - APPROACH_DISCUSSION
- max 3 exchanges or 4 minutes
- if no viable direction after exchange 2, give hint 1
- if still blocked after exchange 3, give hint 2
- if still blocked, force transition to CODING_PHASE instead of looping forever

Stage 3 - CODING_PHASE
- backend timer is authoritative
- code draft autosaves locally and on every run/submit
- sample runs allowed but throttled
- final submit triggers Judge0
- timer expiry auto-submits the latest saved draft or records an empty attempt

Stage 4 - OPTIMIZATION_PHASE
- may be entered at most once per problem
- enter only if solution is mostly correct but too slow, or clearly brute force
- allow exactly one optimization re-submit
- after the re-submit, always transition to DEBRIEF

Stage 5 - DEBRIEF
- voice first, typed fallback always available
- if STT fails twice or is too slow twice, continue the rest of debrief in text
- persist final score and mark stage COMPLETE

#### Hint System
Human-written hints remain the right choice.
Rules:
- 3 total hints max per problem
- hint budget is shared across approach and optimization phases
- hints deduct from scoring according to rubric

### Sub-System 4 - Analysis Engine

#### Scope
Python-only in MVP.
This is a deliberate product cut, not a missing feature.
It keeps static analysis and complexity reasoning reliable.

#### Outputs
- max loop nesting depth
- recursion and memoization signals
- data structures used
- early return or empty-input handling
- estimated time and space complexity
- brute-force likelihood

#### Limits
- `optimal` is best-effort, not mathematically guaranteed
- non-Python code should not receive analysis-based coaching until language-specific analyzers exist

### Sub-System 5 - Evaluator Engine

#### Per-Dimension Scoring
Five separate Groq evaluations remain correct.
They must run concurrently, not sequentially.

Dimensions:
- Approach Quality: 25%
- Code Correctness: 35%
- Complexity Awareness: 20%
- Follow-up Depth: 15%
- Communication: 5%

#### Score Aggregation
question_score = (
approach_score * 0.25 +
correctness_score * 0.35 +
complexity_score * 0.20 +
followup_score * 0.15 +
communication * 0.05
)

Penalty modifiers:
- correctness_score == 0 -> cap total at 60%
- hints_used >= 2 -> multiply by 0.90
- timed_out -> multiply by 0.95

session_score = Q1_score * 0.45 + Q2_score * 0.55

### Sub-System 6 - Report Engine

#### Report Sections
1. Score Overview
2. Correctness Breakdown
3. Code Annotation
4. Approach Comparison
5. Complexity Deep Dive
6. Follow-up Review
7. Growth Recommendations

#### Updated Accuracy Rule
The report may claim:
- exact compiler/runtime line when Judge0 provides one
- otherwise, a best-effort relevant code region

The report must not promise an exact bug line when the runtime does not provide one deterministically.

#### Coding Journey
Every intermediate submission is stored.
This remains one of the strongest product features and should be preserved exactly.

---

## Module 6 - Voice System

### Product Reality
This is a low-latency turn-based voice system.
It is not a continuous real-time voice conversation.

### STT
Use faster-whisper `base.en` with:
- int8 CPU inference
- VAD enabled
- dedicated worker process
- English-only evaluation path for MVP

### TTS
Use Piper TTS with:
- local offline synthesis
- pre-generated audio for fixed question batches when possible
- dynamic generation only for follow-up prompts

### Turn State
`playing_prompt -> waiting_for_user -> recording -> transcribing -> evaluating -> next_prompt_ready`

Rules:
- microphone disabled while prompt audio is playing
- prompt text always visible on screen
- `Skip Audio` is supported
- if transcription exceeds threshold, show visible progress and allow typed fallback

---

## Module 7 - Fine-tuned Models

### Model 1 - BERT Answer Quality Scorer
Optional post-MVP signal.
Do not block product delivery on this.
Groq rubric scoring is good enough for the first usable version.

### Model 2 - CodeT5+ 220M DSA Generator
Optional post-MVP component for Tier 2 problem generation.
Do not build this until the Judge0-based validation pipeline exists and the curated bank is already operational.

---

## Database Schema

### Tables
users
- id, email, name, created_at

sessions
- id, user_id, role_selected, status, created_at, completed_at

resume_data
- id, session_id, raw_text, parsed_json, created_at

role_matches
- id, session_id, role_key, match_percent, skill_gaps, score_breakdown

interview_responses
- id, session_id, round, question_id, question_text, user_answer_text, audio_file_path, score, dimension_scores, feedback, created_at

dsa_sessions
- id, session_id, problem_id, question_number, stage, state_version, state_json, stage_started_at, deadline_at, approach_text, current_code_draft, all_code_submissions, last_submission_id, last_judge_status, final_code, execution_results, dimension_scores, total_score, completed_at, updated_at

problems
- id, slug, source, certified, title, statement, constraints, topics, difficulty, roles, reference_solution_primary, reference_solution_alternate, brute_force_solution, optimal_time, optimal_space, brute_time, brute_space, test_cases, optimal_approach_explanation, key_insight, hints, followup_questions, avg_solve_time_mins, pass_rate, created_at

user_seen_problems
- user_id, problem_id, seen_at

problem_analytics
- problem_id, total_attempts, pass_rate, skip_rate, avg_score, avg_time_mins, updated_at

final_reports
- id, session_id, overall_score, round_scores, dimension_scores, report_json, created_at

---

## Folder Structure
```text
interview_simulator/
├── .github/
│   └── copilot-instructions.md
├── backend/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes_resume.py
│   │   ├── routes_interview.py
│   │   ├── routes_assessment.py
│   │   ├── routes_dsa.py
│   │   └── routes_report.py
│   ├── nlp/
│   │   ├── __init__.py
│   │   ├── resume_parser.py
│   │   ├── role_matcher.py
│   │   ├── question_generator.py
│   │   ├── answer_evaluator.py
│   │   └── feedback_generator.py
│   ├── dsa/
│   │   ├── __init__.py
│   │   ├── problem_selector.py
│   │   ├── problem_validator.py
│   │   ├── code_executor.py
│   │   ├── code_analyzer.py
│   │   ├── interview_brain.py
│   │   ├── dsa_evaluator.py
│   │   └── report_engine.py
│   ├── voice/
│   │   ├── __init__.py
│   │   ├── stt.py
│   │   └── tts.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── supabase_client.py
│   │   └── queries.py
│   └── data/
│       └── problems/
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── UploadPage.jsx
│       │   ├── AssessmentPage.jsx
│       │   ├── InterviewPage.jsx
│       │   ├── DSAPage.jsx
│       │   └── ReportPage.jsx
│       └── components/
│           ├── CodeEditor.jsx
│           ├── VoiceInterface.jsx
│           ├── TestResults.jsx
│           ├── StageIndicator.jsx
│           └── TimerBar.jsx
├── notebooks/
├── scripts/
│   ├── add_problem.py
│   ├── run_verification.py
│   └── analytics_update.py
└── README.md
```

---

## Module Status

### Scaffolded in Workspace
The following modules now exist as stubs in this workspace:
- backend package and subsystem directories
- API route files
- NLP module files
- DSA module files
- voice module files
- database module files
- frontend pages and components
- scripts folder

### Not Implemented Yet
All current module files are scaffolds only.
No runtime logic should be assumed complete until each subsystem is built and validated.

### Immediate Build Order
1. backend/database/supabase_client.py
2. backend/nlp/resume_parser.py
3. backend/nlp/role_matcher.py
4. backend/nlp/question_generator.py
5. backend/voice/stt.py
6. backend/voice/tts.py
7. backend/dsa/code_executor.py
8. backend/dsa/interview_brain.py
9. backend/dsa/code_analyzer.py
10. backend/dsa/dsa_evaluator.py
11. frontend pages and voice workflow
12. backend/dsa/report_engine.py

---

## Key Decisions

### Architecture Decisions
Resume parsing  : PyMuPDF + Groq LLM, not custom NER
Voice STT       : faster-whisper, not standard Whisper CPU flow
Voice TTS       : Piper TTS, not gTTS
Runtime         : WSL2 backend on Windows host
Database        : Supabase from the start, not SQLite-first migration
Code execution  : Judge0 self-hosted, sandboxed
DSA languages   : Python only in MVP
DSA state       : Supabase-backed authoritative state machine
Problem validation: Judge0 sandbox, never backend `exec()` on generated code
DSA evaluation  : 5 separate Groq calls in parallel, not one big prompt

### What Groq Is Used For
- resume extraction
- dynamic role generation
- question generation
- answer evaluation
- DSA approach evaluation
- DSA follow-up generation
- DSA follow-up evaluation
- report narration and structured feedback
- optional Tier 2 problem generation

### What Groq Is Never Used For
- executing code
- generating expected outputs for unverified problems
- being the single source of truth for session state
- producing hints on the fly

---

## What Makes This System Stand Out

### Resume Parsing
Structured LLM extraction keeps the system flexible across resume formats while remaining fast to iterate.

### Project Discussion Round
Questions are grounded in the candidate's actual projects and stated stack, not generic project prompts.

### DSA Problem Correctness
The architecture refuses to trust generated expected outputs without sandboxed validation.

### DSA Interview Realism
The interviewer responds to persisted candidate state, prior answers, and actual code behavior, not just scripted prompts.

### DSA Evaluation Specificity
Separate scoring dimensions and persisted submissions make feedback explainable instead of vague.

### Coding Journey
Every code submission is kept, enabling a report that shows improvement, dead ends, and final outcome instead of only the last attempt.
