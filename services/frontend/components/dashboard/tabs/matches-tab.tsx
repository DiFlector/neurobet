"use client";

import * as React from "react";
import {
  Search,
  Radio,
  Calendar,
  CheckCircle2,
  Trophy,
  Loader2,
  BrainCircuit,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { EventItem, PredictionItem, SportItem } from "@/lib/types";
import { getLocalizedParticipantName } from "@/lib/locale-names";
import { MatchCard } from "./match-card";

interface MatchesTabProps {
  events: EventItem[];
  predictions: Record<string, PredictionItem[]>;
  sports: SportItem[];
  activeSport: string;
  onSelectSport: (sportCode: string) => void;
  activeStatus: string;
  onSelectStatus: (status: string) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onInspectEvent: (eventId: string) => void;
  loading?: boolean;
}

export function MatchesTab({
  events,
  predictions,
  sports,
  activeSport,
  onSelectSport,
  activeStatus,
  onSelectStatus,
  searchQuery,
  onSearchChange,
  onInspectEvent,
  loading = false,
}: MatchesTabProps) {
  // Режим отображения: по умолчанию (false) показываются только ставки с прогнозом.
  // При включении тумблера «Показывать все» (true) — выводятся все матчи.
  const [showAllMatches, setShowAllMatches] = React.useState(false);

  // Available fallback sports if none loaded from API yet
  const sportList =
    sports.length > 0
      ? sports
      : [
          { code: "tennis", name: "Tennis", name_ru: "Теннис" },
          { code: "football", name: "Football", name_ru: "Футбол" },
          { code: "basketball", name: "Basketball", name_ru: "Баскетбол" },
          { code: "hockey", name: "Hockey", name_ru: "Хоккей" },
          { code: "volleyball", name: "Volleyball", name_ru: "Волейбол" },
        ];

  // Дедупликация и фильтрация событий
  const deduplicatedEvents = React.useMemo(() => {
    const seen = new Set<string>();
    const now = Date.now();

    return events.filter((e) => {
      // Ключ для исключения повторных дублей пар игроков
      const pA = (e.participant_a || e.participant_a_name || "").trim().toLowerCase();
      const pB = (e.participant_b || e.participant_b_name || "").trim().toLowerCase();
      const pairKey = `${e.sport_code}_${pA}_${pB}`;

      if (seen.has(pairKey)) {
        return false;
      }
      seen.add(pairKey);

      // Исключаем устаревшие прематч-события, которые не начались (задерживаются более чем на 30 мин)
      // Так как ставим строго на события, которые сейчас идут в Live
      if (e.status === "prematch" && e.scheduled_start_at) {
        const startTime = new Date(e.scheduled_start_at).getTime();
        if (now - startTime > 30 * 60 * 1000) {
          // Если задерживается более 30 минут и до сих пор не live — скрываем
          return false;
        }
      }

      return true;
    });
  }, [events]);

  const eventsWithPredictionsCount = React.useMemo(() => {
    return deduplicatedEvents.filter(
      (e) => predictions[e.id] && predictions[e.id].length > 0
    ).length;
  }, [deduplicatedEvents, predictions]);

  const filteredEvents = React.useMemo(() => {
    let list = deduplicatedEvents;

    // Фильтр по статусу
    if (activeStatus === "live") {
      list = list.filter((e) => e.is_live || e.status === "live");
    } else if (activeStatus === "prematch") {
      list = list.filter((e) => !e.is_live && e.status === "prematch");
    } else if (activeStatus === "finished") {
      list = list.filter((e) => e.status === "finished");
    }

    // По умолчанию (showAllMatches === false): показывать ТОЛЬКО ставки с прогнозом ML
    // Если включено (showAllMatches === true): показывать абсолютно все матчи
    if (!showAllMatches) {
      list = list.filter(
        (e) => predictions[e.id] && predictions[e.id].length > 0
      );
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      list = list.filter((e) => {
        const rawA = (e.participant_a || e.participant_a_name || "").toLowerCase();
        const rawB = (e.participant_b || e.participant_b_name || "").toLowerCase();
        const locA = getLocalizedParticipantName(
          e.participant_a || e.participant_a_name,
          e.sport_code
        ).toLowerCase();
        const locB = getLocalizedParticipantName(
          e.participant_b || e.participant_b_name,
          e.sport_code
        ).toLowerCase();
        return (
          rawA.includes(q) ||
          rawB.includes(q) ||
          locA.includes(q) ||
          locB.includes(q) ||
          (e.source_event_id && e.source_event_id.toLowerCase().includes(q))
        );
      });
    }

    return list;
  }, [
    deduplicatedEvents,
    predictions,
    showAllMatches,
    searchQuery,
    activeStatus,
  ]);

  return (
    <div className="space-y-4">
      {/* Filters Toolbar */}
      <div className="flex flex-col gap-3.5 p-3.5 rounded-lg border border-border/60 bg-card/60 backdrop-blur">
        {/* Top row: Sports buttons (Left), Extended Search (Center-right), Toggle Switch (Right) */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
          {/* Sports Selector */}
          <div className="flex items-center gap-1.5 flex-wrap shrink-0">
            {sportList.map((s) => {
              const isActive = activeSport === s.code;
              return (
                <Button
                  key={s.code}
                  variant={isActive ? "default" : "outline"}
                  size="sm"
                  className={`h-8 px-3 text-xs font-medium transition-all ${
                    isActive
                      ? "bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm"
                      : "border-border/60 text-muted-foreground hover:text-foreground hover:bg-muted"
                  }`}
                  onClick={() => onSelectSport(s.code)}
                >
                  {s.name_ru || s.name || s.code}
                </Button>
              );
            })}
          </div>

          {/* Extended Search Input */}
          <div className="relative flex-1 max-w-none sm:max-w-md">
            <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Поиск по турниру, игроку, команде или ID..."
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              className="h-8 pl-8.5 text-xs bg-background/80 border-border/60 w-full"
            />
          </div>

          {/* Toggle Switch: Показывать все */}
          <div className="flex items-center gap-2.5 px-3 py-1 rounded-md border border-border/60 bg-background/60 self-start sm:self-auto shrink-0 shadow-2xs">
            <BrainCircuit className="h-4 w-4 text-emerald-500" />
            <label
              htmlFor="show-all-switch"
              className="text-xs font-medium cursor-pointer text-muted-foreground hover:text-foreground select-none"
              title="Выключено (по умолчанию): только ставки с прогнозом ML. Включено: показывать абсолютно все матчи (включая ожидающие анализа)."
            >
              Показывать все
            </label>
            <Switch
              id="show-all-switch"
              checked={showAllMatches}
              onCheckedChange={setShowAllMatches}
            />
            <Badge
              variant="secondary"
              className={`text-[10px] px-1.5 py-0 h-4 font-mono font-bold ${
                showAllMatches
                  ? "bg-emerald-600 text-white"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {showAllMatches ? deduplicatedEvents.length : eventsWithPredictionsCount}
            </Badge>
          </div>
        </div>

        {/* Center row: Status Buttons ('Все', 'В игре', 'Предстоящие', 'Завершенные') — STRICTLY CENTERED */}
        <div className="flex items-center justify-center pt-1 border-t border-border/40">
          <div className="inline-flex items-center gap-1 p-1 rounded-lg bg-muted/40 border border-border/40 shadow-2xs">
            <Button
              variant={activeStatus === "all" ? "secondary" : "ghost"}
              size="sm"
              className={`h-7 px-3 text-xs font-medium ${
                activeStatus === "all" ? "bg-background shadow-xs font-semibold" : ""
              }`}
              onClick={() => onSelectStatus("all")}
            >
              Все
            </Button>
            <Button
              variant={activeStatus === "live" ? "secondary" : "ghost"}
              size="sm"
              className={`h-7 px-3 text-xs font-medium gap-1.5 ${
                activeStatus === "live"
                  ? "bg-background shadow-xs text-red-600 dark:text-red-400 font-semibold"
                  : ""
              }`}
              onClick={() => onSelectStatus("live")}
            >
              <Radio className="h-3 w-3 text-red-500" />
              В игре (Live)
            </Button>
            <Button
              variant={activeStatus === "prematch" ? "secondary" : "ghost"}
              size="sm"
              className={`h-7 px-3 text-xs font-medium gap-1.5 ${
                activeStatus === "prematch"
                  ? "bg-background shadow-xs text-blue-600 dark:text-blue-400 font-semibold"
                  : ""
              }`}
              onClick={() => onSelectStatus("prematch")}
            >
              <Calendar className="h-3 w-3 text-blue-500" />
              Предстоящие
            </Button>
            <Button
              variant={activeStatus === "finished" ? "secondary" : "ghost"}
              size="sm"
              className={`h-7 px-3 text-xs font-medium gap-1.5 ${
                activeStatus === "finished" ? "bg-background shadow-xs font-semibold" : ""
              }`}
              onClick={() => onSelectStatus("finished")}
            >
              <CheckCircle2 className="h-3 w-3 text-muted-foreground" />
              Завершенные
            </Button>
          </div>
        </div>
      </div>

      {/* Matches Grid */}
      {loading && filteredEvents.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 text-center rounded-lg border border-border/40 bg-card/40">
          <Loader2 className="h-8 w-8 animate-spin text-emerald-500 mb-3" />
          <p className="text-sm text-muted-foreground">
            Загрузка спортивных событий и расчетных вероятностей...
          </p>
        </div>
      ) : filteredEvents.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 text-center rounded-lg border border-dashed border-border/60 bg-card/30">
          <Trophy className="h-10 w-10 text-muted-foreground/40 mb-3" />
          <h3 className="font-semibold text-base">Событий не найдено</h3>
          <p className="text-xs text-muted-foreground mt-1 max-w-sm">
            {!showAllMatches
              ? "Нет событий с рассчитанными прогнозами ML. Включите тумблер «Показывать все», чтобы увидеть события в очереди анализа."
              : "По текущим фильтрам нет доступных матчей. Ожидаем обновления данных Fonbet..."}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredEvents.map((evt) => (
            <MatchCard
              key={evt.id}
              event={evt}
              predictions={predictions[evt.id] || []}
              onInspect={onInspectEvent}
            />
          ))}
        </div>
      )}
    </div>
  );
}
