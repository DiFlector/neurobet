"use client";

import {
  Search,
  Calendar,
  CheckCircle2,
  Trophy,
  TrendingUp,
  BrainCircuit,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EventItem, PredictionItem } from "@/lib/types";
import { getSportNameRu } from "@/lib/formatters";
import { getLocalizedParticipantName } from "@/lib/locale-names";
import { MatchTimer, stakeKindFromMarket } from "@/components/dashboard/match-timer";
import { MatchClock } from "@/components/dashboard/match-clock";
import { FitText } from "@/components/dashboard/fit-text";
import { HintTooltip, qualityScoreHint } from "@/components/dashboard/hint-tooltip";

interface MatchCardProps {
  event: EventItem;
  predictions: PredictionItem[];
  onInspect: (eventId: string) => void;
}

function matchesParticipant(
  outcomeStr: string,
  rawName: string,
  localizedName: string,
  roleCode: "a" | "b"
): boolean {
  if (!outcomeStr) return false;
  const o = outcomeStr.trim().toLowerCase();
  const raw = rawName.trim().toLowerCase();
  const loc = localizedName.trim().toLowerCase();

  if (
    roleCode === "a" &&
    (o === "player_a" || o === "team_a" || o === "1" || o === "w1" || o === "п1" || o === "home")
  ) {
    return true;
  }
  if (
    roleCode === "b" &&
    (o === "player_b" || o === "team_b" || o === "2" || o === "w2" || o === "п2" || o === "away")
  ) {
    return true;
  }

  // Exact or substring match on either raw or localized name
  if (o === raw || o === loc || raw.includes(o) || o.includes(raw) || loc.includes(o) || o.includes(loc)) {
    return true;
  }

  // Token overlap check
  const oWords = o.split(/\s+/).filter((w) => w.length > 2);
  const rawWords = raw.split(/\s+/).filter((w) => w.length > 2);
  const locWords = loc.split(/\s+/).filter((w) => w.length > 2);
  const allWords = [...rawWords, ...locWords];

  if (oWords.length > 0 && allWords.length > 0) {
    const hasOverlap = oWords.some((w) =>
      allWords.some((pw) => pw.includes(w) || w.includes(pw))
    );
    if (hasOverlap) return true;
  }
  return false;
}

export function MatchCard({ event, predictions, onInspect }: MatchCardProps) {
  const primaryPred =
    predictions.find((p) => p.has_positive_edge) || predictions[0] || null;

  const isLive = event.status === "live";
  const isPrematch = event.status === "prematch";
  const isFinished = event.status === "finished";

  const isTeam = ["basketball", "volleyball", "football", "hockey"].includes(
    event.sport_code?.toLowerCase() || ""
  );

  const roleA = isTeam ? "Команда 1" : "Игрок 1";
  const roleB = isTeam ? "Команда 2" : "Игрок 2";

  const rawNameA = event.participant_a || event.participant_a_name || "Участник 1";
  const rawNameB = event.participant_b || event.participant_b_name || "Участник 2";

  // Локализованные русские имена для фронтенда
  const playerA = getLocalizedParticipantName(rawNameA, event.sport_code);
  const playerB = getLocalizedParticipantName(rawNameB, event.sport_code);

  const outcomeStr =
    primaryPred?.predicted_outcome || primaryPred?.outcome || primaryPred?.selection || "";

  const isPredictedA = primaryPred
    ? matchesParticipant(outcomeStr, rawNameA, playerA, "a")
    : false;
  const isPredictedB = primaryPred
    ? matchesParticipant(outcomeStr, rawNameB, playerB, "b")
    : false;

  const stakePlayer = isPredictedA ? playerA : isPredictedB ? playerB : null;
  const hasEdge = isLive && primaryPred && primaryPred.has_positive_edge;

  return (
    <Card
      className={`border transition-all hover:shadow-md ${
        hasEdge
          ? "border-emerald-500/50 bg-emerald-500/[0.02] dark:bg-emerald-950/[0.15]"
          : "border-border/60 bg-card/80"
      }`}
    >
      <CardContent className="p-4 space-y-3.5">
        {/* Header: Sport, Event ID, Data Quality & Status Badge */}
        <div className="flex items-center gap-1.5 text-xs">
          <Trophy className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
          <span className="shrink-0 font-semibold text-foreground">
            {getSportNameRu(event.sport_code)}
          </span>
          <span className="shrink-0 text-muted-foreground/60">•</span>
          <FitText className="min-w-0 flex-1 font-mono text-muted-foreground" maxPx={11} minPx={9}>
            № {event.source_event_id || event.id.slice(0, 8)}
          </FitText>
          <HintTooltip content={qualityScoreHint(Number(event.quality_score || 100))}>
            <span className="shrink-0 rounded bg-muted/60 px-1 py-0.5 font-mono text-[10px] text-muted-foreground/80">
              К: {Number(event.quality_score || 100).toFixed(0)}%
            </span>
          </HintTooltip>
          <div className="shrink-0 flex items-center gap-1.5">
            {isLive && <MatchClock score={event.current_score} />}
            {isLive && (
              <Badge
                variant="destructive"
                className="whitespace-nowrap bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/30 gap-1.5 font-medium px-2 py-0.5"
              >
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75"></span>
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-red-500"></span>
                </span>
                В игре
              </Badge>
            )}
            {isPrematch && (
              <Badge
                variant="outline"
                className="bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30 gap-1 text-[11px] font-medium px-2 py-0.5"
              >
                <Calendar className="h-3 w-3" />
                Линия
              </Badge>
            )}
            {isFinished && (
              <Badge
                variant="outline"
                className="bg-muted text-muted-foreground border-border gap-1 text-[11px] font-medium px-2 py-0.5"
              >
                <CheckCircle2 className="h-3 w-3" />
                Завершен
              </Badge>
            )}
          </div>
        </div>

        {/* Match Participants & Live MatchTimer */}
        <div className="grid grid-cols-[minmax(0,1fr)_7.5rem_minmax(0,1fr)] items-start gap-2 py-1">
          {/* Participant A */}
          <div
            className={`min-w-0 p-2 rounded-lg transition-all ${
              isPredictedA
                ? "bg-emerald-500/10 dark:bg-emerald-950/40 border border-emerald-500/40 shadow-xs ring-1 ring-emerald-500/30"
                : ""
            }`}
          >
            <div className="flex flex-col items-start gap-1">
              <div
                className={`line-clamp-2 text-[13px] font-semibold leading-4 ${
                  isPredictedA
                    ? "text-emerald-600 dark:text-emerald-400 font-bold"
                    : "text-foreground"
                }`}
                title={playerA}
              >
                {playerA}
              </div>
              {isPredictedA && (
                <Badge
                  variant="outline"
                  className="h-4 max-w-full bg-emerald-500/20 px-1.5 py-0 text-[11px] font-bold text-emerald-700 dark:text-emerald-300 border-emerald-500/50"
                  title={`Выбор ML (${Math.round(primaryPred.model_probability * 100)}%)`}
                >
                  Выбор ML
                </Badge>
              )}
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">{roleA}</div>
          </div>

          {/* Match Score & Live Timer */}
          <MatchTimer event={event} betKind={stakeKindFromMarket(primaryPred?.market)} className="px-1" />

          {/* Participant B */}
          <div
            className={`min-w-0 p-2 rounded-lg transition-all text-right ${
              isPredictedB
                ? "bg-emerald-500/10 dark:bg-emerald-950/40 border border-emerald-500/40 shadow-xs ring-1 ring-emerald-500/30"
                : ""
            }`}
          >
            <div className="flex flex-col items-end gap-1">
              <div
                className={`line-clamp-2 w-full text-right text-[13px] font-semibold leading-4 ${
                  isPredictedB
                    ? "text-emerald-600 dark:text-emerald-400 font-bold"
                    : "text-foreground"
                }`}
                title={playerB}
              >
                {playerB}
              </div>
              {isPredictedB && (
                <Badge
                  variant="outline"
                  className="h-4 max-w-full bg-emerald-500/20 px-1.5 py-0 text-[11px] font-bold text-emerald-700 dark:text-emerald-300 border-emerald-500/50"
                  title={`Выбор ML (${Math.round(primaryPred.model_probability * 100)}%)`}
                >
                  Выбор ML
                </Badge>
              )}
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">{roleB}</div>
          </div>
        </div>

        {/* Odds & Value Matrix */}
        <div className="rounded-md border border-border/50 bg-muted/30 p-2.5">
          {primaryPred ? (
            <div className="grid grid-cols-4 gap-1.5 text-center text-xs">
              <div>
                <div className="text-[10px] text-muted-foreground">Ставка</div>
                <div
                  className="mt-0.5 truncate font-medium text-foreground"
                  title={
                    stakePlayer
                      ? `Победа в матче: ${stakePlayer}`
                      : primaryPred.market || "Победа в матче"
                  }
                >
                  {stakePlayer || "Матч"}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground">Вер. ML</div>
                <div className="font-mono font-semibold text-emerald-600 dark:text-emerald-400 mt-0.5">
                  {(primaryPred.model_probability * 100).toFixed(1)}%
                </div>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground">Коэф.</div>
                <div className="font-mono font-semibold text-foreground mt-0.5">
                  {Number(primaryPred.bookmaker_odds).toFixed(2)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground">Перевес</div>
                <div
                  className={`font-mono font-semibold mt-0.5 flex items-center justify-center gap-0.5 ${
                    primaryPred.edge > 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-muted-foreground"
                  }`}
                >
                  {primaryPred.edge > 0 && <TrendingUp className="h-3 w-3" />}
                  {primaryPred.edge > 0 ? "+" : ""}
                  {(primaryPred.edge * 100).toFixed(1)}%
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between px-2.5 py-1.5 rounded bg-muted/40 text-[11px]">
              <span className="font-medium text-muted-foreground">Нет расчёта</span>
            </div>
          )}
        </div>

        {/* Footer: Verdict Tags & Inspect Button */}
        <div className="flex items-center justify-between pt-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            {hasEdge ? (
              <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white text-[10px] px-2 py-0 font-medium">
                Ценная ставка
              </Badge>
            ) : isLive ? (
              <Badge
                variant="outline"
                className="text-[10px] px-2 py-0 text-muted-foreground border-border/80"
              >
                Без перевеса
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="text-[10px] px-2 py-0 text-muted-foreground/80 border-border/60"
              >
                Только Live
              </Badge>
            )}

            <Badge
              variant="outline"
              className="text-[10px] px-2 py-0 border-border/60 text-muted-foreground gap-1 hidden xs:inline-flex"
            >
              <BrainCircuit className="h-3 w-3 text-indigo-400" />
              {primaryPred ? primaryPred.model_version : "tennis_v1.0"}
            </Badge>
          </div>

          <Button
            variant="outline"
            size="sm"
            className="h-8 px-2.5 gap-1.5 text-xs border-border/70 hover:bg-muted"
            onClick={() => onInspect(event.id)}
          >
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <span>Детали</span>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
