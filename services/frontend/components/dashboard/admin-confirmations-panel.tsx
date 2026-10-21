"use client";

import * as React from "react";
import { Check, ExternalLink, RefreshCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { getApiBase } from "@/lib/api";

type ConfirmationKind = "player_profile" | "event_match";
type ConfirmationStatus = "pending" | "approved" | "rejected" | "superseded";

interface ConfirmationItem {
  id: string;
  kind: ConfirmationKind;
  subject_id: string;
  candidate_id: string;
  status: ConfirmationStatus;
  version: number;
  created_at: string;
  subject: Record<string, any>;
  evidence: Record<string, any>;
}

interface ConfirmationResponse {
  items: ConfirmationItem[];
  total: number;
  counts: Record<string, number>;
  limit: number;
  offset: number;
}

function formatDate(value?: string | null): string {
  if (!value) return "Дата неизвестна";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU");
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function AdminConfirmationsPanel({ onPendingCountChange }: { onPendingCountChange?: (count: number) => void }) {
  const [data, setData] = React.useState<ConfirmationResponse | null>(null);
  const [stale, setStale] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [status, setStatus] = React.useState("pending");
  const [kind, setKind] = React.useState("all");
  const [offset, setOffset] = React.useState(0);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [message, setMessage] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async (initial = false) => {
    if (initial) setLoading(true);
    const params = new URLSearchParams({ status, limit: "25", offset: String(offset) });
    if (kind !== "all") params.set("kind", kind);
    try {
      const response = await fetch(`${getApiBase()}/admin/confirmations?${params}`, {
        cache: "no-store", credentials: "same-origin",
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Сессия администратора истекла" : `HTTP ${response.status}`);
      const next = await response.json() as ConfirmationResponse;
      setData(next);
      setStale(false);
      setError(null);
      onPendingCountChange?.(Number(next.counts?.pending || 0));
    } catch (reason) {
      setStale(true);
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить подтверждения");
    } finally {
      if (initial) setLoading(false);
    }
  }, [status, kind, offset, onPendingCountChange]);

  React.useEffect(() => {
    void load(true);
    const timer = window.setInterval(() => void load(false), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const decide = async (item: ConfirmationItem, decision: "approve" | "reject") => {
    setBusyId(item.id);
    setMessage(null);
    setError(null);
    try {
      const response = await fetch(`${getApiBase()}/admin/confirmations/${item.id}/decision`, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, expected_version: item.version }),
      });
      const body = await response.json().catch(() => ({}));
      if (response.status === 409) {
        await load(false);
        throw new Error(body.detail || "Заявка уже изменилась. Список обновлён, проверьте актуальное предложение.");
      }
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      setMessage(decision === "approve"
        ? "Соответствие подтверждено. Подготовка данных продолжится в фоне."
        : "Кандидат отклонён. Запущен повторный поиск.");
      await load(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить решение");
    } finally {
      setBusyId(null);
    }
  };

  const pages = Math.max(1, Math.ceil((data?.total || 0) / 25));
  const currentPage = Math.floor(offset / 25) + 1;
  const feedback = stale
    ? `Данные устарели. Показан последний успешно загруженный список${error ? ` · ${error}` : ""}`
    : error || message;
  const feedbackIsError = Boolean(error && !stale);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select className="h-8 rounded-md border bg-background px-2 text-xs" value={status}
          onChange={(event) => { setStatus(event.target.value); setOffset(0); }}>
          <option value="pending">Ожидают решения</option>
          <option value="approved">Подтверждённые</option>
          <option value="rejected">Отклонённые</option>
          <option value="superseded">Снятые с рассмотрения</option>
          <option value="all">Все</option>
        </select>
        <select className="h-8 rounded-md border bg-background px-2 text-xs" value={kind}
          onChange={(event) => { setKind(event.target.value); setOffset(0); }}>
          <option value="all">Все типы</option>
          <option value="player_profile">Профили игроков</option>
          <option value="event_match">Связи матчей</option>
        </select>
        <span className="text-xs text-muted-foreground">Ожидают: {data?.counts?.pending ?? "—"}</span>
        <Button size="sm" variant="ghost" className="ml-auto h-8 text-xs" onClick={() => void load(false)}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Обновить
        </Button>
      </div>

      <div
        className={`flex h-9 items-center overflow-hidden rounded-md border px-2 text-xs ${
          !feedback ? "invisible border-transparent" : stale
            ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
            : feedbackIsError
              ? "border-red-500/40 bg-red-500/10 text-red-600"
              : "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
        }`}
        aria-live={feedbackIsError ? "assertive" : "polite"}
        role={feedbackIsError ? "alert" : "status"}
      >
        {feedback && <span className="truncate" title={feedback}>{feedback}</span>}
      </div>

      {loading && !data ? <div className="py-10 text-center text-xs text-muted-foreground">Загружаю заявки…</div> : null}
      {!loading && (data?.items.length || 0) === 0 && <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        {status === "pending" ? "Нет заявок, ожидающих решения" : "Для выбранного фильтра записей нет"}
      </div>}

      <div className="max-h-[52vh] space-y-2 overflow-y-auto pr-1">
        {(data?.items || []).map((item) => {
          const candidate = item.evidence?.candidate || item.evidence?.source_match || {};
          const isPlayer = item.kind === "player_profile";
          const candidateName = isPlayer
            ? candidate.name || `Flashscore ID ${item.candidate_id}`
            : candidate.home_name && candidate.away_name
              ? `${candidate.home_name} — ${candidate.away_name}`
              : `Матч Flashscore ${item.candidate_id}`;
          const sourceUrl = candidate.profile_url || candidate.url || item.evidence?.source_url;
          const isPending = item.status === "pending";
          return (
            <Card key={item.id} className="border-border/70">
              <CardHeader className="space-y-2 p-3 pb-1">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                      {isPlayer ? "Подтвердите соответствие имени" : "Подтвердите соответствие матча"}
                    </div>
                    <div className="mt-1 text-sm font-semibold">{item.subject?.name || "Событие Fonbet"}</div>
                  </div>
                  <Badge variant="outline" className="shrink-0 text-[10px]">{item.status}</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-2 p-3 pt-1">
                <div className="grid gap-2 text-xs sm:grid-cols-2">
                  <div className="rounded-md bg-muted/40 p-2">
                    <div className="text-[10px] text-muted-foreground">Fonbet</div>
                    <div className="font-medium">{item.subject?.name || "—"}</div>
                    {!isPlayer && item.subject?.score && <div className="mt-1 text-muted-foreground">Счёт: {displayValue(item.subject.score)}</div>}
                  </div>
                  <div className="rounded-md bg-muted/40 p-2">
                    <div className="text-[10px] text-muted-foreground">Flashscore</div>
                    <div className="font-medium">{candidateName}</div>
                    <div className="mt-1 break-all font-mono text-[10px] text-muted-foreground">ID: {item.candidate_id}</div>
                  </div>
                </div>
                {!isPlayer && <div className="text-[11px] text-muted-foreground">
                  {candidate.tournament || item.evidence?.tournament || item.evidence?.source_match?.tournament || "Турнир не указан"} · {formatDate(candidate.played_at || item.evidence?.source_match?.played_at || item.subject?.scheduled_start_at)}
                  {(candidate.score || item.evidence?.source_match?.score) && ` · ${displayValue(candidate.score || item.evidence?.source_match?.score)}`}
                </div>}
                {sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[11px] text-blue-600 hover:underline">
                  Открыть Flashscore <ExternalLink className="h-3 w-3" />
                </a>}
                {isPending && <div className="flex justify-end gap-2 pt-1">
                  <Button size="sm" variant="outline" disabled={busyId === item.id} className="h-8 text-xs"
                    onClick={() => void decide(item, "reject")}>
                    <X className="mr-1 h-3.5 w-3.5" /> Нет
                  </Button>
                  <Button size="sm" disabled={busyId === item.id} className="h-8 text-xs"
                    onClick={() => void decide(item, "approve")}>
                    <Check className="mr-1 h-3.5 w-3.5" /> Да
                  </Button>
                </div>}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {data && data.total > 25 && <div className="flex items-center justify-between border-t pt-2 text-xs text-muted-foreground">
        <span>Страница {currentPage} из {pages} · записей: {data.total}</span>
        <div className="flex gap-1">
          <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Назад</Button>
          <Button size="sm" variant="outline" disabled={offset + 25 >= data.total} onClick={() => setOffset(offset + 25)}>Далее</Button>
        </div>
      </div>}
    </div>
  );
}
