"use client";

import * as React from "react";
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
import { decisionStatus, stakeReason } from "@/lib/decision-status";
import { formatCurrency } from "@/lib/formatters";
import { isPlacedBet } from "@/lib/placed-bets";
import { BetItem, EventItem, PredictionItem } from "@/lib/types";
import { getSportNameRu } from "@/lib/formatters";
import { getLocalizedParticipantName } from "@/lib/locale-names";
import { MatchTimer, stakeKindFromMarket } from "@/components/dashboard/match-timer";
import { MatchClock } from "@/components/dashboard/match-clock";
import { FitText } from "@/components/dashboard/fit-text";
import { HintTooltip, qualityScoreHint } from "@/components/dashboard/hint-tooltip";
import { compositeVerdict, isCompositePrediction } from "@/lib/composite-label";

export type BetOutcomeTone = "won" | "lost" | "void";

interface MatchCardProps {
  event: EventItem;
  predictions: PredictionItem[];
  bets?: BetItem[];
  marketFilter?: string;
  onInspect: (eventId: string) => void;
  betOutcome?: BetOutcomeTone | null;
}

/** Card fills for a settled bet on a finished match. */
const OUTCOME_CARD: Record<BetOutcomeTone, string> = {
  won: "border-[#22c55e] bg-[#14532d]",
  lost: "border-[#ef4444] bg-[#7f1d1d]",
  void: "border-[#a1a1aa] bg-[#3f3f46]",
};

const OUTCOME_LABEL: Record<BetOutcomeTone, string> = {
  won: "Победа",
  lost: "Поражение",
  void: "Возврат",
};

const OUTCOME_MARK: Record<BetOutcomeTone, string> = {
  won: "/diflector/neurobet/brand/won.svg",
  lost: "/diflector/neurobet/brand/lost.svg",
  void: "/diflector/neurobet/brand/void.svg",
};

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

function lineLabel(pred: PredictionItem): string {
  const name = pred.model_name || "";
  const set = /^set_winner:(\d+)$/.exec(pred.market || "");
  const market = set ? `Сет ${set[1]}` : "Матч";
  if (isCompositePrediction(pred)) return `${market} · Составная модель`;
  if (name.includes("logistic")) return `${market} · Логистическая`;
  if (name.includes("gradient")) return `${market} · Градиент`;
  return `${market} · ${pred.model_version || "Модель"}`;
}

function sideChoiceHint(
  lines: PredictionItem[],
  rawName: string,
  playerName: string,
  side: "a" | "b",
  playerA: string,
  playerB: string,
  sportCode?: string,
  placedModels: string[] = [],
) {
  const picks = lines.filter((line) => {
    const outcome = line.predicted_outcome || line.outcome || line.selection || "";
    return matchesParticipant(outcome, rawName, playerName, side);
  });
  if (picks.length === 0) {
    return `${playerName}. Ни одна модель не выбрала эту сторону.`;
  }
  return (
    <span className="block space-y-1">
      <span className="block font-medium">{playerName}</span>
      {placedModels.length > 0 && (
        <span className="block font-medium text-emerald-600 dark:text-emerald-400">
          Поставили: {placedModels.join(", ")}
        </span>
      )}
      {picks.map((line) => (
        <span key={line.id || lineKey(line)} className="block">
          Модель: {line.model_version || line.model_name || "неизвестна"} —{" "}
          {decisionStatus(line, sportCode).label}
          {isCompositePrediction(line) ? compositeVerdict(line, playerA, playerB) : null}
        </span>
      ))}
    </span>
  );
}

function lineKey(row: PredictionItem): string {
  return `${row.market || "match"}:${row.model_name || row.model_version || row.id || "line"}`;
}

function scoredLines(predictions: PredictionItem[], shadow: boolean): PredictionItem[] {
  const rows = predictions.filter(
    (p) => Boolean(p.is_shadow) === shadow && (p.model_probability || 0) > 0,
  );
  const byLine = new Map<string, PredictionItem>();
  for (const row of rows) {
    const key = lineKey(row);
    const prev = byLine.get(key);
    if (!prev) {
      byLine.set(key, row);
      continue;
    }
    if ((row.created_at || "") > (prev.created_at || "")) {
      byLine.set(key, row);
      continue;
    }
    if ((row.created_at || "") === (prev.created_at || "") && row.has_positive_edge && !prev.has_positive_edge) {
      byLine.set(key, row);
    }
  }
  return [...byLine.values()].sort((a, b) => lineKey(a).localeCompare(lineKey(b)));
}

function marketStakeLabel(market: string): string {
  const set = /^set_winner:(\d+)$/.exec(market || "");
  return set ? `Победа в сете ${set[1]}` : "Победа в матче";
}

function betOutcomeLabel(
  outcome: string,
  playerA: string,
  playerB: string,
): string {
  const code = (outcome || "").trim().toLowerCase();
  if (["player_a", "team_a", "1", "w1", "п1", "home", "p1"].includes(code)) return playerA;
  if (["player_b", "team_b", "2", "w2", "п2", "away", "p2"].includes(code)) return playerB;
  return outcome || "Исход";
}

export function MatchCard({
  event,
  predictions,
  bets = [],
  marketFilter,
  onInspect,
  betOutcome = null,
}: MatchCardProps) {
  const [copiedParticipant, setCopiedParticipant] = React.useState<string | null>(null);
  const scopedPredictions = marketFilter
    ? predictions.filter((prediction) => (prediction.market || "match_winner") === marketFilter)
    : predictions;
  const scopedBets = marketFilter
    ? bets.filter((bet) => bet.market === marketFilter)
    : bets;
  const combatPredictions =
    [...scopedPredictions]
      .filter((p) => !p.is_shadow && p.has_positive_edge)
      .sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
  const placedBets = scopedBets.filter((bet) => isPlacedBet(bet));
  const placedMarkets = new Set(placedBets.map((bet) => bet.market));
  const placedCombatPredictions = [...scopedPredictions]
    .filter((p) => !p.is_shadow && p.has_positive_edge && placedMarkets.has(p.market || "match_winner"))
    .sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
  const combatPred = placedCombatPredictions[0] || combatPredictions[0] || null;
  const analysisLines = [
    ...scoredLines(scopedPredictions, false),
    ...scoredLines(scopedPredictions, true),
  ];
  const isPlacedModelLine = (line: PredictionItem) =>
    placedBets.some(
      (bet) =>
        bet.market === (line.market || "match_winner") &&
        bet.outcome === (line.predicted_outcome || line.outcome || line.selection || "") &&
        (!bet.model_version || bet.model_version === line.model_version),
    );
  const visibleLines = analysisLines.filter(
    (line) =>
      isPlacedModelLine(line) ||
      decisionStatus(line, event.sport_code).chosen,
  );
  const chosenLines = visibleLines.filter(
    (line) =>
      !line.is_shadow &&
      decisionStatus(line, event.sport_code).chosen,
  );
  const primaryPred = chosenLines[0] || combatPred || null;
  const groupedVisibleLines = [...new Map(
    visibleLines.reduce((groups, line) => {
      const market = line.market || "match_winner";
      const current = groups.get(market) || [];
      current.push(line);
      groups.set(market, current);
      return groups;
    }, new Map<string, PredictionItem[]>()).entries(),
  )];
  const shadowOnly = placedBets.length === 0 && visibleLines.length > 0 && !combatPred;

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

  const linePicks = (line: PredictionItem, rawName: string, playerName: string, side: "a" | "b") => {
    const outcome = line.predicted_outcome || line.outcome || line.selection || "";
    return matchesParticipant(outcome, rawName, playerName, side);
  };
  const selectedLines = placedBets.length
    ? analysisLines.filter((line) =>
        placedBets.some(
          (bet) =>
            bet.market === (line.market || "match_winner") &&
            bet.outcome === (line.predicted_outcome || line.outcome || line.selection || ""),
        ),
      )
    : combatPred
      ? [combatPred]
      : chosenLines;
  const isPredictedA = selectedLines.some((line) => linePicks(line, rawNameA, playerA, "a"));
  const isPredictedB = selectedLines.some((line) => linePicks(line, rawNameB, playerB, "b"));

  const placedSideA = placedBets.some((bet) => matchesParticipant(bet.outcome, rawNameA, playerA, "a"));
  const placedSideB = placedBets.some((bet) => matchesParticipant(bet.outcome, rawNameB, playerB, "b"));
  const placedModelsForSide = (side: "a" | "b") => [
    ...new Set(
      placedBets
        .filter((bet) => matchesParticipant(
          bet.outcome,
          side === "a" ? rawNameA : rawNameB,
          side === "a" ? playerA : playerB,
          side,
        ))
        .map((bet) => bet.model_version)
        .filter((version): version is string => Boolean(version)),
    ),
  ];
  const copyParticipant = React.useCallback(async (name: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(name);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = name;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
      setCopiedParticipant(name);
      window.setTimeout(
        () => setCopiedParticipant((current) => (current === name ? null : current)),
        1400,
      );
    } catch {
      setCopiedParticipant(null);
    }
  }, []);
  const hasEdge = isLive && Boolean(combatPred && combatPred.has_positive_edge);
  const outcomeTone = isFinished ? betOutcome : null;

  return (
    <Card
      className={`relative overflow-hidden border transition-all hover:shadow-md ${
        outcomeTone
          ? OUTCOME_CARD[outcomeTone]
          : hasEdge
            ? "border-emerald-500/50 bg-emerald-500/[0.02] dark:bg-emerald-950/[0.15]"
            : "border-border/60 bg-card/80"
      }`}
    >
      {outcomeTone && (
        <img
          src={OUTCOME_MARK[outcomeTone]}
          alt=""
          aria-hidden
          className="pointer-events-none absolute bottom-0 right-0 z-0 h-40 w-40 select-none"
        />
      )}
      <CardContent className="relative z-10 p-4 space-y-3.5">
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
          <HintTooltip content={qualityScoreHint(Number(event.quality_score || 100), event.quality_reasons)}>
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
            {isFinished && outcomeTone === "won" && (
              <Badge className="border-[#22c55e] bg-[#22c55e] text-white gap-1 text-[11px] font-medium px-2 py-0.5 hover:bg-[#22c55e]">
                <CheckCircle2 className="h-3 w-3" />
                {OUTCOME_LABEL.won}
              </Badge>
            )}
            {isFinished && outcomeTone === "lost" && (
              <Badge className="border-[#ef4444] bg-[#ef4444] text-white gap-1 text-[11px] font-medium px-2 py-0.5 hover:bg-[#ef4444]">
                <CheckCircle2 className="h-3 w-3" />
                {OUTCOME_LABEL.lost}
              </Badge>
            )}
            {isFinished && outcomeTone === "void" && (
              <Badge className="border-[#a1a1aa] bg-[#a1a1aa] text-[#18181b] gap-1 text-[11px] font-medium px-2 py-0.5 hover:bg-[#a1a1aa]">
                <CheckCircle2 className="h-3 w-3" />
                {OUTCOME_LABEL.void}
              </Badge>
            )}
            {isFinished && !outcomeTone && (
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
            className={`min-w-0 cursor-pointer p-2 rounded-lg transition-all ${
              isPredictedA || placedSideA
                ? "bg-emerald-500/10 dark:bg-emerald-950/40 border border-emerald-500/40 shadow-xs ring-1 ring-emerald-500/30"
                : ""
            }`}
            role="button"
            tabIndex={0}
            title={`Скопировать: ${playerA}`}
            onClick={() => void copyParticipant(playerA)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                void copyParticipant(playerA);
              }
            }}
          >
            <HintTooltip
              className="w-full max-w-full flex-col items-start"
              side="bottom"
              content={sideChoiceHint(
                visibleLines, rawNameA, playerA, "a", playerA, playerB, event.sport_code,
                placedModelsForSide("a"),
              )}
            >
              <span className="flex w-full flex-col items-start gap-1">
                <span
                  className={`line-clamp-2 text-[13px] font-semibold leading-4 ${
                    isPredictedA || placedSideA
                      ? "text-emerald-600 dark:text-emerald-400 font-bold"
                      : "text-foreground"
                  }`}
                >
                  {playerA}
                </span>
                {(isPredictedA || placedSideA || copiedParticipant === playerA) && (
                  <Badge
                    variant="outline"
                    className="h-4 max-w-full bg-emerald-500/20 px-1.5 py-0 text-[11px] font-bold text-emerald-700 dark:text-emerald-300 border-emerald-500/50"
                  >
                    {copiedParticipant === playerA ? "Скопировано" : "Выбрала"}
                  </Badge>
                )}
                <span className="mt-0.5 text-[11px] font-normal text-muted-foreground">{roleA}</span>
              </span>
            </HintTooltip>
          </div>

          {/* Match Score & Live Timer */}
          <MatchTimer
            event={event}
            betKind={stakeKindFromMarket(placedBets[0]?.market || primaryPred?.market)}
            market={placedBets[0]?.market || primaryPred?.market}
            className="px-1"
          />

          {/* Participant B */}
          <div
            className={`min-w-0 cursor-pointer p-2 rounded-lg transition-all text-right ${
              isPredictedB || placedSideB
                ? "bg-emerald-500/10 dark:bg-emerald-950/40 border border-emerald-500/40 shadow-xs ring-1 ring-emerald-500/30"
                : ""
            }`}
            role="button"
            tabIndex={0}
            title={`Скопировать: ${playerB}`}
            onClick={() => void copyParticipant(playerB)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                void copyParticipant(playerB);
              }
            }}
          >
            <HintTooltip
              className="w-full max-w-full flex-col items-end"
              side="bottom"
              content={sideChoiceHint(
                visibleLines, rawNameB, playerB, "b", playerA, playerB, event.sport_code,
                placedModelsForSide("b"),
              )}
            >
              <span className="flex w-full flex-col items-end gap-1">
                <span
                  className={`line-clamp-2 w-full text-right text-[13px] font-semibold leading-4 ${
                    isPredictedB || placedSideB
                      ? "text-emerald-600 dark:text-emerald-400 font-bold"
                      : "text-foreground"
                  }`}
                >
                  {playerB}
                </span>
                {(isPredictedB || placedSideB || copiedParticipant === playerB) && (
                  <Badge
                    variant="outline"
                    className="h-4 max-w-full bg-emerald-500/20 px-1.5 py-0 text-[11px] font-bold text-emerald-700 dark:text-emerald-300 border-emerald-500/50"
                  >
                    {copiedParticipant === playerB ? "Скопировано" : "Выбрала"}
                  </Badge>
                )}
                <span className="mt-0.5 text-[11px] font-normal text-muted-foreground">{roleB}</span>
              </span>
            </HintTooltip>
          </div>
        </div>

        {placedBets.length > 0 && (
          <div className="space-y-1.5">
            {placedBets.map((bet) => (
              <div key={bet.id} className="flex items-center justify-between gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-2 text-xs">
                <div>
                  <div className="font-semibold text-foreground">Ставка: {marketStakeLabel(bet.market)}</div>
                  <div className="text-[11px] text-emerald-700 dark:text-emerald-300">
                    {betOutcomeLabel(bet.outcome, playerA, playerB)}
                  </div>
                </div>
                <span className="font-mono font-semibold text-emerald-700 dark:text-emerald-300">
                  {formatCurrency(bet.stake)} · {Number(bet.odds).toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Odds & Value Matrix */}
        <div className="rounded-md border border-border/50 bg-muted/30 p-2.5">
          {visibleLines.length > 0 ? (
            <div className="space-y-2">
              {groupedVisibleLines.map(([market, lines]) => (
                <div key={market} className="space-y-2">
                  <div className="border-b border-border/50 pb-1 text-[11px] font-semibold text-foreground">
                    {marketStakeLabel(market)}
                  </div>
                  {lines.map((line) => {
                const lineSide = line.predicted_outcome || line.outcome || line.selection || "";
                const placedForLine = placedBets.find(
                  (bet) => bet.market === (line.market || "match_winner"),
                );
                const displayedSide = placedForLine?.outcome || lineSide;
                const linePlayer = matchesParticipant(displayedSide, rawNameA, playerA, "a")
                  ? playerA
                  : matchesParticipant(displayedSide, rawNameB, playerB, "b")
                    ? playerB
                    : "—";
                const shownOdds = placedForLine ? Number(placedForLine.odds) : Number(line.bookmaker_odds);
                  return (
                    <div key={`${market}:${line.id || line.model_name || line.model_version}`} className="grid grid-cols-4 gap-1.5 text-center text-xs">
                    <div>
                      <HintTooltip
                        content={
                          isCompositePrediction(line)
                            ? compositeVerdict(line, playerA, playerB)
                            : lineLabel(line)
                        }
                        side="bottom"
                      >
                        <div className="text-[10px] text-muted-foreground">
                          {line.model_version || line.model_name || "Модель"}
                        </div>
                      </HintTooltip>
                      <div className="mt-0.5 truncate font-medium text-foreground" title={linePlayer}>
                        {linePlayer}
                      </div>
                      <HintTooltip
                        content={
                          isCompositePrediction(line)
                            ? compositeVerdict(line, playerA, playerB)
                            : decisionStatus(line, event.sport_code).title
                        }
                        side="bottom"
                      >
                        <span
                          className={`mt-0.5 text-[10px] font-medium ${
                            decisionStatus(line, event.sport_code).chosen
                              ? "text-emerald-600 dark:text-emerald-400"
                              : "text-muted-foreground"
                          }`}
                        >
                          {decisionStatus(line, event.sport_code).label}
                        </span>
                      </HintTooltip>
                    </div>
                    <div>
                      <div className="text-[10px] text-muted-foreground">Вер. ML</div>
                      <div className="font-mono font-semibold text-emerald-600 dark:text-emerald-400 mt-0.5">
                        {(line.model_probability * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-muted-foreground">Коэф.</div>
                      <div className="font-mono font-semibold text-foreground mt-0.5">
                        {shownOdds.toFixed(2)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-muted-foreground">Перевес</div>
                      <div
                        className={`font-mono font-semibold mt-0.5 flex items-center justify-center gap-0.5 ${
                          line.edge > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"
                        }`}
                      >
                        {line.edge > 0 && <TrendingUp className="h-3 w-3" />}
                        {line.edge > 0 ? "+" : ""}
                        {(line.edge * 100).toFixed(1)}%
                      </div>
                    </div>
                    </div>
                  );
                })}
                </div>
              ))}
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
            ) : shadowOnly ? (
              <Badge
                variant="outline"
                className="text-[10px] px-2 py-0 border-indigo-500/40 text-indigo-600 dark:text-indigo-400"
              >
                Тень, без ставки
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
