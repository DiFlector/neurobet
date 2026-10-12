"use client";

import * as React from "react";
import { Clock, AlertTriangle, Radio } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { EventItem } from "@/lib/types";

interface MatchTimerProps {
  event: EventItem;
  className?: string;
}

/**
 * Парсинг детального счета по сетам, геймам и периодам
 */
export function getDetailedScore(evt: EventItem): { main: string; sub?: string } {
  const cs = evt.current_score;

  if (cs && typeof cs === "object" && Object.keys(cs).length > 0) {
    // Теннис: сеты и геймы
    if (cs.sets !== undefined && cs.games !== undefined) {
      return {
        main: String(cs.sets),
        sub: `геймы: ${cs.games}`,
      };
    }
    if (cs.sets_a !== undefined && cs.sets_b !== undefined) {
      let subText = "";
      if (cs.games_a !== undefined && cs.games_b !== undefined) {
        subText = `(${cs.games_a}:${cs.games_b})`;
      }
      return {
        main: `${cs.sets_a}:${cs.sets_b}`,
        sub: subText || undefined,
      };
    }
    if (cs.score_a !== undefined && cs.score_b !== undefined) {
      return { main: `${cs.score_a}:${cs.score_b}` };
    }
    if (cs.score) return { main: String(cs.score) };
    if (cs.sets) return { main: String(cs.sets) };
  }

  if (evt.score_str) return { main: evt.score_str };
  if (evt.status === "live") {
    return { main: "1-й сет", sub: "В игре" };
  }
  if (evt.status === "finished") {
    return { main: "Окончен", sub: "Итог" };
  }
  return { main: "vs" };
}

export function MatchTimer({ event, className = "" }: MatchTimerProps) {
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
    timeElement = (
      <div className="flex items-center gap-1 text-[11px] font-semibold text-red-500 animate-pulse">
        <Radio className="h-3 w-3" />
        <span>В прямом эфире</span>
      </div>
    );
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

  const score = getDetailedScore(event);

  return (
    <div className={`flex flex-col items-center justify-center text-center ${className}`}>
      <div className="font-mono font-bold text-sm sm:text-base text-foreground px-2.5 py-0.5 rounded bg-muted/70 border border-border/50 shadow-2xs">
        {score.main}
      </div>
      {score.sub && (
        <div className="text-[10px] font-mono text-emerald-600 dark:text-emerald-400 mt-0.5 font-medium">
          {score.sub}
        </div>
      )}
      <div className="mt-1">{timeElement}</div>
    </div>
  );
}
