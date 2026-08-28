export default function RoundStatusPage({
  kicker,
  title,
  description,
  statusLabel,
  statusTone = "checking",
  cards,
  note,
  previewTitle,
  previewDescription,
  previewChecklist = [],
  primaryAction,
  secondaryAction,
  extraActions,
  onNavigate,
}) {
  return (
    <div className="page-shell">
      <section className="page-heading preview-heading">
        <section className="glass-panel placeholder-panel">
          <div className="panel-head">
            <div>
              <p className="section-kicker">{kicker}</p>
              <h2>{title}</h2>
            </div>
            <span className={`status-pill status-pill--${statusTone}`}>{statusLabel}</span>
          </div>

          <p className="hero-text">{description}</p>

          <div className="hero-tags preview-tags">
            <span>{statusLabel}</span>
            <span>Preview</span>
            {primaryAction ? <span>{primaryAction.label}</span> : null}
          </div>

          {primaryAction || secondaryAction || extraActions ? (
            <div className="action-row preview-actions">
              {primaryAction ? (
                <button
                  type="button"
                  className="primary-button action-row__button"
                  onClick={() => onNavigate?.(primaryAction.target)}
                  disabled={!onNavigate}
                >
                  {primaryAction.label}
                </button>
              ) : null}
              {secondaryAction ? (
                <button
                  type="button"
                  className="secondary-button action-row__button"
                  onClick={() => onNavigate?.(secondaryAction.target)}
                  disabled={!onNavigate}
                >
                  {secondaryAction.label}
                </button>
              ) : null}
              {extraActions}
            </div>
          ) : null}
        </section>

        <aside className="glass-panel preview-aside">
          <span>Next</span>
          <strong>{previewTitle}</strong>
          {previewDescription ? <p>{previewDescription}</p> : null}

          {previewChecklist.length ? (
            <ul className="preview-list">
              {previewChecklist.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </aside>
      </section>

      <div className="value-grid placeholder-card-grid">
        {cards.map((card) => (
          <article key={card.title} className="value-card placeholder-card">
            <span>{card.title}</span>
            <strong>{card.headline}</strong>
            <p>{card.body}</p>
          </article>
        ))}
      </div>

      {note ? (
        <div className="message-stack">
          <p className="info-banner">{note}</p>
        </div>
      ) : null}
    </div>
  );
}