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
import { BetItem, EventItem, PredictionItem, SportItem } from "@/lib/types";
import { BetOutcomeTone } from "./match-card";
import { getLocalizedParticipantName } from "@/lib/locale-names";
import { isPlacedBet, placedEventIds } from "@/lib/placed-bets";
import { decisionStatus } from "@/lib/decision-status";
import { MatchCard } from "./match-card";

interface MatchesTabProps {
  events: EventItem[];
  predictions: Record<string, PredictionItem[]>;
  bets?: BetItem[];
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

function settleBetTone(eventBets: BetItem[]): BetOutcomeTone | null {
  const settled = eventBets.filter(
    (bet) => bet.status === "WON" || bet.status === "LOST" || bet.status === "VOID"
  );
  if (settled.length === 0) return null;
  const wonStake = settled
    .filter((bet) => bet.status === "WON")
    .reduce((sum, bet) => sum + Number(bet.stake || 0), 0);
  const lostStake = settled
    .filter((bet) => bet.status === "LOST")
    .reduce((sum, bet) => sum + Number(bet.stake || 0), 0);
  if (wonStake > 0 && lostStake === 0) return "won";
  if (lostStake > 0 && wonStake === 0) return "lost";
  if (wonStake === 0 && lostStake === 0) return "void";
  if (wonStake > lostStake) return "won";
  if (lostStake > wonStake) return "lost";
  return "void";
}

export function MatchesTab({
  events,
  predictions,
  bets = [],
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
  // По умолчанию видны только матчи с зарегистрированной ставкой и суммой.
  // «Показывать все» открывает вердикты без суммы.
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

  const betOutcomeByEvent = React.useMemo(() => {
    const grouped = new Map<string, BetItem[]>();
    for (const bet of bets) {
      if (!bet.event_id) continue;
      const list = grouped.get(bet.event_id) || [];
      list.push(bet);
      grouped.set(bet.event_id, list);
    }
    const tones = new Map<string, BetOutcomeTone>();
    for (const [eventId, eventBets] of grouped) {
      const tone = settleBetTone(eventBets);
      if (tone) tones.set(eventId, tone);
    }
    return tones;
  }, [bets]);

  const betEventIds = React.useMemo(() => placedEventIds(bets), [bets]);
  const betsByEvent = React.useMemo(() => {
    const grouped = new Map<string, BetItem[]>();
    for (const bet of bets) {
      if (!bet.event_id) continue;
      const list = grouped.get(bet.event_id) || [];
      list.push(bet);
      grouped.set(bet.event_id, list);
    }
    return grouped;
  }, [bets]);

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

    // Завершённый матч виден только если на него есть сумма ставки.
    list = list.filter((e) => e.status !== "finished" || betEventIds.has(e.id));

    // По умолчанию — только зарегистрированные ставки. «Показывать все» оставляет вердикты.
    if (!showAllMatches) {
      list = list.filter((e) => betEventIds.has(e.id));
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
    betEventIds,
    showAllMatches,
    searchQuery,
    activeStatus,
  ]);

  return (
    <div className="space-y-4">
      {/* Filters Toolbar */}
      <div className="flex flex-col rounded-lg border border-border/60 bg-card/60 p-3.5 pb-0 backdrop-blur">
        {/* Спорт слева, поиск по центру панели, «Показывать все» справа */}
        <div className="grid grid-cols-1 items-center gap-3 md:grid-cols-[minmax(auto,1fr)_minmax(0,42rem)_minmax(auto,1fr)]">
          {/* Sports Selector */}
          <div className="flex items-center gap-1.5 flex-wrap md:justify-self-start">
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

          {/* Extended Search Input — геометрический центр блока, как ряд статусов */}
          <div className="relative w-full md:justify-self-center">
            <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Поиск по турниру, игроку, команде или номеру..."
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              className="h-8 pl-8.5 text-xs bg-background/80 border-border/60 w-full"
            />
          </div>

          {/* Toggle Switch: Показывать все */}
          <div className="flex w-max shrink-0 items-center gap-2.5 self-start whitespace-nowrap rounded-md border border-border/60 bg-background/60 px-3 py-1 shadow-2xs md:justify-self-end md:self-auto">
            <BrainCircuit className="h-4 w-4 text-emerald-500" />
            <label
              htmlFor="show-all-switch"
              className="cursor-pointer select-none whitespace-nowrap text-xs font-medium text-muted-foreground hover:text-foreground"
              title="Выключено: только матчи со ставкой и суммой. Включено: также вердикты без ставки."
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
              {filteredEvents.length}
            </Badge>
          </div>
        </div>

        {/* Center row: Status Buttons ('Все', 'В игре', 'Предстоящие', 'Завершенные') — STRICTLY CENTERED */}
        <div className="mt-3.5 flex items-center justify-center border-t border-border/40 py-3.5">
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
              В игре
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
            {activeStatus === "finished"
              ? "Neurobet ещё не поставил на завершённые матчи этого вида спорта."
              : !showAllMatches
              ? "Нет поставленных ставок. Включите «Показывать все», чтобы увидеть матчи с вердиктом без суммы."
              : "По текущим фильтрам нет доступных матчей. Ожидаем обновления данных Fonbet..."}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredEvents.flatMap((evt) => {
            const eventPredictions = predictions[evt.id] || [];
            const eventBets = betsByEvent.get(evt.id) || [];
            const markets = new Set<string>();
            for (const bet of eventBets) {
              if (isPlacedBet(bet) && bet.market) markets.add(bet.market);
            }
            if (showAllMatches) {
              for (const prediction of eventPredictions) {
                if (decisionStatus(prediction, evt.sport_code).chosen) {
                  markets.add(prediction.market || "match_winner");
                }
              }
            }
            const orderedMarkets = [...markets].sort((a, b) => {
              const order = (market: string) => {
                const set = /^set_winner:(\d+)$/.exec(market);
                return set ? Number(set[1]) : 0;
              };
              return order(a) - order(b);
            });
            return orderedMarkets.map((market) => (
              <MatchCard
                key={`${evt.id}:${market}`}
                event={evt}
                predictions={eventPredictions}
                bets={eventBets}
                marketFilter={market}
                betOutcome={betOutcomeByEvent.get(evt.id) || null}
                onInspect={onInspectEvent}
              />
            ));
          })}
        </div>
      )}
    </div>
  );
}
