import { useEffect, useMemo, useState } from "react";

function formatRemaining(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "00:00";
  }
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = safeSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

export default function TimerBar({
  deadlineAt,
  stageStartedAt,
  stageLabel = "Coding timer",
  inactiveLabel = "Not armed",
  inactiveNote = "The backend has not published a deadline yet, so this timer is informational only.",
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!deadlineAt) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [deadlineAt]);

  const remainingSeconds = useMemo(() => {
    if (!deadlineAt) {
      return null;
    }
    const deadline = Date.parse(deadlineAt);
    if (Number.isNaN(deadline)) {
      return null;
    }
    return Math.max(0, Math.round((deadline - now) / 1000));
  }, [deadlineAt, now]);

  const totalSeconds = useMemo(() => {
    if (!deadlineAt || !stageStartedAt) {
      return null;
    }
    const deadline = Date.parse(deadlineAt);
    const started = Date.parse(stageStartedAt);
    if (Number.isNaN(deadline) || Number.isNaN(started) || deadline <= started) {
      return null;
    }
    return Math.round((deadline - started) / 1000);
  }, [deadlineAt, stageStartedAt]);

  const hasActiveDeadline = remainingSeconds != null;
  const fillWidth = hasActiveDeadline
    ? totalSeconds && totalSeconds > 0
      ? `${Math.max(0, Math.min(100, (remainingSeconds / totalSeconds) * 100))}%`
      : "100%"
    : "12%";

  return (
    <section className="glass-panel dsa-timer" aria-label={stageLabel}>
      <div className="dsa-timer__head">
        <div>
          <p className="section-kicker">Timer</p>
          <h2>{stageLabel}</h2>
        </div>
        <strong>{hasActiveDeadline ? formatRemaining(remainingSeconds) : inactiveLabel}</strong>
      </div>
      <div className="dsa-timer__track">
        <div className="dsa-timer__fill" style={{ width: fillWidth }} />
      </div>
      <p className="dsa-timer__note">
        {hasActiveDeadline
          ? "Backend deadline is active for this stage."
          : inactiveNote}
      </p>
    </section>
  );
}
