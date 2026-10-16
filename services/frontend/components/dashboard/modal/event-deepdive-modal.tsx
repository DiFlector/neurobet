"use client";

import * as React from "react";
import {
  TrendingUp,
  BrainCircuit,
  Bot,
  Clock,
  ExternalLink,
  ShieldAlert,
  CheckCircle2,
  Trophy,
  Calendar,
  Radio,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { decisionStatus } from "@/lib/decision-status";
import { compositeVerdict, isCompositePrediction } from "@/lib/composite-label";
import { EventDetailData, EventItem, LLMDecisionItem, OddItem, PredictionItem } from "@/lib/types";
import { getSportNameRu, formatTime } from "@/lib/formatters";
import { getLocalizedParticipantName } from "@/lib/locale-names";
import { getDetailedScore } from "@/components/dashboard/match-timer";
import {
  fatigueRiskHint,
  injuryRiskHint,
  localizeLlmSummary,
  localizeReasonCode,
  localizeRiskLevel,
  localizeVerdict,
  reasonCodeHint,
} from "@/lib/llm-locale";
import { HintTooltip, qualityScoreHint } from "@/components/dashboard/hint-tooltip";

function buildMlFallbackSummary(params: {
  playerA: string;
  playerB: string;
  score: string;
  primaryPred: PredictionItem | null;
  predictedWinnerName: string;
}): string {
  const { playerA, playerB, score, primaryPred, predictedWinnerName } = params;
  if (!primaryPred) {
    return `Для матча ${playerA} — ${playerB} (счёт ${score}) числовой прогноз и вердикт языковой модели пока не записаны.`;
  }
  const prob = (primaryPred.model_probability * 100).toFixed(1);
  const odds = Number(primaryPred.bookmaker_odds).toFixed(2);
  const edgePct = (primaryPred.edge * 100).toFixed(1);
  const edgeSign = primaryPred.edge > 0 ? "+" : "";
  const version = primaryPred.model_version || "unknown";
  return (
    `Матч ${playerA} — ${playerB}, текущий счёт ${score}. ` +
    `Модель ${version} оценивает исход «${predictedWinnerName || primaryPred.predicted_outcome || primaryPred.outcome || "—"}» ` +
    `с вероятностью ${prob}% при коэффициенте ${odds} (перевес ${edgeSign}${edgePct}%). ` +
    `Вердикт локальной языковой модели для этого события ещё не записан.`
  );
}

function verdictBadgeClass(verdict: string): string {
  const v = (verdict || "").toUpperCase();
  if (v === "BET" || v === "SUPPORT") {
    return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30";
  }
  if (v === "NO_BET" || v === "OPPOSE") {
    return "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

interface EventDeepdiveModalProps {
  data: EventDetailData | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
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
  if (o === raw || o === loc || raw.includes(o) || o.includes(raw) || loc.includes(o) || o.includes(loc)) {
    return true;
  }
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

function collapseTimeline(items: EventDetailData["timeline"]): EventDetailData["timeline"] {
  const collapsed: EventDetailData["timeline"] = [];
  for (const item of items) {
    const score = item.score || "";
    const previous = collapsed[collapsed.length - 1];
    if (previous && score && (previous.score || "") === score) {
      continue;
    }
    collapsed.push(item);
  }
  return collapsed;
}

function outcomeSideLabel(outcome: string | undefined, playerA: string, playerB: string): string {
  const code = (outcome || "").trim().toLowerCase();
  if (["player_a", "team_a", "1", "p1", "w1", "home", "п1"].includes(code)) return playerA;
  if (["player_b", "team_b", "2", "p2", "w2", "away", "п2"].includes(code)) return playerB;
  return outcome || "—";
}

function marketLabel(market: string | undefined): string {
  const code = (market || "").trim().toLowerCase();
  if (!code || code === "match_winner") return "Победа в матче";
  return market || "Победа в матче";
}

function impliedProbability(odd: OddItem): number | null {
  const raw = odd.implied_prob ?? odd.probability_implied;
  if (raw === undefined || raw === null || Number.isNaN(Number(raw))) return null;
  const value = Number(raw);
  if (value <= 0) return null;
  return value > 1 ? value / 100 : value;
}

function marginsByMoment(odds: OddItem[]): Map<string, number> {
  const groups = new Map<string, number[]>();
  for (const odd of odds) {
    const price = Number(odd.odds);
    if (!odd.observed_at || !Number.isFinite(price) || price <= 1) continue;
    const key = `${odd.observed_at}|${odd.market_type || odd.market || ""}`;
    const prices = groups.get(key) || [];
    prices.push(price);
    groups.set(key, prices);
  }
  const margins = new Map<string, number>();
  for (const [key, prices] of groups) {
    if (prices.length < 2) continue;
    const overround = prices.reduce((sum, price) => sum + 1 / price, 0) - 1;
    margins.set(key, overround);
  }
  return margins;
}

function getFormattedScore(evt: EventItem): string {
  const score = getDetailedScore(evt);
  return score.sub ? `${score.main} (${score.sub})` : score.main;
}

export function EventDeepdiveModal({
  data,
  open,
  onOpenChange,
}: EventDeepdiveModalProps) {
  if (!data || !data.evt) return null;

  const { evt, timeline: rawTimeline, odds, preds, research, research_status, llm = [] } = data;
  const timeline = collapseTimeline(rawTimeline);
  const marginByMoment = marginsByMoment(odds);

  const rawA = evt.participant_a || evt.participant_a_name || "Участник 1";
  const rawB = evt.participant_b || evt.participant_b_name || "Участник 2";

  // Локализованные русские имена
  const playerA = getLocalizedParticipantName(rawA, evt.sport_code);
  const playerB = getLocalizedParticipantName(rawB, evt.sport_code);

  const byFirstChoice = (items: PredictionItem[]) =>
    [...items].sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
  const primaryPred =
    byFirstChoice(preds.filter((p) => !p.is_shadow && p.has_positive_edge))[0] || null;
  const lineKey = (row: PredictionItem) =>
    `${row.market || "match"}:${row.model_name || ""}:${row.model_version}:${row.is_shadow ? "s" : "c"}`;
  const shadowPreds = [...preds.filter((p) => p.is_shadow && (p.model_probability || 0) > 0)]
    .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
    .filter((p, index, rows) => rows.findIndex((row) => lineKey(row) === lineKey(p)) === index);
  const orderedPreds = primaryPred ? [primaryPred, ...shadowPreds] : shadowPreds;
  const primaryLlm: LLMDecisionItem | null = llm[0] || null;

  const outcomeStr =
    primaryPred?.predicted_outcome || primaryPred?.outcome || primaryPred?.selection || "";

  const isPredA = primaryPred
    ? matchesParticipant(outcomeStr, rawA, playerA, "a")
    : false;
  const isPredB = primaryPred
    ? matchesParticipant(outcomeStr, rawB, playerB, "b")
    : false;

  const predictedWinnerName = isPredA
    ? playerA
    : isPredB
    ? playerB
    : getLocalizedParticipantName(outcomeStr, evt.sport_code);

  const isLive = evt.status === "live";
  const isPrematch = evt.status === "prematch";
  const isFinished = evt.status === "finished";

  const defaultTab = preds.length > 0 && odds.length === 0 ? "ml" : "odds";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl md:max-w-3xl lg:max-w-4xl max-h-[90vh] overflow-y-auto w-full p-4 sm:p-6">
        <DialogHeader className="border-b border-border/60 pb-3 space-y-2.5">
          {/* Metadata bar */}
          <div className="flex items-center justify-between text-xs text-muted-foreground font-medium">
            <div className="flex items-center gap-1.5 flex-wrap">
              <Trophy className="h-4 w-4 text-emerald-500" />
              <span className="font-semibold text-foreground">
                {getSportNameRu(evt.sport_code)}
              </span>
              <span>•</span>
              <span className="font-mono">
                № {evt.source_event_id || evt.id.slice(0, 8)}
              </span>
              <span>•</span>
              <HintTooltip content={qualityScoreHint(Number(evt.quality_score || 100), evt.quality_reasons)}>
                <span className="font-mono">
                  К: {Number(evt.quality_score || 100).toFixed(0)}%
                </span>
              </HintTooltip>
            </div>

            <div>
              {isLive && (
                <Badge
                  variant="destructive"
                  className="bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/30 gap-1 text-[11px] font-medium px-2 py-0.5"
                >
                  <Radio className="h-3 w-3 text-red-500 animate-pulse" />
                  В игре
                </Badge>
              )}
              {isPrematch && (
                <Badge
                  variant="outline"
                  className="bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30 gap-1 text-[11px] font-medium px-2 py-0.5"
                >
                  <Calendar className="h-3 w-3 text-blue-500" />
                  Линия
                </Badge>
              )}
              {isFinished && (
                <Badge
                  variant="outline"
                  className="bg-muted text-muted-foreground border-border gap-1 text-[11px] font-medium px-2 py-0.5"
                >
                  <CheckCircle2 className="h-3 w-3 text-muted-foreground" />
                  Завершен
                </Badge>
              )}
            </div>
          </div>

          {/* Title & Score */}
          <DialogTitle className="text-lg sm:text-2xl font-bold text-foreground flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={
                  isPredA
                    ? "text-emerald-600 dark:text-emerald-400 font-extrabold"
                    : "text-foreground"
                }
              >
                {playerA}
              </span>
              <span className="text-muted-foreground font-normal text-sm sm:text-base">vs</span>
              <span
                className={
                  isPredB
                    ? "text-emerald-600 dark:text-emerald-400 font-extrabold"
                    : "text-foreground"
                }
              >
                {playerB}
              </span>
            </div>

            <div className="text-sm font-mono font-bold px-3 py-1 rounded bg-muted/60 border border-border/50 text-foreground">
              {getFormattedScore(evt)}
            </div>
          </DialogTitle>

          <DialogDescription className="text-xs text-muted-foreground flex items-center gap-2">
            <span>
              Статус:{" "}
              {isLive
                ? "Прямой эфир"
                : isPrematch
                ? "Предстоящее событие"
                : "Завершено"}
            </span>
            {evt.scheduled_start_at && (
              <>
                <span>•</span>
                <span>Начало: {formatTime(evt.scheduled_start_at)}</span>
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* Model Recommendation Callout */}
        {primaryPred && (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 p-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 dark:bg-emerald-950/30">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-md bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 shrink-0">
                <BrainCircuit className="h-4 w-4" />
              </div>
              <div>
                <div className="text-[11px] text-muted-foreground font-medium">
                  Рекомендация ML-модели:
                </div>
                <div className="text-sm font-bold text-foreground">
                  Победа:{" "}
                  <span className="text-emerald-600 dark:text-emerald-400 font-extrabold">
                    {predictedWinnerName || "Участник A"}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3 text-xs flex-wrap">
              <div>
                <span className="text-muted-foreground">Вероятность: </span>
                <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">
                  {(primaryPred.model_probability * 100).toFixed(1)}%
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">Коэффициент: </span>
                <span className="font-mono font-bold text-foreground">
                  {Number(primaryPred.bookmaker_odds).toFixed(2)}
                </span>
              </div>
              {primaryPred.edge > 0 && (
                <Badge
                  variant="outline"
                  className="bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border-emerald-500/40 font-bold text-[11px]"
                >
                  +{(primaryPred.edge * 100).toFixed(1)}% перевес
                </Badge>
              )}
            </div>
          </div>
        )}

        {/* Modal Inner Tabs */}
        <Tabs defaultValue={defaultTab} className="w-full mt-2">
          <TabsList className="w-full grid grid-cols-2 sm:grid-cols-4 h-auto p-1 bg-muted/60 gap-1 rounded-lg">
            <TabsTrigger value="odds" className="text-xs gap-1.5 py-2 px-2.5">
              <TrendingUp className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">Котировки ({odds.length})</span>
            </TabsTrigger>
            <TabsTrigger value="ml" className="text-xs gap-1.5 py-2 px-2.5">
              <BrainCircuit className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">ML Анализ ({orderedPreds.length})</span>
            </TabsTrigger>
            <TabsTrigger value="llm" className="text-xs gap-1.5 py-2 px-2.5">
              <Bot className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">Контекст модели</span>
            </TabsTrigger>
            <TabsTrigger value="timeline" className="text-xs gap-1.5 py-2 px-2.5">
              <Clock className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">Таймлайн ({timeline.length})</span>
            </TabsTrigger>
          </TabsList>

          {/* 1. Котировки и Рынки */}
          <TabsContent value="odds" className="mt-3">
            <div className="rounded-md border border-border/60 overflow-hidden">
              <Table>
                <TableHeader className="bg-muted/40">
                  <TableRow className="border-border/60 text-xs">
                    <TableHead className="font-semibold">Рынок</TableHead>
                    <TableHead className="font-semibold">Выбор</TableHead>
                    <TableHead className="font-semibold text-right">Коэффициент</TableHead>
                    <TableHead className="font-semibold text-right">Вероятность</TableHead>
                    <TableHead className="font-semibold text-right">Маржа</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {odds.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={5}
                        className="text-center text-muted-foreground py-6 text-xs"
                      >
                        Котировки рынков обновляются с линии Fonbet...
                      </TableCell>
                    </TableRow>
                  ) : (
                    odds.map((o, idx) => {
                      const probability = impliedProbability(o);
                      const momentKey = `${o.observed_at}|${o.market_type || o.market || ""}`;
                      const margin = o.margin ?? marginByMoment.get(momentKey);
                      return (
                      <TableRow key={o.id || idx} className="border-border/50 text-xs hover:bg-muted/30">
                        <TableCell className="font-medium">
                          {marketLabel(o.market || o.market_type)}
                        </TableCell>
                        <TableCell className="font-semibold">
                          {o.selection || outcomeSideLabel(o.outcome, playerA, playerB)}
                        </TableCell>
                        <TableCell className="font-mono text-right font-bold text-foreground">
                          {Number(o.odds).toFixed(2)}
                        </TableCell>
                        <TableCell className="font-mono text-right text-muted-foreground">
                          {probability !== null ? `${(probability * 100).toFixed(1)}%` : "—"}
                        </TableCell>
                        <TableCell className="font-mono text-right text-muted-foreground">
                          {margin !== undefined ? `${(margin * 100).toFixed(1)}%` : "—"}
                        </TableCell>
                      </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </TabsContent>

          {/* 2. ML Анализ */}
          <TabsContent value="ml" className="mt-3 space-y-3">
            {orderedPreds.length === 0 ? (
              <div className="text-center text-muted-foreground py-8 text-xs border rounded-md border-border/60">
                Модель рассчитывает вектор вероятностей для данного матча...
              </div>
            ) : (
              orderedPreds.map((p, idx) => (
                <div
                  key={p.id || idx}
                  className="rounded-md border border-border/60 p-3.5 bg-muted/20 space-y-2.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-semibold text-sm text-foreground">
                      Рынок: {p.market}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <HintTooltip content={isCompositePrediction(p) ? compositeVerdict(p, playerA, playerB) : decisionStatus(p, evt.sport_code).title}>
                        <Badge
                          variant="outline"
                          className={
                            decisionStatus(p, evt.sport_code).chosen
                              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[11px]"
                              : "text-muted-foreground text-[11px]"
                          }
                        >
                          {decisionStatus(p, evt.sport_code).label}
                        </Badge>
                      </HintTooltip>
                      {p.is_shadow && (
                        <Badge
                          variant="outline"
                          className="border-indigo-500/40 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 text-[11px]"
                        >
                          Тень
                        </Badge>
                      )}
                      <Badge
                        variant="outline"
                        className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 text-[11px]"
                      >
                        {p.model_version || "tennis_v1.0"}
                      </Badge>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 py-1 text-center">
                    <div className="p-2.5 rounded bg-card border border-border/40">
                      <div className="text-[10px] text-muted-foreground">P(Модель)</div>
                      <div className="text-sm font-bold font-mono text-emerald-600 dark:text-emerald-400">
                        {(p.model_probability * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div className="p-2.5 rounded bg-card border border-border/40">
                      <div className="text-[10px] text-muted-foreground">Линия Fonbet</div>
                      <div className="text-sm font-bold font-mono text-foreground">
                        {Number(p.bookmaker_odds).toFixed(2)}
                      </div>
                    </div>
                    <div className="p-2.5 rounded bg-card border border-border/40">
                      <div className="text-[10px] text-muted-foreground">Математический перевес</div>
                      <div
                        className={`text-sm font-bold font-mono ${
                          p.edge > 0
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-muted-foreground"
                        }`}
                      >
                        {p.edge > 0 ? "+" : ""}
                        {(p.edge * 100).toFixed(1)}%
                      </div>
                    </div>
                  </div>
                  {isCompositePrediction(p) && (
                    <HintTooltip content={compositeVerdict(p, playerA, playerB)} side="bottom">
                      <div className="rounded border border-indigo-500/30 bg-indigo-500/5 px-2 py-1.5 text-[11px] text-indigo-700 dark:text-indigo-300">
                        Составная модель — {decisionStatus(p, evt.sport_code).label}
                      </div>
                    </HintTooltip>
                  )}
                </div>
              ))
            )}
          </TabsContent>

          {/* 3. LLM Контекст & Новости */}
          <TabsContent value="llm" className="mt-3 space-y-3">
            <div className="rounded-md border border-border/60 p-3.5 bg-card/60 space-y-2.5">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4 text-emerald-500" />
                  <span className="text-xs font-semibold text-foreground">
                    {primaryLlm
                      ? "Аналитическое резюме локальной языковой модели"
                      : "Резюме по прогнозу ML — вердикт языковой модели ещё не записан"}
                  </span>
                </div>
                {primaryLlm && (
                  <Badge
                    variant="outline"
                    className={`text-[11px] font-semibold ${verdictBadgeClass(primaryLlm.verdict)}`}
                  >
                    {localizeVerdict(primaryLlm.verdict)}
                  </Badge>
                )}
              </div>

              <p className="text-xs text-muted-foreground leading-relaxed">
                {primaryLlm
                  ? localizeLlmSummary(primaryLlm.summary || primaryLlm.reasoning)
                  : buildMlFallbackSummary({
                      playerA,
                      playerB,
                      score: getFormattedScore(evt),
                      primaryPred,
                      predictedWinnerName,
                    })}
              </p>

              {primaryLlm && (
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  {(primaryLlm.reason_codes || []).map((code) => (
                    <HintTooltip key={code} content={reasonCodeHint(code)}>
                      <Badge
                        variant="outline"
                        className="text-[10px] bg-muted/40 border-border/50"
                      >
                        {localizeReasonCode(code)}
                      </Badge>
                    </HintTooltip>
                  ))}
                  {primaryLlm.injury_risk && primaryLlm.injury_risk !== "unknown" && (
                    <HintTooltip content={injuryRiskHint(primaryLlm.injury_risk)}>
                      <Badge variant="outline" className="text-[10px] border-border/50">
                        Травма: {localizeRiskLevel(primaryLlm.injury_risk)}
                      </Badge>
                    </HintTooltip>
                  )}
                  {primaryLlm.fatigue_risk && primaryLlm.fatigue_risk !== "unknown" && (
                    <HintTooltip content={fatigueRiskHint(primaryLlm.fatigue_risk)}>
                      <Badge variant="outline" className="text-[10px] border-border/50">
                        Усталость: {localizeRiskLevel(primaryLlm.fatigue_risk)}
                      </Badge>
                    </HintTooltip>
                  )}
                  {primaryLlm.llm_version && (
                    <span className="text-[10px] text-muted-foreground font-mono self-center ml-auto">
                      {primaryLlm.llm_version}
                    </span>
                  )}
                </div>
              )}
            </div>

            <div className="space-y-2">
              <div className="text-xs font-semibold text-foreground">
                Источники и новости
              </div>
              {research.length === 0 ? (
                <div className="text-center text-muted-foreground py-4 text-xs border rounded-md border-border/40">
                  {research_status === "running"
                    ? "Поиск работает"
                    : research_status === "empty"
                      ? "Нет информации о матче в интернете"
                      : "В ожидании обработки"}
                </div>
              ) : (
                research.map((r) => (
                  <div
                    key={r.id}
                    className="rounded-md border border-border/40 p-2.5 bg-muted/20 text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between font-medium gap-2">
                      <span className="text-foreground">
                        {r.title || r.source_title || r.source_domain || "Источник"}
                      </span>
                      {r.url && (
                        <a
                          href={r.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-emerald-500 hover:underline flex items-center gap-1 text-[11px] shrink-0"
                        >
                          Источник <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>
                    <p className="text-muted-foreground text-[11px]">{r.snippet || r.summary}</p>
                  </div>
                ))
              )}
            </div>
          </TabsContent>

          {/* 4. Таймлайн */}
          <TabsContent value="timeline" className="mt-3">
            <div className="rounded-md border border-border/60 p-3 space-y-2.5 max-h-72 overflow-y-auto">
              {timeline.length === 0 ? (
                <div className="text-center text-muted-foreground py-6 text-xs">
                  Хронология матча формируется по мере поступления событий Fonbet...
                </div>
              ) : (
                timeline.map((t) => (
                  <div
                    key={t.id || `${t.observed_at}-${t.score}`}
                    className="flex items-start gap-3 text-xs border-b border-border/30 pb-2 last:border-0 last:pb-0"
                  >
                    <span className="font-mono text-muted-foreground shrink-0 text-[11px]">
                      {formatTime(t.observed_at || t.event_time || t.timestamp)}
                    </span>
                    <div className="space-y-0.5">
                      {t.description && (
                        <p className="font-medium text-foreground">{t.description}</p>
                      )}
                      {t.score && (
                        <p className="text-[11px] font-mono text-emerald-500">Счет: {t.score}</p>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
