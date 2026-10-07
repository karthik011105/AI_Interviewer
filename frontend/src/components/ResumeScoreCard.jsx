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

  // A small fixed data-viz palette, deliberately not the app's CSS variables:
  // this ring/badge always needs white text on top of it, and the dark-theme
  // accent/warning tokens are too light for that contrast. Chosen to match
  // the light-theme tokens in styles.css so it still reads as one system.
  const ringColor =
    pct >= 80 ? "#2e7d5b" :
    pct >= 65 ? "#33409e" :
    pct >= 45 ? "#a66a1e" : "#ae3b3b";

  function dimColor(p) {
    return p >= 75 ? "#2e7d5b" : p >= 55 ? "#33409e" : p >= 35 ? "#a66a1e" : "#ae3b3b";
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
      <div className="ats-header">
        {/* Animated SVG ring */}
        <div className="ats-ring-wrap">
          <svg width="130" height="130" viewBox="0 0 130 130" aria-hidden="true">
            {/* Track */}
            <circle cx="65" cy="65" r={R} fill="none" stroke="var(--border)" strokeWidth="10" />
            {/* Animated progress arc */}
            <circle
              cx="65" cy="65" r={R} fill="none"
              stroke={ringColor}
              strokeWidth="10"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={animatedOffset}
              transform="rotate(-90 65 65)"
              style={{ transition: revealed ? "stroke-dashoffset 1.2s cubic-bezier(0.34,1.56,0.64,1)" : "none" }}
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
              <span className="ats-stat-card__val" style={{ color: "var(--success)" }}>{action_verbs?.strong_count ?? "—"}</span>
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
                    background: pass ? "var(--success-bg)" : "var(--danger-bg)",
                    color: pass ? "var(--success)" : "var(--danger)",
                    border: `1px solid ${pass ? "var(--success)" : "var(--danger)"}`,
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
                    background: color,
                    transition: revealed ? `width 0.9s cubic-bezier(0.34,1.2,0.64,1) ${i * 80}ms` : "none",
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
