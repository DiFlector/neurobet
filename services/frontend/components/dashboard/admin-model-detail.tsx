"use client";

import * as React from "react";
import { getApiBase } from "@/lib/api";

interface PeriodStats {
  committed: number;
  settled: number;
  wins: number;
  losses: number;
  voids: number;
  pending: number;
  win_rate: number | null;
  match_settled: number;
  match_wins: number;
  match_win_rate: number | null;
  set_settled: number;
  set_wins: number;
  set_win_rate: number | null;
  avg_probability: number | null;
  avg_odds: number | null;
  avg_edge: number | null;
  avg_expected_value: number | null;
  curve: { bets: number; wins: number }[];
}

interface JournalStats {
  is_composite?: boolean;
  periods: {
    all: PeriodStats;
    week: PeriodStats;
    day: PeriodStats;
  };
}

const PERIODS = [
  { id: "all", label: "Всё время" },
  { id: "week", label: "Неделя" },
  { id: "day", label: "День" },
] as const;

function percent(rate: number | null, settled: number): string {
  if (!settled || rate === null) return "—";
  return `${Math.round(rate * 100)}%`;
}

function numberOrDash(value: number | null, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

function WinCurve({ points }: { points: { bets: number; wins: number }[] }) {
  if (points.length === 0) {
    return (
      <p className="py-8 text-center text-[11px] text-muted-foreground">
        Решённых ставок в этом окне ещё нет.
      </p>
    );
  }
  const width = 640;
  const height = 180;
  const pad = { left: 36, right: 12, top: 12, bottom: 28 };
  const last = points[points.length - 1];
  const maxBets = Math.max(last.bets, 1);
  const maxWins = maxBets;
  const xOf = (bets: number) => pad.left + ((width - pad.left - pad.right) * bets) / maxBets;
  const yOf = (wins: number) => pad.top + (height - pad.top - pad.bottom) * (1 - wins / maxWins);
  const line = points.map((point) => `${xOf(point.bets)},${yOf(point.wins)}`).join(" ");
  const half = `M ${xOf(0)} ${yOf(0)} L ${xOf(maxBets)} ${yOf(maxBets / 2)}`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-44 w-full" role="img" aria-label="Накопленные победы по числу ставок">
      <line x1={pad.left} y1={pad.top} x2={pad.left} y2={height - pad.bottom} className="stroke-border" />
      <line x1={pad.left} y1={height - pad.bottom} x2={width - pad.right} y2={height - pad.bottom} className="stroke-border" />
      <path d={half} className="fill-none stroke-muted-foreground/40" strokeDasharray="4 4" />
      <polyline points={`${xOf(0)},${yOf(0)} ${line}`} className="fill-none stroke-emerald-500" strokeWidth="2" />
      <text x={pad.left} y={12} className="fill-muted-foreground text-[10px]">
        {maxWins}
      </text>
      <text x={4} y={height / 2} className="fill-muted-foreground text-[10px]">
        Победы
      </text>
      <text x={width - pad.right} y={height - 8} textAnchor="end" className="fill-muted-foreground text-[10px]">
        Ставки {maxBets}
      </text>
    </svg>
  );
}

function PeriodCard({ title, stats }: { title: string; stats: PeriodStats }) {
  return (
    <div className="rounded-md border border-border/60 bg-background/60 p-2">
      <div className="mb-1 text-[11px] font-semibold text-foreground">{title}</div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
        <dt className="text-muted-foreground">Решено</dt>
        <dd className="text-right font-mono">{stats.settled}</dd>
        <dt className="text-muted-foreground">Победы / поражения</dt>
        <dd className="text-right font-mono">
          {stats.wins} / {stats.losses}
        </dd>
        <dt className="text-muted-foreground">Победы / поражения в матче</dt>
        <dd className="text-right font-mono">
          {stats.match_wins} / {stats.match_settled - stats.match_wins}
        </dd>
        <dt className="text-muted-foreground">Победы / поражения в сете</dt>
        <dd className="text-right font-mono">
          {stats.set_wins} / {stats.set_settled - stats.set_wins}
        </dd>
        <dt className="text-muted-foreground">Матч</dt>
        <dd className="text-right font-mono">{percent(stats.match_win_rate, stats.match_settled)}</dd>
        <dt className="text-muted-foreground">Сет</dt>
        <dd className="text-right font-mono">{percent(stats.set_win_rate, stats.set_settled)}</dd>
        <dt className="text-muted-foreground">Общий %</dt>
        <dd className="text-right font-mono">{percent(stats.win_rate, stats.settled)}</dd>
        <dt className="text-muted-foreground">Открытые / возвраты</dt>
        <dd className="text-right font-mono">
          {stats.pending} / {stats.voids}
        </dd>
        <dt className="text-muted-foreground">Средняя вероятность</dt>
        <dd className="text-right font-mono">{numberOrDash(stats.avg_probability, 3)}</dd>
        <dt className="text-muted-foreground">Средний коэффициент</dt>
        <dd className="text-right font-mono">{numberOrDash(stats.avg_odds)}</dd>
        <dt className="text-muted-foreground">Средний перевес</dt>
        <dd className="text-right font-mono">{numberOrDash(stats.avg_edge, 3)}</dd>
        <dt className="text-muted-foreground">Средний EV</dt>
        <dd className="text-right font-mono">{numberOrDash(stats.avg_expected_value, 3)}</dd>
      </dl>
    </div>
  );
}

export function AdminModelDetail({ modelId, composite }: { modelId: string; composite: boolean }) {
  const [stats, setStats] = React.useState<JournalStats | null>(null);
  const [failed, setFailed] = React.useState(false);
  const [period, setPeriod] = React.useState<(typeof PERIODS)[number]["id"]>("all");

  React.useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch(`${getApiBase()}/models/${modelId}/journal-stats`, { cache: "no-store" })
        .then(async (res) => {
          if (!res.ok) throw new Error(String(res.status));
          return res.json();
        })
        .then((data: JournalStats) => {
          if (!cancelled) {
            setStats(data);
            setFailed(false);
          }
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [modelId]);

  if (failed && !stats) {
    return <p className="px-3 py-4 text-xs text-red-500">Не удалось загрузить статистику модели.</p>;
  }
  if (!stats) {
    return <p className="px-3 py-4 text-xs text-muted-foreground">Считаю журнал…</p>;
  }
  const selected = stats.periods[period];
  return (
    <div className="space-y-3 px-3 py-3">
      {composite && (
        <p className="text-[10px] text-muted-foreground">
          Составная модель показана целиком: в журнал пишется одна сторона, части не разделяются.
        </p>
      )}
      <div className="grid gap-2 md:grid-cols-3">
        <PeriodCard title="Всё время" stats={stats.periods.all} />
        <PeriodCard title="Неделя" stats={stats.periods.week} />
        <PeriodCard title="День" stats={stats.periods.day} />
      </div>
      <div className="rounded-md border border-border/60 bg-background/60 p-2">
        <div className="mb-1 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-semibold text-foreground">Победы по ставкам</span>
          {PERIODS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`rounded px-2 py-0.5 text-[10px] ${period === item.id ? "bg-foreground text-background" : "bg-muted text-muted-foreground"}`}
              onClick={() => setPeriod(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <WinCurve points={selected.curve} />
        <p className="text-[10px] text-muted-foreground">
          По вертикали накопленные победы, по горизонтали число решённых ставок. Пунктир — половина побед. График обновляется каждые 5 секунд.
        </p>
      </div>
    </div>
  );
}
