"use client";

import * as React from "react";
import { Clock, Layers, Zap, CheckCircle2, Search } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { QueueItem, QueueStats, FlashscoreQueueData } from "@/lib/types";

interface QueueTabProps {
  queue: QueueItem[];
  stats: QueueStats | null;
  flashscore?: FlashscoreQueueData | null;
  flashscoreStale?: boolean;
  onSelectEvent?: (eventId: string) => void;
  onFlashscoreOffsetChange?: (offset: number) => void;
  onFlashscoreFiltersChange?: (filters: { kind?: string; status?: string; mode?: string; search?: string }) => void;
  includeCompleted?: boolean;
  onIncludeCompletedChange?: (value: boolean) => void;
  loading?: boolean;
}

export function QueueTab({ queue, stats, flashscore, flashscoreStale = false, onSelectEvent, onFlashscoreOffsetChange, onFlashscoreFiltersChange, includeCompleted = false, onIncludeCompletedChange, loading = false }: QueueTabProps) {
  const [kind, setKind] = React.useState("all");
  const [status, setStatus] = React.useState("all");
  const [mode, setMode] = React.useState("all");
  const [search, setSearch] = React.useState("");
  const queueLength = stats?.queue_length ?? queue.length;
  const processedTotal = stats?.processed_total ?? 0;
  const runningCount = stats?.running_count ?? queue.filter((item) => item.status === "PROCESSING").length;
  React.useEffect(() => {
    const timer = window.setTimeout(() => {
      onFlashscoreFiltersChange?.({
        kind: kind === "all" ? undefined : kind,
        status: status === "all" ? undefined : status,
        mode: mode === "all" ? undefined : mode,
        search: search.trim() || undefined,
      });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [kind, status, mode, search, onFlashscoreFiltersChange]);
  const filtered = flashscore?.items || [];
  const pageCount = Math.max(1, Math.ceil((flashscore?.total || 0) / 50));
  const pageIndex = Math.floor((flashscore?.offset || 0) / 50);
  const visible = filtered;
  const statusLabels: Record<string, string> = {
    queued: "В очереди", running: "Выполняется", retry: "Повтор запланирован",
    blocked: "Источник недоступен", identity_confirmation_required: "Ожидает подтверждения",
    waiting_confirmation: "Ожидает подтверждения", waiting_players: "Ожидает профили игроков",
    waiting_match: "Ожидает связь матча", ambiguous: "Ожидает подтверждения",
    failed: "Источник недоступен", completed: "Готово", finalized: "Готово", skipped: "Пропущено",
    matched: "Матч найден",
  };
  const kindLabels: Record<string, string> = {
    player_history: "История игрока", match_lookup: "Поиск матча", pbp: "Очковая лента", full_archive: "Полный архив",
  };

  return (
    <div className="space-y-6">
      {/* 3 Карточки статистики шедулера */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
        <Card className="border-border/60 bg-card/60">
          <CardContent className="p-4 flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground font-medium">Задач в очереди</p>
              <p className="text-2xl font-bold font-mono text-foreground">{queueLength}</p>
            </div>
            <div className="p-3 rounded-lg bg-blue-500/10 text-blue-500">
              <Layers className="h-5 w-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/60">
          <CardContent className="p-4 flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground font-medium">Обработано событий</p>
              <p className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
                {processedTotal}
              </p>
            </div>
            <div className="p-3 rounded-lg bg-emerald-500/10 text-emerald-500">
              <CheckCircle2 className="h-5 w-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/60">
          <CardContent className="p-4 flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground font-medium">Поиск работает</p>
              <p className="text-2xl font-bold font-mono text-foreground">{runningCount}</p>
            </div>
            <div className="p-3 rounded-lg bg-purple-500/10 text-purple-500">
              <Zap className="h-5 w-5" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Таблица приоритетной очереди */}
      <Card className="border-border/60 bg-card/80">
        <CardHeader className="p-4 pb-2">
          <div className="flex items-center gap-2">
            <Clock className="h-5 w-5 text-emerald-500" />
            <CardTitle className="text-base font-semibold">
              Очередь поиска
            </CardTitle>
          </div>
          <CardDescription className="text-xs">
            Матчи, для которых идёт или ожидается поиск источников в интернете
          </CardDescription>
        </CardHeader>

        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/40">
                <TableRow className="border-border/60 text-xs">
                  <TableHead className="font-semibold">Матч</TableHead>
                  <TableHead className="font-semibold text-center">Статус в очереди</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {queue.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={2}
                      className="text-center text-muted-foreground py-8 text-xs"
                    >
                      Очередь поиска пуста: для текущих матчей поиск не требуется
                    </TableCell>
                  </TableRow>
                ) : (
                  queue.map((item) => (
                    <TableRow
                      key={item.event_id}
                      className="border-border/50 text-xs hover:bg-muted/40"
                    >
                      <TableCell className="font-medium text-foreground">
                        {item.label || item.event_id.slice(0, 8)}
                      </TableCell>
                      <TableCell className="text-center">
                        <Badge
                          variant="outline"
                          className="bg-muted text-[10px] font-mono py-0"
                        >
                          {{
                            QUEUED: "В ожидании обработки",
                            PROCESSING: "Поиск работает",
                            DONE: "Готово",
                            COMPLETED: "Готово",
                          }[(item.status || "QUEUED").toUpperCase()] || "В очереди"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/60 bg-card/80">
        <CardHeader className="p-4 pb-2">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <CardTitle className="text-base font-semibold">Flashscore</CardTitle>
              <CardDescription className="text-xs mt-1">
                {flashscore ? `${flashscore.eligible_matches} подходящих матчей · ${flashscore.prepared_players} подготовленных профилей · ${flashscore.total} заданий` : "Состояние очереди пока недоступно"}
                {flashscoreStale && <span className="ml-2 text-amber-600">{flashscore ? "Показаны сохранённые данные, обновить не удалось" : "Не удалось загрузить очередь; сохранённых данных пока нет"}</span>}
              </CardDescription>
            </div>
            <Badge variant="outline" className="font-mono text-[10px]">
              Слоты {flashscore?.worker.network_slots ?? 0} из {flashscore?.worker.network_slots_limit ?? 2}
            </Badge>
            {flashscore?.worker.browser_lanes && <span className="text-[10px] text-muted-foreground">
              Браузеры: live {flashscore.worker.browser_lanes.live_pbp.active}/{flashscore.worker.browser_lanes.live_pbp.capacity}
              · матчи {flashscore.worker.browser_lanes.current_matches.active}/{flashscore.worker.browser_lanes.current_matches.capacity}
              · архив {flashscore.worker.browser_lanes.backlog.active}/{flashscore.worker.browser_lanes.backlog.capacity}
            </span>}
            <span className="text-[10px] text-muted-foreground">
              {flashscore?.worker.cooldown_until && new Date(flashscore.worker.cooldown_until) > new Date() ? `Пауза источника до ${new Date(flashscore.worker.cooldown_until).toLocaleTimeString("ru-RU")}` : flashscore?.worker.healthy ? "Исполнитель работает" : flashscore?.worker.configured ? "Исполнитель не отвечает" : "Состояние регулятора недоступно"}
            </span>
          </div>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
            <span>В очереди: {flashscore?.statuses.queued ?? 0}</span>
            <span>Выполняется: {flashscore?.statuses.running ?? 0}</span>
            <span>Нужна идентификация: {flashscore?.identity_confirmation_required ?? 0}</span>
            <span>Повторы запланированы: {flashscore?.statuses.retry ?? 0}</span>
            <span>Ошибки: {(flashscore?.statuses.failed ?? 0) + (flashscore?.statuses.blocked ?? 0)}</span>
            <span>Просроченный live PBP: {flashscore?.overdue_pbp ?? 0}</span>
            <span>PBP-документов за час: {flashscore?.documents_saved_last_hour ?? 0}</span>
            <span>Связей за час: {flashscore?.confirmed_links_last_hour ?? 0}</span>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <select className="h-8 rounded-md border bg-background px-2 text-xs" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="all">Все типы</option><option value="player_history">История игрока</option><option value="match_lookup">Поиск матча</option><option value="pbp">Очковая лента</option><option value="full_archive">Полный архив</option>
            </select>
            <select className="h-8 rounded-md border bg-background px-2 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">Все статусы</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <select className="h-8 rounded-md border bg-background px-2 text-xs" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="all">Live и prematch</option><option value="live">Live</option><option value="prematch">Prematch</option>
            </select>
            <div className="relative min-w-[180px] flex-1">
              <Search className="absolute left-2 top-2 h-4 w-4 text-muted-foreground" />
              <input className="h-8 w-full rounded-md border bg-background pl-8 pr-2 text-xs" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Матч или игрок" />
            </div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={includeCompleted} onChange={(e) => onIncludeCompletedChange?.(e.target.checked)} />Завершённые за сутки</label>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/40"><TableRow className="text-xs">
                <TableHead>Матч / игрок</TableHead><TableHead>Работа</TableHead><TableHead>Статус</TableHead><TableHead>Приоритет</TableHead><TableHead>Ожидание / работа</TableHead><TableHead>Последняя активность</TableHead><TableHead>Следующая попытка</TableHead><TableHead>Причина</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {visible.length === 0 ? <TableRow><TableCell colSpan={8} className="py-8 text-center text-xs text-muted-foreground">{loading ? "Загрузка очереди…" : "Заданий по выбранным фильтрам нет"}</TableCell></TableRow> : visible.map((item) => (
                  <TableRow key={item.id} className="border-border/50 text-xs">
                    <TableCell>
                      {item.event_id && onSelectEvent ? <button className="text-left font-medium text-primary hover:underline" onClick={() => onSelectEvent(item.event_id!)}>{item.label || item.event_id.slice(0, 8)}</button> : <span className="font-medium">{item.label || item.player || "—"}</span>}
                      {item.player && <div className="text-[10px] text-muted-foreground">{item.player}{(item.related_events || 0) > 1 ? ` · матчей: ${item.related_events}` : ""}</div>}
                      {item.mode && item.mode !== "archive" && <div className="text-[10px] text-muted-foreground">{item.mode === "live" ? "Live" : "Prematch"}</div>}
                    </TableCell>
                    <TableCell>{kindLabels[item.kind] || item.kind}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-[10px]">{statusLabels[item.status] || item.status}</Badge>
                      {typeof item.attempts === "number" && item.attempts > 0 && <div className="mt-1 text-[10px] text-muted-foreground">Попытка {item.attempts}</div>}
                      {item.lease_expired && <div className="mt-1 text-[10px] text-destructive">Lease истёк</div>}
                    </TableCell>
                    <TableCell>{item.priority ?? "—"}</TableCell>
                    <TableCell>
                      {item.status === "running" && item.attempt_seconds != null ? `${Math.floor(item.attempt_seconds / 60)} мин выполняется` :
                       (item.status === "retry" || item.status === "queued") && item.retry_in_seconds != null ? `повтор через ${Math.max(1, Math.ceil(item.retry_in_seconds / 60))} мин` :
                       item.wait_seconds != null ? `${Math.floor(item.wait_seconds / 60)} мин в очереди` : "—"}
                    </TableCell>
                    <TableCell>{item.last_activity_at ? new Date(item.last_activity_at).toLocaleString("ru-RU") : "—"}</TableCell>
                    <TableCell>{item.next_retry_at ? new Date(item.next_retry_at).toLocaleString("ru-RU") : "—"}</TableCell>
                    <TableCell className="max-w-[220px] truncate" title={item.last_error || item.reason || ""}>{item.reason || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-muted-foreground">
            <span>{flashscore?.total ?? filtered.length} заданий · страница {pageIndex + 1} из {pageCount}</span>
            <div className="flex gap-2"><button className="rounded border px-2 py-1 disabled:opacity-40" disabled={pageIndex === 0} onClick={() => onFlashscoreOffsetChange?.(Math.max(0, (pageIndex - 1) * 50))}>Назад</button><button className="rounded border px-2 py-1 disabled:opacity-40" disabled={pageIndex + 1 >= pageCount} onClick={() => onFlashscoreOffsetChange?.((pageIndex + 1) * 50)}>Дальше</button></div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
