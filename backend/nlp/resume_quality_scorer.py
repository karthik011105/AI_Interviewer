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

ROLE_KEYWORDS: dict[str, list[str]] = {
    "backend":    ["api", "rest", "graphql", "microservice", "database", "sql", "cache",
                   "redis", "docker", "kubernetes", "ci", "cd", "authentication", "jwt",
                   "flask", "django", "fastapi", "spring", "node", "express", "postgresql",
                   "mysql", "mongodb", "kafka", "rabbitmq", "grpc"],
    "frontend":   ["react", "vue", "angular", "typescript", "javascript", "html", "css",
                   "webpack", "vite", "redux", "component", "ui", "ux", "responsive",
                   "accessibility", "dom", "hooks", "state management", "tailwind"],
    "data":       ["pandas", "numpy", "sql", "etl", "pipeline", "spark", "airflow",
                   "tableau", "power bi", "data warehouse", "dbt", "snowflake", "bigquery",
                   "redshift", "jupyter", "matplotlib", "seaborn", "statistics",
                   "hypothesis", "a/b test", "regression", "classification"],
    "ai_ml":      ["machine learning", "deep learning", "neural network", "pytorch",
                   "tensorflow", "scikit-learn", "nlp", "computer vision", "bert",
                   "transformer", "llm", "fine-tuning", "rag", "embedding", "vector",
                   "model training", "hyperparameter", "cross-validation", "roc", "f1"],
    "devops":     ["docker", "kubernetes", "terraform", "ansible", "jenkins", "github actions",
                   "ci/cd", "aws", "gcp", "azure", "monitoring", "prometheus", "grafana",
                   "linux", "bash", "infrastructure", "iac", "helm", "argocd"],
    "security":   ["penetration testing", "owasp", "firewall", "encryption", "ssl", "tls",
                   "soc", "siem", "vulnerability", "threat", "incident response", "iam",
                   "zero trust", "authentication", "oauth", "jwt", "rbac"],
    "software_foundations": ["data structures", "algorithms", "oop", "solid", "design patterns",
                             "git", "unit test", "testing", "debug", "complexity", "recursion"],
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


def _role_keywords_for_key(role_key: str) -> list[str]:
    """Return keyword list for a role_key by matching family prefixes."""
    rk = (role_key or "").lower()
    for prefixes, families in _ROLE_FAMILY_MAP:
        if any(rk.startswith(p) for p in prefixes):
            keywords: list[str] = []
            for fam in families:
                keywords.extend(ROLE_KEYWORDS.get(fam, []))
            return keywords
    return ROLE_KEYWORDS["software_foundations"]


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
    re.compile(r"\b(million|billion|thousand|k|m)\b", re.I),   # 1 million records
]


def _count_quantified_bullets(text: str) -> int:
    """Count sentences/bullets that contain a quantification signal."""
    bullets = re.split(r"[.\n•\-–—]+", text)
    return sum(
        1 for b in bullets
        if b.strip() and any(p.search(b) for p in _QUANTIFICATION_PATTERNS)
    )


def _total_bullets(text: str) -> int:
    bullets = re.split(r"[.\n•\-–—]+", text)
    return sum(1 for b in bullets if len(b.strip()) > 10)


# ---------------------------------------------------------------------------
# TF-IDF keyword density (pure Python, no sklearn for this function)
# ---------------------------------------------------------------------------

def _compute_keyword_density(resume_text: str, keywords: list[str]) -> float:
    """
    Fraction of role-relevant keywords that appear in the resume text.
    Uses simple term presence (binary TF) then computes coverage %.
    """
    if not keywords:
        return 0.0
    lower = resume_text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in lower)
    return hits / len(keywords)


def _compute_tfidf_similarity(resume_text: str, keywords: list[str]) -> float:
    """
    Cosine similarity between resume text and role keyword document using
    sklearn TfidfVectorizer (runs entirely in-process).

    Raw cosine similarity between a short resume and a keyword bag is naturally
    very low (0.03–0.12) even for strong resumes.  We remap the raw value onto
    a realistic 0–1 scale using empirical bounds:
      raw <= 0.03  → 0.0  (no meaningful overlap)
      raw >= 0.20  → 1.0  (excellent overlap)
    so that a typical good resume scores 0.5–0.8 instead of 0.05–0.15.
    """
    if not resume_text.strip() or not keywords:
        return 0.0
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        import numpy as np  # type: ignore

        role_doc = " ".join(keywords)
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
        tfidf = vectorizer.fit_transform([resume_text.lower(), role_doc.lower()])
        vec_a = tfidf[0].toarray()[0]
        vec_b = tfidf[1].toarray()[0]
        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        raw = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
        # Remap [0.03, 0.20] → [0.0, 1.0]
        low, high = 0.03, 0.20
        return max(0.0, min(1.0, (raw - low) / (high - low)))
    except Exception:
        return _compute_keyword_density(resume_text, keywords)


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
class KeywordDensityResult:
    role_key: str
    keywords_matched: int
    keywords_total: int
    tfidf_similarity: float
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
    keyword_density: KeywordDensityResult
    section_completeness: SectionCompletenessResult
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


def _score_quantification(project_text: str) -> QuantificationResult:
    quantified = _count_quantified_bullets(project_text)
    total = max(_total_bullets(project_text), 1)

    ratio = quantified / total
    # Reward even 1-2 quantified bullets heavily — they signal result orientation
    score = min(ratio * 1.5, 1.0)  # 67%+ quantified = full score

    if quantified == 0:
        label = "Weak"
        tip = "Add specific numbers and metrics. E.g. 'reduced load time by 40%' instead of 'improved performance'."
    elif ratio < 0.3:
        label = "Needs work"
        tip = f"Only {quantified} of your ~{total} project bullets include metrics. Aim for at least 50%."
    elif ratio < 0.6:
        label = "Good"
        tip = "Good start. Try to add metrics to more project bullets — even rough estimates count."
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


def _score_keyword_density(resume_text: str, role_key: str) -> KeywordDensityResult:
    keywords = _role_keywords_for_key(role_key)
    tfidf_sim = _compute_tfidf_similarity(resume_text, keywords)
    coverage = _compute_keyword_density(resume_text, keywords)
    matched = sum(1 for kw in keywords if kw.lower() in resume_text.lower())

    # 50% normalised TF-IDF + 50% raw binary keyword coverage.
    # Coverage alone already gives a fair reading; TF-IDF adds semantic depth.
    score = 0.50 * tfidf_sim + 0.50 * coverage

    if score < 0.20:
        label = "Weak"
        missing = [kw for kw in keywords if kw.lower() not in resume_text.lower()][:3]
        tip = f"Your resume lacks key technical terms for this role. Add: {', '.join(missing)}."
    elif score < 0.40:
        label = "Needs work"
        tip = "Add more domain-specific technologies and tools relevant to this role."
    elif score < 0.60:
        label = "Good"
        tip = "Decent keyword coverage. Mention specific tools and technologies by name in your project descriptions."
    else:
        label = "Excellent"
        tip = "Strong alignment with this role's technical vocabulary."

    return KeywordDensityResult(
        role_key=role_key,
        keywords_matched=matched,
        keywords_total=len(keywords),
        tfidf_similarity=round(tfidf_sim, 4),
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
    required = ["Name", "Email", "Summary / Objective", "Skills / Technologies", "Projects", "Education"]
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

    missing_required = [s for s in required if s not in present_set]
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_resume(
    parsed_resume: dict[str, Any],
    raw_text: str,
    role_key: str = "software_engineer",
) -> ResumeQualityScore:
    """
    Score a parsed resume across four NLP dimensions.

    Args:
        parsed_resume: The structured resume dict from resume_parser.
        raw_text:      The raw extracted PDF text (used for TF-IDF).
        role_key:      The role the candidate selected (used for keyword density).

    Returns:
        ResumeQualityScore with per-dimension scores and overall.
    """
    # Build project text from all project descriptions + outcomes
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

    # Combine raw text + skills + summary for keyword density
    skills_text = " ".join(str(s) for s in (parsed_resume.get("skills") or []))
    tech_text   = " ".join(str(t) for t in (parsed_resume.get("technologies") or []))
    summary_text = parsed_resume.get("summary") or ""
    full_text = " ".join([raw_text, skills_text, tech_text, summary_text])

    action_result       = _score_action_verbs(project_text or full_text)
    quant_result        = _score_quantification(project_text or full_text)
    keyword_result      = _score_keyword_density(full_text, role_key)
    completeness_result = _score_section_completeness(parsed_resume)

    # Weighted overall — calibrated so a solid fresher resume lands 65-75.
    # Keywords (real ATS weight), completeness, verbs, quantification.
    overall = (
        keyword_result.score      * 0.35 +
        completeness_result.score * 0.30 +
        action_result.score       * 0.25 +
        quant_result.score        * 0.10
    )
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

    # Pick most impactful tip (lowest scoring dimension)
    dims = [
        (action_result.score,       action_result.tip),
        (quant_result.score,        quant_result.tip),
        (keyword_result.score,      keyword_result.tip),
        (completeness_result.score, completeness_result.tip),
    ]
    dims.sort(key=lambda x: x[0])
    top_tip = dims[0][1]

    return ResumeQualityScore(
        overall_score=overall_pct,
        overall_label=overall_label,
        action_verbs=action_result,
        quantification=quant_result,
        keyword_density=keyword_result,
        section_completeness=completeness_result,
        top_tip=top_tip,
    )


def score_resume_as_dict(
    parsed_resume: dict[str, Any],
    raw_text: str,
    role_key: str = "software_engineer",
) -> dict[str, Any]:
    """Serialisable version of score_resume for API responses."""
    result = score_resume(parsed_resume, raw_text, role_key)
    from dataclasses import asdict
    return asdict(result)
