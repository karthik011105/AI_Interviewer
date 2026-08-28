export default function StageIndicator({ steps, currentStep, ariaLabel = "Stage progress" }) {
  const currentIndex = Math.max(
    0,
    steps.findIndex((step) => step.key === currentStep),
  );

  return (
    <ol className="stage-indicator" aria-label={ariaLabel}>
      {steps.map((step, index) => {
        const state = index < currentIndex ? "complete" : index === currentIndex ? "active" : "pending";
        return (
          <li key={step.key} className={`stage-item stage-item--${state}`}>
            <div className="stage-item__rail" />
            <div className="stage-item__orb">{index + 1}</div>
            <div className="stage-item__content">
              <p className="stage-item__label">{step.label}</p>
              <p className="stage-item__meta">{step.meta}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
