"use client";

import * as React from "react";

interface MatchClockProps {
  score?: Record<string, any> | null;
}

function formatMinutes(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${minutes}:${ss}`;
}

function elapsedSeconds(clock: Record<string, any>, nowMs: number): number {
  const base = Number(clock.base_seconds) || 0;
  if (!clock.running) return base;
  const updated = Number(clock.updated_at_ms) || nowMs;
  return base + Math.max(0, (nowMs - updated) / 1000);
}

/** Время матча с линии Fonbet. На паузе цифра не идёт. */
export function MatchClock({ score }: MatchClockProps) {
  const clock = score?.stats?.match_clock;
  const [nowMs, setNowMs] = React.useState(() => Date.now());

  React.useEffect(() => {
    if (!clock?.running) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [clock?.running]);

  if (!clock || clock.base_seconds == null) return null;

  const text = formatMinutes(elapsedSeconds(clock, nowMs));
  if (clock.paused) {
    return (
      <span
        className="whitespace-nowrap font-mono text-[11px] font-semibold text-amber-500"
        title={clock.label ? `Fonbet: ${clock.label}` : "Матч на паузе"}
      >
        На паузе {text}
      </span>
    );
  }

  return (
    <span className="whitespace-nowrap font-mono text-[11px] font-semibold text-muted-foreground" title="Время матча с Fonbet">
      {text}
    </span>
  );
}
