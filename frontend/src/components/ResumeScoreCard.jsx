import { useEffect, useRef, useState } from "react";

/**
 * ResumeScoreCard — Animated AI-powered ATS resume score card.
 * Modelled after Jobscan / Resume Worded visual language.
 */
export default function ResumeScoreCard({ score }) {
  const [revealed, setRevealed] = useState(false);
  const [displayScore, setDisplayScore] = useState(0);
  const ref = useRef(null);

  const pct = Math.round(score?.overall_score ?? 0);

  // Intersection observer — trigger once the card enters view
  useEffect(() => {
    if (!score) return;
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setRevealed(true);
        obs.disconnect();
      }
    }, { threshold: 0.15 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [score]);

  // Count-up animation for the ring number
  useEffect(() => {
    if (!revealed) return;
    let frame;
    const duration = 1200;
    const start = performance.now();
    function tick(now) {
      const t = Math.min((now - start) / duration, 1);
      const ease = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplayScore(Math.round(ease * pct));
      if (t < 1) frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [revealed, pct]);

  if (!score) return null;

  const { overall_label, action_verbs, quantification, keyword_density, section_completeness } = score;

  const ringColor =
    pct >= 80 ? "#22c55e" :
    pct >= 65 ? "#3b82f6" :
    pct >= 45 ? "#f59e0b" : "#ef4444";

  const ringGlow =
    pct >= 80 ? "0 0 24px rgba(34,197,94,0.35)" :
    pct >= 65 ? "0 0 24px rgba(59,130,246,0.35)" :
    pct >= 45 ? "0 0 24px rgba(245,158,11,0.30)" : "0 0 24px rgba(239,68,68,0.30)";

  function dimColor(p) {
    return p >= 75 ? "#22c55e" : p >= 55 ? "#3b82f6" : p >= 35 ? "#f59e0b" : "#ef4444";
  }

  const R = 52;
  const circumference = 2 * Math.PI * R;
  const animatedOffset = revealed
    ? circumference * (1 - pct / 100)
    : circumference;

  const dims = [
    { label: "Action Verbs",   icon: "⚡", pct: Math.round((action_verbs?.score ?? 0) * 100),   tip: action_verbs?.tip },
    { label: "Quantification", icon: "📊", pct: Math.round((quantification?.score ?? 0) * 100), tip: quantification?.tip },
    { label: "Role Keywords",  icon: "🔑", pct: Math.round((keyword_density?.score ?? 0) * 100), tip: keyword_density?.tip },
    { label: "Completeness",   icon: "✅", pct: Math.round((section_completeness?.score ?? 0) * 100), tip: section_completeness?.tip },
  ];

  const kw = keyword_density;

  return (
    <section ref={ref} className="ats-score-card glass-panel" aria-label="ATS resume score">
      {/* Glowing header strip */}
      <div className="ats-header">
        {/* Animated SVG ring */}
        <div className="ats-ring-wrap">
          <svg width="130" height="130" viewBox="0 0 130 130" aria-hidden="true">
            {/* Gradient definition */}
            <defs>
              <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor={ringColor} stopOpacity="1" />
                <stop offset="100%" stopColor={ringColor} stopOpacity="0.5" />
              </linearGradient>
            </defs>
            {/* Track */}
            <circle cx="65" cy="65" r={R} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="10" />
            {/* Animated progress arc */}
            <circle
              cx="65" cy="65" r={R} fill="none"
              stroke="url(#ringGrad)"
              strokeWidth="10"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={animatedOffset}
              transform="rotate(-90 65 65)"
              style={{ transition: revealed ? "stroke-dashoffset 1.2s cubic-bezier(0.34,1.56,0.64,1)" : "none",
                       filter: revealed ? `drop-shadow(${ringGlow})` : "none" }}
            />
          </svg>
          <div className="ats-ring-center">
            <span className="ats-ring-number" style={{ color: ringColor }}>{displayScore}</span>
            <span className="ats-ring-sub">out of 100</span>
            <span className="ats-ring-badge" style={{ background: ringColor }}>{overall_label}</span>
          </div>
        </div>

        {/* Right: title + headline stats */}
        <div className="ats-stats">
          <div className="ats-title-row">
            <span className="ats-ai-tag">AI Powered</span>
            <h2 className="ats-title">Resume Score</h2>
            <p className="ats-subtitle">Analysed against industry ATS criteria for your target role.</p>
          </div>
          <div className="ats-stat-grid">
            <div className="ats-stat-card">
              <span className="ats-stat-card__val" style={{ color: ringColor }}>{kw?.keywords_matched ?? "—"}</span>
              <span className="ats-stat-card__key">Keywords matched</span>
            </div>
            <div className="ats-stat-card">
              <span className="ats-stat-card__val">{kw?.keywords_total ?? "—"}</span>
              <span className="ats-stat-card__key">Role keywords</span>
            </div>
            <div className="ats-stat-card">
              <span className="ats-stat-card__val" style={{ color: "#22c55e" }}>{action_verbs?.strong_count ?? "—"}</span>
              <span className="ats-stat-card__key">Strong verbs</span>
            </div>
            <div className="ats-stat-card">
              <span className="ats-stat-card__val">{quantification?.quantified_bullets ?? "—"}</span>
              <span className="ats-stat-card__key">Quantified bullets</span>
            </div>
          </div>
        </div>
      </div>

      {/* Divider */}
      <div className="ats-divider" />

      {/* Category rows */}
      <div className="ats-categories">
        {dims.map((d, i) => {
          const color = dimColor(d.pct);
          const pass = d.pct >= 55;
          return (
            <div
              key={d.label}
              className="ats-category"
              style={{ animationDelay: revealed ? `${i * 80}ms` : "0ms" }}
              data-revealed={revealed}
            >
              <div className="ats-category__top">
                <div className="ats-category__left">
                  <span className="ats-category__emoji">{d.icon}</span>
                  <span className="ats-category__name">{d.label}</span>
                  <span className="ats-category__badge" style={{
                    background: pass ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)",
                    color: pass ? "#22c55e" : "#ef4444",
                    border: `1px solid ${pass ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}`,
                  }}>
                    {pass ? "Passed" : "Improve"}
                  </span>
                </div>
                <span className="ats-category__pct" style={{ color }}>{d.pct}%</span>
              </div>
              <div className="ats-bar-track">
                <div
                  className="ats-bar-fill"
                  style={{
                    width: revealed ? `${d.pct}%` : "0%",
                    background: `linear-gradient(90deg, ${color}cc, ${color})`,
                    transition: revealed ? `width 0.9s cubic-bezier(0.34,1.2,0.64,1) ${i * 80}ms` : "none",
                    boxShadow: revealed ? `0 0 8px ${color}55` : "none",
                  }}
                />
              </div>
              {d.tip && <p className="ats-category__tip">{d.tip}</p>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
