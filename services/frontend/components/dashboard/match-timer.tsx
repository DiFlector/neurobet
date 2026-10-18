"use client";

import * as React from "react";
import { Clock, AlertTriangle } from "lucide-react";
import { EventItem } from "@/lib/types";
import { FitText } from "@/components/dashboard/fit-text";

interface MatchTimerProps {
  event: EventItem;
  className?: string;
  betKind?: StakeKind;
  market?: string;
}

function asText(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return undefined;
}

export type StakeKind = "match" | "set";

export function stakeKindFromMarket(market?: string): StakeKind {
  const name = (market || "match_winner").toLowerCase();
  if (name.includes("set") && !name.includes("match")) return "set";
  return "match";
}

function completedSets(score: Record<string, any> | undefined): Array<{ games_a: number; games_b: number }> {
  const sets = score?.sets;
  if (!Array.isArray(sets)) return [];
  return sets.filter(
    (row) => row && typeof row === "object" && "games_a" in row && "games_b" in row
  ) as Array<{ games_a: number; games_b: number }>;
}

/** Одна пара скобок с очками текущего гейма — как у Fonbet: (00-00*). */
function wrapDetail(detail: string): string {
  const cleaned = detail
    .replace(/тай[\s-]*брейк/gi, " ")
    .replace(/очки\s*:/gi, " ")
    .replace(/[()]/g, " ")
    .trim();
  const pointPairs = cleaned.match(/(?:AD|\d+)\*?-(?:AD|\d+)\*?/gi);
  if (pointPairs && pointPairs.length > 0) {
    return `(${pointPairs[pointPairs.length - 1]})`;
  }
  const tokens = cleaned
    .split(/\s+/)
    .map((part) => part.trim())
    .filter((part) => /[0-9A]/i.test(part));
  if (tokens.length === 0) return "";
  return `(${tokens[tokens.length - 1]})`;
}

function setLineText(score: Record<string, any> | undefined): string | undefined {
  const lines = score?.stats?.set_lines;
  if (!Array.isArray(lines) || lines.length === 0) return undefined;
  const text = lines
    .filter((line) => line && line.games_a != null && line.games_b != null)
    .map((line) => {
      const games = `${line.games_a}:${line.games_b}`;
      const extra = line.live && line.detail ? wrapDetail(String(line.detail)) : "";
      return extra ? `${games} ${extra}` : games;
    })
    .join("\n");
  return text || undefined;
}

/** Счёт под подпись ставки: в рамке — то, на что ставится, снизу — остальные сеты. */
export function getStakeScore(
  evt: EventItem,
  kind: StakeKind,
  market?: string,
): { stake: string; main: string; below?: string } {
  const cs = evt.current_score;
  const rows = completedSets(cs);
  const lines = setLineText(cs);
  const setLines = Array.isArray(cs?.stats?.set_lines) ? cs.stats.set_lines : [];
  const live = setLines.find((line: { live?: boolean }) => line?.live);
  const finished = setLines.length
    ? setLines
        .filter((line: { live?: boolean }) => line && !line.live)
        .map((line: { games_a: number; games_b: number }) => `${line.games_a}:${line.games_b}`)
        .join("\n")
    : rows.map((row) => `${row.games_a}:${row.games_b}`).join("\n");

  if (kind === "set") {
    const setNumber = /^set_winner:(\d+)$/.exec(market || "")?.[1];
    const current = live ? `${live.games_a}:${live.games_b}` : "0:0";
    const points = live?.detail ? wrapDetail(String(live.detail)) : undefined;
    return {
      stake: setNumber ? `Победа в сете ${setNumber}` : "Победа в сете",
      main: current,
      below: [finished, points].filter(Boolean).join("\n") || undefined,
    };
  }

  const wonA = cs?.stats?.sets_a != null
    ? Number(cs.stats.sets_a)
    : rows.filter((row) => Number(row.games_a) > Number(row.games_b)).length;
  const wonB = cs?.stats?.sets_b != null
    ? Number(cs.stats.sets_b)
    : rows.filter((row) => Number(row.games_b) > Number(row.games_a)).length;
  return {
    stake: "Победа в матче",
    main: `${wonA}:${wonB}`,
    below: lines || finished || undefined,
  };
}

function formatSetList(sets: unknown): { main: string; sub?: string; label?: string } | undefined {
  const rows = completedSets({ sets });
  if (rows.length === 0) return undefined;
  const wonA = rows.filter((row) => Number(row.games_a) > Number(row.games_b)).length;
  const wonB = rows.filter((row) => Number(row.games_b) > Number(row.games_a)).length;
  return {
    main: `${wonA}:${wonB}`,
    sub: rows.map((row) => `${row.games_a}-${row.games_b}`).join(" "),
  };
}

/**
 * Парсинг детального счета по сетам, геймам и периодам.
 * В current_score лежит объект состояния, его нельзя печатать через String().
 */
export function getDetailedScore(evt: EventItem): { main: string; sub?: string; label?: string } {
  const cs = evt.current_score;

  if (cs && typeof cs === "object" && Object.keys(cs).length > 0) {
    const listed = formatSetList(cs.sets);
    if (listed) {
      const game = cs.current_game;
      const pointsA = asText(game?.points_a);
      const pointsB = asText(game?.points_b);
      if (pointsA && pointsB && !(pointsA === "0" && pointsB === "0")) {
        listed.sub = listed.sub ? `${listed.sub} · ${pointsA}-${pointsB}` : `${pointsA}-${pointsB}`;
      }
      return listed;
    }
    if (typeof cs.sets === "string" && cs.games != null && typeof cs.games !== "object") {
      return {
        main: cs.sets,
        sub: `геймы: ${cs.games}`,
      };
    }
    if (cs.sets_a !== undefined && cs.sets_b !== undefined && typeof cs.sets_a !== "object") {
      let subText = "";
      if (cs.games_a !== undefined && cs.games_b !== undefined && typeof cs.games_a !== "object") {
        subText = `(${cs.games_a}:${cs.games_b})`;
      }
      return {
        main: `${cs.sets_a}:${cs.sets_b}`,
        sub: subText || undefined,
      };
    }
    if (cs.score_a !== undefined && cs.score_b !== undefined && typeof cs.score_a !== "object") {
      return { main: `${cs.score_a}:${cs.score_b}` };
    }
    const plainScore = asText(cs.score) || asText(cs.sets);
    if (plainScore) return { main: plainScore };
    if (typeof cs.current_period === "number" && cs.current_period > 0) {
      return { main: "0:0", sub: `${cs.current_period}-й сет`, label: "сеты" };
    }
  }

  if (evt.score_str) return { main: evt.score_str };
  if (evt.status === "live") {
    return { main: "0:0", sub: "1-й сет", label: "сеты" };
  }
  if (evt.status === "finished") {
    return { main: "Окончен", sub: "Итог" };
  }
  return { main: "vs" };
}

export function MatchTimer({ event, className = "", betKind = "match", market }: MatchTimerProps) {
  const [now, setNow] = React.useState<Date>(new Date());

  // Обновление локального времени каждые 15 секунд для актуального обратного отсчета / задержки
  React.useEffect(() => {
    const timer = setInterval(() => {
      setNow(new Date());
    }, 15000);
    return () => clearInterval(timer);
  }, []);

  const isLive = event.status === "live";
  const isPrematch = event.status === "prematch";
  const isFinished = event.status === "finished";

  // Расчет статуса времени
  let timeElement: React.ReactNode = null;

  if (isLive) {
    timeElement = null;
  } else if (isPrematch && event.scheduled_start_at) {
    try {
      const start = new Date(event.scheduled_start_at);
      const diffMs = start.getTime() - now.getTime();
      const diffMin = Math.round(diffMs / 60000);

      if (diffMin < 0) {
        // Матч должен был начаться в прошлом, но всё еще прематч -> задержка
        const delayMin = Math.abs(diffMin);
        const delayText =
          delayMin > 60
            ? `~${Math.floor(delayMin / 60)} ч ${delayMin % 60} мин`
            : `~${delayMin} мин`;

        timeElement = (
          <div
            className="flex items-center gap-1 text-[10px] text-amber-500 font-medium"
            title={`Запланированное начало: ${start.toLocaleTimeString("ru-RU", {
              hour: "2-digit",
              minute: "2-digit",
            })}`}
          >
            <AlertTriangle className="h-3 w-3 shrink-0" />
            <span>Задерживается ({delayText})</span>
          </div>
        );
      } else if (diffMin <= 120) {
        // Начнется скоро (в пределах 2 часов)
        timeElement = (
          <div className="flex items-center gap-1 text-[10px] text-blue-500 font-medium">
            <Clock className="h-3 w-3 shrink-0" />
            <span>Через {diffMin} мин</span>
          </div>
        );
      } else {
        // Обычное время старта
        timeElement = (
          <div className="text-[10px] text-muted-foreground font-mono">
            Начало{" "}
            {start.toLocaleTimeString("ru-RU", {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </div>
        );
      }
    } catch {
      timeElement = <span className="text-[10px] text-muted-foreground">Прематч</span>;
    }
  } else if (isFinished) {
    timeElement = <span className="text-[10px] text-muted-foreground">Матч завершен</span>;
  }

  const score = getStakeScore(event, betKind, market);

  return (
    <div className={`flex w-full min-w-0 max-w-[7.5rem] flex-col items-center justify-center overflow-hidden text-center ${className}`}>
      <div className="text-[10px] font-medium leading-tight text-muted-foreground">Ставка</div>
      <div className="mb-1 text-[10px] font-semibold leading-tight text-foreground">{score.stake}</div>
      <div className="w-full rounded border border-border/50 bg-muted/70 px-1 py-0.5 font-mono font-bold text-foreground shadow-2xs">
        <FitText className="text-center" maxPx={14} minPx={11}>
          {score.main}
        </FitText>
      </div>
      <div className="mt-0.5 min-h-3 w-full whitespace-pre-line font-mono text-[10px] font-medium leading-tight text-emerald-600 dark:text-emerald-400">
        {score.below || null}
      </div>
      {timeElement ? <div className="mt-1 w-full">{timeElement}</div> : null}
    </div>
  );
}
