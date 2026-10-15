"""Resume Quality Scorer — pure local NLP, no API calls.

Analyses the parsed resume JSON and raw text to produce a structured
score card across four dimensions:

  1. Action Verb Strength   — strong vs weak verbs in project/experience bullets
  2. Quantification         — presence of numbers, %, metrics in project descriptions
  3. Role Keyword Density   — TF-IDF overlap between resume text and role signal terms
  4. Section Completeness   — presence and depth of key resume sections

All processing runs in-process using regex, word lists, and sklearn TF-IDF.
No LLM or external API is used.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary lists
# ---------------------------------------------------------------------------

STRONG_ACTION_VERBS: frozenset[str] = frozenset({
    # Built / shipped
    "built", "developed", "engineered", "designed", "architected", "implemented",
    "created", "launched", "deployed", "shipped", "released", "published",
    # Improved / optimised
    "optimised", "optimized", "improved", "accelerated", "reduced", "decreased",
    "increased", "boosted", "enhanced", "upgraded", "refactored", "rewrote",
    # Led / drove
    "led", "drove", "spearheaded", "pioneered", "initiated", "established",
    "founded", "directed", "managed", "mentored", "coached",
    # Solved / fixed
    "solved", "debugged", "resolved", "fixed", "eliminated", "mitigated",
    "identified", "diagnosed", "detected",
    # Automated / integrated
    "automated", "integrated", "migrated", "containerised", "containerized",
    "dockerized", "orchestrated", "configured", "provisioned", "deployed",
    # Analysed / researched
    "analysed", "analyzed", "researched", "evaluated", "tested", "validated",
    "benchmarked", "profiled", "measured", "monitored",
    # Other strong
    "secured", "scaled", "parallelized", "serialized", "trained", "fine-tuned",
    "scraped", "parsed", "visualised", "visualized", "modelled", "modeled",
    "predicted", "classified", "clustered", "generated", "synthesized",
})

WEAK_ACTION_VERBS: frozenset[str] = frozenset({
    "worked", "helped", "assisted", "supported", "contributed", "participated",
    "involved", "part of", "responsible for", "tasked with", "engaged",
    "collaborated", "liaised", "communicated", "handled", "dealt",
    "did", "made", "got", "tried", "used", "used to", "learnt", "learned",
    "understood", "aware", "familiar", "exposure",
})

# ---------------------------------------------------------------------------
# Role keyword signal maps (TF-IDF-style seed vocabulary per role family)
# ---------------------------------------------------------------------------

# Each entry is one skill; "a|b|c" lists interchangeable alternatives, any one
# of which counts. Without a job description to compare against, expecting a
# fresher to know Flask *and* Django *and* Spring would make every match low.
ROLE_KEYWORDS: dict[str, list[str]] = {
    "backend":    ["python|java|golang|node.js|nodejs|c#|kotlin", "api", "rest|restful", "sql|mysql|postgresql|sqlite", "database",
                   "nosql|mongodb|dynamodb|cassandra", "cache|caching|redis",
                   "flask|django|fastapi|spring|spring boot|express|node.js|nodejs",
                   "authentication|jwt|oauth", "docker", "microservice",
                   "ci/cd|github actions|jenkins|gitlab ci", "kubernetes|k8s",
                   "graphql|grpc", "kafka|rabbitmq|message queue"],
    "frontend":   ["javascript", "typescript", "html", "css", "react|vue|angular|svelte|next.js",
                   "component", "hooks", "redux|zustand|state management|context api",
                   "responsive", "tailwind|bootstrap|sass|scss", "accessibility|a11y|aria",
                   "dom", "vite|webpack", "ui|ux"],
    "data":       ["python|r", "sql|mysql|postgresql", "pandas", "numpy", "excel", "statistics|statistical",
                   "tableau|power bi|looker", "matplotlib|seaborn|plotly", "jupyter",
                   "etl", "pipeline", "spark|pyspark", "airflow",
                   "data warehouse|snowflake|bigquery|redshift|dbt", "hypothesis|a/b test",
                   "regression", "classification"],
    "ai_ml":      ["python", "machine learning", "deep learning", "neural network",
                   "pytorch|tensorflow|keras", "scikit-learn|sklearn",
                   "nlp|natural language processing", "computer vision|opencv",
                   "transformer|bert|hugging face", "llm|gpt", "rag|retrieval",
                   "embedding|vector database", "fine-tuning|fine-tuned",
                   "hyperparameter", "cross-validation",
                   "precision|recall|f1|roc|auc"],
    "devops":     ["python|bash|go|golang", "linux", "bash|shell scripting", "docker", "kubernetes|k8s",
                   "ci/cd", "jenkins|github actions|gitlab ci", "aws|gcp|azure",
                   "terraform|infrastructure as code|iac", "ansible", "helm",
                   "monitoring", "prometheus|grafana", "networking|dns|load balancer"],
    "security":   ["penetration testing|pentest", "owasp", "vulnerability", "threat",
                   "firewall", "encryption|cryptography", "ssl|tls", "siem|soc",
                   "incident response", "iam|rbac|access control", "zero trust",
                   "authentication|oauth|jwt", "nmap|wireshark|burp suite|metasploit"],
    "software_foundations": ["data structures", "algorithms", "oop|object-oriented",
                             "design patterns|solid", "git|github",
                             "unit test|pytest|junit|jest", "testing",
                             "debug|debugged|debugging", "complexity|big-o", "recursion"],
}

# Map role_key prefixes/families to keyword groups
_ROLE_FAMILY_MAP: list[tuple[list[str], list[str]]] = [
    (["backend_python", "backend_java", "backend_node", "full_stack"], ["backend", "software_foundations"]),
    (["frontend_react", "mobile"], ["frontend", "software_foundations"]),
    (["data_analyst", "business_intelligence"], ["data"]),
    (["data_engineer"], ["data", "backend"]),
    (["machine_learning", "ai_engineer", "data_scientist"], ["ai_ml", "data"]),
    (["devops", "cloud", "site_reliability"], ["devops"]),
    (["cybersecurity"], ["security"]),
    (["software_engineer", "qa"], ["backend", "software_foundations"]),
]


# Role names the matcher invents ("junior_backend_engineer", "api_engineer")
# rarely start with a family prefix, so fall back to the words in the key/title.
_FAMILY_TOKEN_HINTS: list[tuple[set[str], list[str]]] = [
    ({"security", "cyber", "cybersecurity", "soc"}, ["security"]),
    ({"ml", "ai", "machine", "learning", "scientist", "nlp", "vision", "llm"}, ["ai_ml", "data"]),
    ({"data", "analyst", "analytics", "bi", "etl", "warehouse"}, ["data"]),
    ({"devops", "cloud", "sre", "platform", "infrastructure", "reliability"}, ["devops"]),
    ({"frontend", "front", "react", "ui", "web", "angular", "vue", "mobile", "android", "ios"}, ["frontend", "software_foundations"]),
    ({"backend", "back", "api", "python", "java", "node", "django", "spring", "server"}, ["backend", "software_foundations"]),
]


def _role_keywords_for_key(role_key: str, role_title: str = "") -> list[str]:
    """Keyword list for a role: family prefix first, then words in the key/title."""
    rk = (role_key or "").lower()
    families: list[str] | None = None
    for prefixes, fams in _ROLE_FAMILY_MAP:
        # Anywhere in the key, so "junior_data_engineer" still reads as data_engineer.
        if any(re.search(rf"(^|_){re.escape(p)}", rk) for p in prefixes):
            families = fams
            break
    if families is None:
        tokens = set(re.split(r"[^a-z0-9]+", f"{rk} {(role_title or '').lower()}"))
        for hints, fams in _FAMILY_TOKEN_HINTS:
            if tokens & hints:
                families = fams
                break
    if families is None:
        families = ["software_foundations", "backend"]
    keywords: list[str] = []
    for fam in families:
        for kw in ROLE_KEYWORDS.get(fam, []):
            if kw not in keywords:
                keywords.append(kw)
    return keywords


def _has_keyword(text_lower: str, keyword: str) -> bool:
    """Whole-word match, allowing a plural ("APIs", "databases") and any of the
    "a|b" alternatives. Plain substring matching found "ci" in "specific",
    "ui" in "build" and "rest" in "interest", inflating every keyword score."""
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(alt.strip())}(?:e?s)?(?![a-z0-9])", text_lower)
        for alt in keyword.lower().split("|")
    )


def _keyword_label(text_lower: str, keyword: str) -> str:
    """What to show for a skill: the alternative the resume uses, or the first
    few alternatives ("flask / django / fastapi") when none is there."""
    alts = [alt.strip() for alt in keyword.split("|")]
    for alt in alts:
        if _has_keyword(text_lower, alt):
            return alt
    # Grammar variants of the first one ("debugged", "statistical") are for
    # matching, not for showing.
    shown = [alt for alt in alts if alt == alts[0] or not alt.startswith(alts[0][:5])]
    return " / ".join(shown[:3])


# ---------------------------------------------------------------------------
# Quantification patterns
# ---------------------------------------------------------------------------

_QUANTIFICATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d+[\.,]?\d*\s*%"),                          # 40%, 99.9%
    re.compile(r"\b\d+[xX]\b"),                                 # 3x, 10x
    re.compile(r"\b\d+\s*(ms|seconds?|minutes?|hours?)\b", re.I),  # 200ms, 2 hours
    re.compile(r"\b(reduced|improved|increased|decreased|boosted|cut|saved)\b.{0,40}\b\d+", re.I),
    re.compile(r"\b\d[\d,]*\s*(users?|requests?|calls?|records?|rows?|files?|tests?|errors?)\b", re.I),
    re.compile(r"\b(first|top|2nd|3rd|\d+th)\b", re.I),        # ranked first, top 5
    re.compile(r"\b\d+(?:\.\d+)?\s*(million|billion|thousand|k|m)\b", re.I),  # 2 million, 10k - a bare "k"/"m" is not a metric
]


# Any other number counts too ("served 1,200 students", "3,000 titles"),
# except years, version numbers and digits inside names (EC2, S3, HTML5).
_NUMBER = re.compile(r"(?<![A-Za-z0-9#.])(\d[\d,]*(?:\.\d+)?)(?![A-Za-z0-9])")
_NOT_A_METRIC_BEFORE = re.compile(
    r"(python|java|php|node|angular|ios|android|windows|version|v|grade|class|semester|sem|standard)\s*$",
    re.IGNORECASE,
)


def _has_metric(text: str) -> bool:
    if any(p.search(text) for p in _QUANTIFICATION_PATTERNS):
        return True
    for match in _NUMBER.finditer(text):
        digits = match.group(1).replace(",", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        if value == 0 or (len(digits) == 4 and 1950 <= value <= 2039):
            continue
        if _NOT_A_METRIC_BEFORE.search(text[max(0, match.start() - 14):match.start()]):
            continue
        return True
    return False


_BULLET_MARK = "•●▪◦○■-–—*·➢►✓"
_BULLET_LINE = re.compile(rf"^\s*[{re.escape(_BULLET_MARK)}]\s*(.*)$")
# "Languages: Python, C++" - a skills list, not an achievement bullet.
_LABELLED_LIST = re.compile(r"^[A-Za-z][A-Za-z &/+.-]{1,28}:\s")


def _resume_bullets(raw_text: str, parsed_resume: dict[str, Any]) -> list[str]:
    """The resume's bullet points, the unit recruiters' checkers count.

    Read from the PDF text when it has bullet markers, with wrapped lines
    joined back on (PDFs often put the marker on its own line, or break a
    bullet mid-sentence). Splitting a joined blob on every "." and "-" (as
    before) turned "real-time" and "Node.js" into extra bullets and buried
    real metrics. Falls back to the parsed projects when there are no markers.
    """
    from backend.nlp.resume_detector import is_heading

    bullets: list[str] = []
    open_bullet = False
    for line in (raw_text or "").splitlines():
        stripped = line.strip()
        match = _BULLET_LINE.match(line)
        if match and not (stripped[:1] == "-" and stripped[1:2].isalnum()):
            bullets.append(match.group(1).strip())
            open_bullet = True
        elif not stripped or is_heading(stripped):
            open_bullet = False
        elif open_bullet and bullets:
            last = bullets[-1]
            # Text after a bare marker, or a wrapped line of an unfinished bullet.
            if not last or last[-1] not in ".!?" or stripped[0].islower() or stripped[0].isdigit():
                bullets[-1] = f"{last} {stripped}".strip()
            else:
                open_bullet = False
    bullets = [b for b in bullets if not (_LABELLED_LIST.match(b) and b.count(",") >= 2)]
    bullets = [b for b in bullets if len(b) > 15]
    if len(bullets) >= 3:
        return bullets

    items: list[str] = []
    for project in parsed_resume.get("projects") or []:
        if not isinstance(project, dict):
            continue
        items.extend(re.split(r"(?<=[.!?])\s+(?=[A-Z])", str(project.get("description") or "")))
        items.extend(str(o) for o in (project.get("outcomes") or []) if o)
    return [i.strip() for i in items if len(i.strip()) > 15]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ActionVerbResult:
    strong_count: int
    weak_count: int
    total_checked: int
    strong_verbs_found: list[str]
    weak_verbs_found: list[str]
    score: float          # 0.0 – 1.0
    label: str            # "Excellent" / "Good" / "Needs work" / "Weak"
    tip: str


@dataclass
class QuantificationResult:
    quantified_bullets: int
    total_bullets: int
    score: float
    label: str
    tip: str


@dataclass
class SectionCompletenessResult:
    sections_present: list[str]
    sections_missing: list[str]
    depth_notes: list[str]
    score: float
    label: str
    tip: str


@dataclass
class ResumeQualityScore:
    overall_score: float          # 0–100
    overall_label: str            # "Strong" / "Good" / "Needs improvement" / "Weak"
    action_verbs: ActionVerbResult
    quantification: QuantificationResult
    section_completeness: SectionCompletenessResult
    style: "StyleResult"
    top_tip: str                  # Single most impactful improvement


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _label_from_score(score: float) -> str:
    if score >= 0.80:
        return "Excellent"
    if score >= 0.60:
        return "Good"
    if score >= 0.40:
        return "Needs work"
    return "Weak"


def _score_action_verbs(project_text: str) -> ActionVerbResult:
    words = re.findall(r"\b[a-z]+\b", project_text.lower())
    strong_found: list[str] = []
    weak_found: list[str] = []
    seen: set[str] = set()

    for w in words:
        if w in seen:
            continue
        seen.add(w)
        if w in STRONG_ACTION_VERBS:
            strong_found.append(w)
        elif w in WEAK_ACTION_VERBS:
            weak_found.append(w)

    total = len(strong_found) + len(weak_found)

    if total == 0:
        score = 0.3  # no verbs at all
        tip = "Add strong action verbs to your project descriptions (e.g. 'built', 'reduced', 'automated')."
    else:
        # Score = strong / (strong + weak), penalise for having weak verbs
        ratio = len(strong_found) / total
        # Also penalise if very few strong verbs in absolute terms
        abs_bonus = min(len(strong_found) / 5, 1.0)  # 5+ strong verbs = full bonus
        score = 0.7 * ratio + 0.3 * abs_bonus
        if len(weak_found) > 2:
            tip = f"Replace weak verbs ({', '.join(weak_found[:3])}) with stronger alternatives like 'built', 'optimised', 'led'."
        elif len(strong_found) < 3:
            tip = "Use more strong action verbs in your project descriptions to show impact."
        else:
            tip = "Good use of action verbs. Consider adding more impact-focused verbs like 'scaled' or 'reduced'."

    return ActionVerbResult(
        strong_count=len(strong_found),
        weak_count=len(weak_found),
        total_checked=len(seen),
        strong_verbs_found=strong_found[:10],
        weak_verbs_found=weak_found[:5],
        score=round(score, 3),
        label=_label_from_score(score),
        tip=tip,
    )


def _score_quantification(bullets: list[str]) -> QuantificationResult:
    quantified = sum(1 for b in bullets if _has_metric(b))
    total = max(len(bullets), 1)

    ratio = quantified / total
    # Reward even 1-2 quantified bullets heavily — they signal result orientation
    score = min(ratio * 1.5, 1.0)  # 67%+ quantified = full score

    if quantified == 0:
        label = "Weak"
        tip = "Add specific numbers and metrics. E.g. 'reduced load time by 40%' instead of 'improved performance'."
    elif ratio < 0.3:
        label = "Needs work"
        tip = f"Only {quantified} of your {total} bullet points include a number. Aim for at least half."
    elif ratio < 0.6:
        label = "Good"
        tip = f"{quantified} of your {total} bullet points include a number. Add metrics to more of them - even rough estimates count."
    else:
        label = "Excellent"
        tip = "Strong quantification. Your resume clearly shows impact through numbers."

    return QuantificationResult(
        quantified_bullets=quantified,
        total_bullets=total,
        score=round(score, 3),
        label=label,
        tip=tip,
    )


def _score_section_completeness(parsed_resume: dict[str, Any]) -> SectionCompletenessResult:
    sections_present: list[str] = []
    sections_missing: list[str] = []
    depth_notes: list[str] = []

    # Name + contact
    if parsed_resume.get("name"):
        sections_present.append("Name")
    else:
        sections_missing.append("Name")

    if parsed_resume.get("email"):
        sections_present.append("Email")
    else:
        sections_missing.append("Email")

    # Summary
    summary = (parsed_resume.get("summary") or "").strip()
    if len(summary) > 30:
        sections_present.append("Summary")
        if len(summary.split()) < 20:
            depth_notes.append("Summary is very short — expand to 3-4 sentences.")
    else:
        sections_missing.append("Summary / Objective")

    # Skills
    skills = parsed_resume.get("skills") or []
    techs = parsed_resume.get("technologies") or []
    all_skills = list(skills) + list(techs)
    if len(all_skills) >= 3:
        sections_present.append("Skills")
        if len(all_skills) < 8:
            depth_notes.append(f"Only {len(all_skills)} skills listed — aim for 10+.")
    else:
        sections_missing.append("Skills / Technologies")

    # Projects
    projects = parsed_resume.get("projects") or []
    if len(projects) >= 1:
        sections_present.append("Projects")
        if len(projects) == 1:
            depth_notes.append("Only 1 project — add 2-3 projects for stronger coverage.")
        has_outcomes = sum(1 for p in projects if p.get("outcomes"))
        if has_outcomes == 0:
            depth_notes.append("None of your projects mention outcomes — add what you achieved.")
    else:
        sections_missing.append("Projects")

    # Education
    education = parsed_resume.get("education") or []
    if len(education) >= 1:
        sections_present.append("Education")
        has_cgpa = any(e.get("cgpa") for e in education)
        if not has_cgpa:
            depth_notes.append("Consider adding your CGPA/GPA if it is above 7.5.")
    else:
        sections_missing.append("Education")

    # Certifications
    certs = parsed_resume.get("certifications") or []
    if len(certs) >= 1:
        sections_present.append("Certifications")
    else:
        sections_missing.append("Certifications (optional but helpful)")

    # Achievements — optional, noted but not penalised in score
    achievements = parsed_resume.get("achievements") or []
    if len(achievements) >= 1:
        sections_present.append("Achievements")
    # Not a depth_note — too harsh for freshers

    # Score: weighted by importance
    # Certifications and achievements are bonuses, not required.
    weights = {
        "Name": 0.08, "Email": 0.07, "Summary": 0.12,
        "Skills": 0.20, "Projects": 0.28,
        "Education": 0.15, "Certifications": 0.05, "Achievements": 0.05,
    }
    # (name recorded in sections_present, name shown to the candidate). These
    # used to compare "Summary / Objective" against the recorded "Summary", so
    # a resume with every section was still told to add Summary and Skills.
    required = [
        ("Name", "Name"),
        ("Email", "Email"),
        ("Summary", "Summary / Objective"),
        ("Skills", "Skills"),
        ("Projects", "Projects"),
        ("Education", "Education"),
    ]
    present_set = set(sections_present)
    # Match partial section names to weight keys
    base_score = 0.0
    for s in sections_present:
        for wk, wv in weights.items():
            if wk.lower() in s.lower() or s.lower() in wk.lower():
                base_score += wv
                break
    base_score = min(base_score, 1.0)
    # Cap depth penalty — only penalise for truly missing critical sections
    depth_penalty = min(len([n for n in depth_notes if "missing" in n.lower() or "no " in n.lower()]) * 0.05, 0.10)
    score = max(0.0, min(1.0, base_score - depth_penalty))

    missing_required = [shown for recorded, shown in required if recorded not in present_set]
    if missing_required:
        tip = f"Add missing sections: {', '.join(missing_required[:2])}."
    elif depth_notes:
        tip = depth_notes[0]
    else:
        tip = "All key sections present with good depth."

    return SectionCompletenessResult(
        sections_present=sections_present,
        sections_missing=sections_missing,
        depth_notes=depth_notes,
        score=round(score, 3),
        label=_label_from_score(score),
        tip=tip,
    )


_BUZZWORDS: tuple[str, ...] = (
    "hardworking", "hard-working", "hard working", "team player", "go-getter",
    "self-motivated", "self motivated", "detail-oriented", "detail oriented",
    "passionate", "dynamic", "synergy", "results-driven", "results driven",
    "think outside the box", "quick learner", "fast learner", "responsible for",
    "duties included", "various",
)
_PRONOUN_RE = re.compile(r"\b(I|me|my|mine|myself)\b")
_BULLET_RE = re.compile(r"^\s*(?:[-*\u2022\u25aa\u25cf\u2013]|\d+[.)])\s+(.+)$", re.M)


@dataclass
class StyleResult:
    word_count: int
    pronoun_count: int
    buzzwords_found: list[str]
    long_bullets: int
    score: float
    label: str
    tip: str


def _score_style(raw_text: str) -> StyleResult:
    """Clarity and style, the checks Resume Worded groups as brevity/style:
    length, first-person pronouns, buzzwords and over-long bullet points."""

    text = raw_text or ""
    lower = text.lower()
    word_count = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'+#.-]*", text))
    pronouns = len(_PRONOUN_RE.findall(text))
    buzzwords = [b for b in _BUZZWORDS if _has_keyword(lower, b)]
    bullets = [m.group(1) for m in _BULLET_RE.finditer(text)]
    long_bullets = sum(1 for b in bullets if len(b.split()) > 32)

    issues: list[tuple[float, str]] = []
    if word_count < 200:
        issues.append((0.20 + 0.40 * (200 - word_count) / 200, f"Your resume is short ({word_count} words). Add detail to projects and skills - aim for 350-700 words."))
    elif word_count > 900:
        issues.append((0.15, f"Your resume is long ({word_count} words). Keep a fresher resume to one page - about 350-700 words."))
    if pronouns:
        issues.append((min(pronouns * 0.04, 0.20), f'Drop first-person words like "I" and "my" ({pronouns} found). Start bullets with an action verb instead.'))
    if buzzwords:
        quoted = ", ".join(f'"{b}"' for b in buzzwords[:3])
        issues.append((min(len(buzzwords) * 0.07, 0.30), f"Replace vague phrases like {quoted} with a concrete example of what you did."))
    if long_bullets:
        issues.append((min(long_bullets * 0.06, 0.20), f"{long_bullets} bullet point{'s are' if long_bullets > 1 else ' is'} very long. Keep each to one or two lines."))

    score = max(0.0, 1.0 - sum(weight for weight, _ in issues))
    tip = max(issues, key=lambda item: item[0])[1] if issues else "Clear and concise - no filler words or first-person phrasing."
    return StyleResult(
        word_count=word_count,
        pronoun_count=pronouns,
        buzzwords_found=buzzwords,
        long_bullets=long_bullets,
        score=round(score, 3),
        label=_label_from_score(score),
        tip=tip,
    )


@dataclass
class RoleMatchResult:
    role_key: str
    role_title: str
    matched: list[str]
    missing: list[str]
    match_percent: float
    tip: str
    skill_coverage: float = 0.0
    profile_similarity: float | None = None
    other_skills: list[str] = field(default_factory=list)


# A JobBERT skill counts for a role skill when MiniLM puts them this close
# ("postgres" ~ "postgresql", "ci pipelines" ~ "ci/cd").
_SKILL_SIMILARITY = 0.72
# Resume-vs-role cosine similarity measured on a backend resume: ~0.33-0.40
# for unrelated roles, 0.55 for a neighbouring one (devops), 0.71 for the
# right one. Stretch 0.35-0.75 onto 0-100 so the score separates them.
_PROFILE_SIM_LOW, _PROFILE_SIM_HIGH = 0.35, 0.75
_COVERAGE_WEIGHT, _SIMILARITY_WEIGHT = 0.6, 0.4


def _semantic_encoder() -> Any | None:
    from backend.nlp.role_matcher import get_semantic_encoder  # heavy module; import lazily
    return get_semantic_encoder()


def _model_skills(raw_text: str) -> list[str]:
    from backend.nlp.skill_extractor import extract_skills
    return list(extract_skills(raw_text or ""))


def _semantic_hits(encoder: Any, keywords: list[str], skills: list[str]) -> dict[str, str]:
    """Role skills (keyword entries) that one of the extracted resume skills
    means, mapped to that resume skill."""
    if not keywords or not skills:
        return {}
    alts = [(kw, alt.strip()) for kw in keywords for alt in kw.split("|")]
    alt_vecs = encoder.encode([alt for _, alt in alts], normalize_embeddings=True)
    skill_vecs = encoder.encode(skills, normalize_embeddings=True)
    sims = alt_vecs @ skill_vecs.T
    hits: dict[str, str] = {}
    for row, (kw, _) in enumerate(alts):
        col = int(sims[row].argmax())
        if kw not in hits and float(sims[row][col]) >= _SKILL_SIMILARITY:
            hits[kw] = skills[col]
    return hits


def _profile_similarity(encoder: Any, resume_profile: str, role_profile: str) -> float:
    vecs = encoder.encode([resume_profile, role_profile], normalize_embeddings=True)
    cosine = float(vecs[0] @ vecs[1])
    return max(0.0, min(1.0, (cosine - _PROFILE_SIM_LOW) / (_PROFILE_SIM_HIGH - _PROFILE_SIM_LOW)))


def _resume_profile(parsed_resume: dict[str, Any], skills: list[str]) -> str:
    parts = [str(parsed_resume.get("summary") or "")]
    parts.append(", ".join(str(s) for s in (parsed_resume.get("skills") or [])))
    parts.append(", ".join(str(t) for t in (parsed_resume.get("technologies") or [])))
    for project in parsed_resume.get("projects") or []:
        if isinstance(project, dict):
            parts.append(f"{project.get('title') or ''}: {project.get('description') or ''}")
    parts.append(", ".join(skills))
    return ". ".join(p for p in parts if p.strip())


def score_role_match(
    parsed_resume: dict[str, Any],
    raw_text: str,
    role_key: str,
    role_title: str = "",
) -> RoleMatchResult:
    """How well the resume covers one role's hard skills - the Jobscan-style
    match rate. Only meaningful once a role has been chosen, so it is scored
    separately from the general resume quality score."""

    keywords = _role_keywords_for_key(role_key, role_title)
    title = role_title or role_key.replace("_", " ").title()
    skills_text = " ".join(str(s) for s in (parsed_resume.get("skills") or []))
    tech_text = " ".join(str(t) for t in (parsed_resume.get("technologies") or []))
    text = " ".join([raw_text or "", skills_text, tech_text, parsed_resume.get("summary") or ""]).lower()
    hits = {kw: _keyword_label(text, kw) for kw in keywords if _has_keyword(text, kw)}

    # Pretrained models, when installed: JobBERT finds the skills, MiniLM
    # matches them to the role by meaning and scores overall profile fit.
    model_skills = _model_skills(raw_text)
    encoder = _semantic_encoder()
    similarity: float | None = None
    if encoder is not None:
        try:
            unmatched = [kw for kw in keywords if kw not in hits]
            hits.update(_semantic_hits(encoder, unmatched, model_skills))
            role_profile = f"{title}. Skills: " + ", ".join(kw.replace("|", ", ") for kw in keywords)
            similarity = _profile_similarity(encoder, _resume_profile(parsed_resume, model_skills), role_profile)
        except Exception:
            similarity = None

    matched = [hits[kw] for kw in keywords if kw in hits]
    missing = [_keyword_label(text, kw) for kw in keywords if kw not in hits]
    coverage = len(hits) / len(keywords) if keywords else 0.0
    blended = coverage if similarity is None else _COVERAGE_WEIGHT * coverage + _SIMILARITY_WEIGHT * similarity
    pct = round(100 * blended, 1)
    used = {m.lower() for m in matched}
    other_skills = [
        skill for skill in model_skills
        if skill not in used and not any(_has_keyword(skill, kw) for kw in keywords)
    ][:10]
    to_add = ", ".join(m.split(" / ")[0] for m in missing[:3])
    if pct >= 60:
        tip = "Strong match. Make sure these skills also appear inside your project bullets, not only in the skills list."
    elif pct >= 35:
        tip = f"Reasonable match. If you have used them, add {to_add}."
    else:
        tip = f"Low match for this role. Learn or highlight {to_add}."
    return RoleMatchResult(
        role_key=role_key,
        role_title=title,
        matched=matched,
        missing=missing[:12],
        match_percent=pct,
        tip=tip,
        skill_coverage=round(100 * coverage, 1),
        profile_similarity=None if similarity is None else round(100 * similarity, 1),
        other_skills=other_skills,
    )


def score_role_match_as_dict(parsed_resume: dict[str, Any], raw_text: str, role_key: str, role_title: str = "") -> dict[str, Any]:
    from dataclasses import asdict
    return asdict(score_role_match(parsed_resume, raw_text, role_key, role_title))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_resume(
    parsed_resume: dict[str, Any],
    raw_text: str,
    role_key: str | None = None,
) -> ResumeQualityScore:
    """General resume quality - no role needed.

    Four dimensions modelled on what recruiter-facing checkers (Resume Worded,
    Jobscan's formatting checks) flag: measurable impact, action verbs, section
    completeness, and clarity/style. Role keyword coverage is NOT part of this
    score any more: it used to be 35% of it, computed against a default role
    before the candidate had chosen one. See score_role_match.

    ``role_key`` is accepted for backward compatibility and ignored.
    """
    projects: list[dict] = parsed_resume.get("projects") or []
    project_texts: list[str] = []
    for p in projects:
        if p.get("description"):
            project_texts.append(p["description"])
        outcomes = p.get("outcomes") or []
        project_texts.extend(str(o) for o in outcomes if o)
        if p.get("role"):
            project_texts.append(p["role"])
    project_text = " ".join(project_texts)

    skills_text = " ".join(str(s) for s in (parsed_resume.get("skills") or []))
    tech_text = " ".join(str(t) for t in (parsed_resume.get("technologies") or []))
    summary_text = parsed_resume.get("summary") or ""
    full_text = " ".join([raw_text, skills_text, tech_text, summary_text])

    action_result = _score_action_verbs(project_text or full_text)
    quant_result = _score_quantification(_resume_bullets(raw_text, parsed_resume))
    completeness_result = _score_section_completeness(parsed_resume)
    style_result = _score_style(raw_text)

    overall = (
        quant_result.score * 0.30
        + completeness_result.score * 0.30
        + action_result.score * 0.20
        + style_result.score * 0.20
    )
    # The other checks are ratios, so a 60-word resume with two numbers would
    # otherwise rate "Excellent". Too little text to judge caps the score.
    if style_result.word_count < 150:
        overall = min(overall, 0.60)
    overall_pct = round(overall * 100, 1)

    if overall >= 0.80:
        overall_label = "Excellent"
    elif overall >= 0.65:
        overall_label = "Strong"
    elif overall >= 0.50:
        overall_label = "Good"
    elif overall >= 0.35:
        overall_label = "Needs Improvement"
    else:
        overall_label = "Weak"

    dims = [
        (action_result.score, action_result.tip),
        (quant_result.score, quant_result.tip),
        (completeness_result.score, completeness_result.tip),
        (style_result.score, style_result.tip),
    ]
    dims.sort(key=lambda x: x[0])

    return ResumeQualityScore(
        overall_score=overall_pct,
        overall_label=overall_label,
        action_verbs=action_result,
        quantification=quant_result,
        section_completeness=completeness_result,
        style=style_result,
        top_tip=dims[0][1],
    )


def score_resume_as_dict(
    parsed_resume: dict[str, Any],
    raw_text: str,
    role_key: str | None = None,
) -> dict[str, Any]:
    """Serialisable version of score_resume for API responses."""
    result = score_resume(parsed_resume, raw_text, role_key)
    from dataclasses import asdict
    return asdict(result)
