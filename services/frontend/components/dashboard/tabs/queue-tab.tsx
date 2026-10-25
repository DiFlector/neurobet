"use client";

import * as React from "react";
import { Clock, Layers, Zap, CheckCircle2, Search, RefreshCw } from "lucide-react";
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
import { QueueItem, QueueStats, FlashscoreQueueData, TennisSourceStatusData } from "@/lib/types";

interface QueueTabProps {
  queue: QueueItem[];
  stats: QueueStats | null;
  flashscore?: FlashscoreQueueData | null;
  sourceStatus?: TennisSourceStatusData | null;
  flashscoreStale?: boolean;
  onSelectEvent?: (eventId: string) => void;
  onFlashscoreOffsetChange?: (offset: number) => void;
  onFlashscoreFiltersChange?: (filters: { kind?: string; status?: string; mode?: string; search?: string; include_parked?: boolean }) => void;
  onRetryBlockedFlashscoreJob?: (kind: "full_archive" | "player_history", itemId: string) => Promise<boolean>;
  includeCompleted?: boolean;
  onIncludeCompletedChange?: (value: boolean) => void;
  loading?: boolean;
}

export function QueueTab({ queue, stats, flashscore, sourceStatus, flashscoreStale = false, onSelectEvent, onFlashscoreOffsetChange, onFlashscoreFiltersChange, onRetryBlockedFlashscoreJob, includeCompleted = false, onIncludeCompletedChange, loading = false }: QueueTabProps) {
  const [kind, setKind] = React.useState("all");
  const [status, setStatus] = React.useState("all");
  const [mode, setMode] = React.useState("all");
  const [search, setSearch] = React.useState("");
  const [showParked, setShowParked] = React.useState(false);
  const [retryingJobId, setRetryingJobId] = React.useState<string | null>(null);
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
        include_parked: showParked || undefined,
      });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [kind, status, mode, search, showParked, onFlashscoreFiltersChange]);
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
  const historySourceLabels: Record<string, string> = {
    tennis_my_life: "TennisMyLife", tennis_explorer: "TennisExplorer",
    tennis_abstract: "Tennis Abstract", flashscore: "Flashscore",
  };
  const sourceRoleLabels: Record<string, string> = {
    primary: "Основной", supplement: "Дополнение", historical_supplement: "Исторический архив",
    recent_results_supplement: "Свежие результаты",
    temporary_archive: "Временный архив", catalog_supplement: "Каталог матчей",
    catalog_fallback: "Резерв каталога", catalog_candidate: "Кандидат каталога",
    catalog_and_profile_supplement: "Каталог и профили (дополнение)", fallback: "Резерв",
  };
  const sourceStatusLabels: Record<string, string> = {
    fallback_ready: "Резерв готов", not_integrated: "Не подключён",
    credentials_ready: "Архив доступен", raw_archived_and_season_results_normalized: "Результаты загружаются",
    daily_hash_check_and_raw_snapshot: "Сверка архива",
    hourly_free_catalog_supplement: "Каталог обновляется",
    live_catalog_and_profiles: "Каталог и профили доступны", live_catalog_supplement: "Каталог доступен",
    profiles_available: "Профили доступны",
    http_reachable_not_ingested_coverage_unmeasured: "Ожидает интеграции",
    scheduled_recent_results_ingestion_player_coverage_unverified: "Ожидает первого импорта",
    recent_results_ingested_player_career_unverified: "Результаты загружены; история профиля не подтверждена",
    paused_rate_limit: "Пауза по лимиту источника",
    recent_results_refresh_error: "Ошибка обновления результатов",
    paused_daily_budget: "Пауза: дневная квота исчерпана", archiving: "Архив загружается",
    queued: "Ожидает квоты", archive_complete: "Архив завершён", cutoff_reached: "Доступ закрыт по сроку",
  };
  const sourceStatusLabel = (status: string) => status.startsWith("retrospective_ingested:")
    ? "Ретроспективный архив загружен"
    : sourceStatusLabels[status] || status.replaceAll("_", " ");
  const capabilityLabels: Record<string, string> = {
    results: "результаты", serve_statistics: "статистика подачи", partial_pbp: "частичная PBP",
    pbp: "PBP", history: "история", profiles: "профили", selected_pbp: "выборочная PBP",
    shot_statistics: "удары", match_catalog: "каталог матчей", scoreboard: "табло", historical_pbp: "архивная PBP",
    match_history: "история игрока", point_by_point: "очковая лента",
    public_player_profiles: "публичные профили игроков",
  };

  return (
    <div className="space-y-6">
      <Card className="border-border/60 bg-card/80">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="text-base font-semibold">Источники теннисных данных</CardTitle>
          <CardDescription className="text-xs">
            Возможности и состояние HTTP-источников. Flashscore остаётся резервом для непокрытых данных.
            {sourceStatus?.generated_at ? ` Срез: ${new Date(sourceStatus.generated_at).toLocaleTimeString("ru-RU")}` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-4 pt-2">
          {sourceStatus?.sources?.length ? (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {sourceStatus.sources.map((source) => {
                const jobs = Object.values(source.jobs || {}).reduce((sum, count) => sum + count, 0);
                const docs = Object.values(source.point_documents || {}).reduce((sum, count) => sum + count, 0);
                const playerCoverage = source.player_coverage || [];
                const pbpImport = source.offline_imports?.pbp_backfill;
                const canonicalImport = source.offline_imports?.canonical_projection_backfill;
                return (
                  <div key={source.source} className="min-w-0 rounded-md border border-border/60 bg-muted/20 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-semibold">{source.label}</p>
                        <p className="text-[10px] text-muted-foreground">{sourceRoleLabels[source.role] || source.role}</p>
                      </div>
                      <Badge variant="outline" className="shrink-0 text-[9px]">
                        {sourceStatusLabel(source.status)}
                      </Badge>
                    </div>
                    <p className="mt-2 line-clamp-2 text-[10px] text-muted-foreground">
                      {(source.capabilities || []).map((capability) => capabilityLabels[capability] || capability).join(" · ")}
                    </p>
                    <p className="mt-1 text-[10px] text-muted-foreground">Туры: {(source.tours || []).join(" · ")}</p>
                    {source.routing_enabled && Object.keys(source.routing_enabled).length > 0 && (
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        Маршрут: {Object.entries(source.routing_enabled).map(([capability, enabled]) =>
                          `${capabilityLabels[capability] || capability} ${enabled ? "вкл." : "выкл."}`
                        ).join(" · ")}
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                      {source.quota?.daily_limit != null && <span>Квота {source.quota.daily_used ?? 0}/{source.quota.daily_limit} сегодня</span>}
                      {pbpImport && pbpImport.status !== "not_started" && <span>Офлайн PBP: {pbpImport.rows_scanned ?? 0} строк · {pbpImport.documents_saved ?? 0} документов</span>}
                      {canonicalImport && canonicalImport.status !== "not_started" && <span>Офлайн история: {canonicalImport.rows_scanned ?? 0} строк · {canonicalImport.projected ?? 0} новых · {canonicalImport.merged ?? 0} объединено · {canonicalImport.ambiguous ?? 0} спорных · {canonicalImport.unlinked ?? 0} без связи</span>}
                      {source.recent_window?.normalized_matches != null && <span>Результатов за 7 дней {source.recent_window.normalized_matches}</span>}
                      {source.recent_window?.days_completed?.length ? <span>Дней проверено {source.recent_window.days_completed.length}/7</span> : null}
                      {source.profile_history && <span>Профили: {source.profile_history.fresh_snapshots}/{source.profile_history.profiles_with_snapshot} свежих · записей {source.profile_history.attempted_profiles}</span>}
                      {source.profile_history?.last_attempt?.reason && <span>Последняя проверка профиля: {source.profile_history.last_attempt.reason}{source.profile_history.last_attempt.retry_after ? ` · повтор через ${source.profile_history.last_attempt.retry_after}` : ""}</span>}
                      {source.live_catalog && <span>Матчей в каталоге {source.live_catalog.match_count} · счёт: {source.live_catalog.score_authority === "fonbet" ? "Fonbet" : source.live_catalog.score_authority}</span>}
                      {jobs > 0 && <span>Заданий {jobs}</span>}
                      {docs > 0 && <span>PBP документов {docs}</span>}
                    </div>
                    {playerCoverage.length > 0 && (
                      <div className="mt-2 space-y-1 border-t border-border/40 pt-2">
                        {playerCoverage.map((coverage) => (
                          <p key={`${coverage.capability}:${coverage.status}:${coverage.complete}`} className="text-[10px] text-muted-foreground">
                            {capabilityLabels[coverage.capability] || coverage.capability}: пригодно {coverage.usable_profiles}/{coverage.fresh_profiles} свежих · проверено {coverage.profiles}
                            {coverage.complete ? " · полное" : " · частичное"}
                          </p>
                        ))}
                      </div>
                    )}
                    {source.metrics && (source.metrics.requests > 0 || source.metrics.published > 0 || source.metrics.prevented_flashscore > 0) && (
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border/40 pt-2 text-[10px] text-muted-foreground">
                        <span>HTTP за 24 ч {source.metrics.successful_requests}/{source.metrics.requests} · ошибок {source.metrics.errors}</span>
                        {source.metrics.avg_latency_ms != null && <span>ответ ср./макс. {source.metrics.avg_latency_ms}/{source.metrics.max_latency_ms ?? "—"} мс</span>}
                        {source.metrics.published > 0 && <span>публикаций {source.metrics.published}</span>}
                        {source.metrics.prevented_flashscore > 0 && <span>предотвращено Flashscore {source.metrics.prevented_flashscore}</span>}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">Состояние источников пока недоступно</p>
          )}
        </CardContent>
      </Card>

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
            {flashscore?.worker.player_history_slots && <span className="text-[10px] text-muted-foreground">
              История игроков {flashscore.worker.player_history_slots.active}/{flashscore.worker.player_history_slots.capacity}
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
            <label className="flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={showParked} onChange={(e) => { setShowParked(e.target.checked); onFlashscoreOffsetChange?.(0); }} />Отложенные задания</label>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table className="table-fixed min-w-[1180px]">
              <TableHeader className="bg-muted/40"><TableRow className="text-xs">
                <TableHead className="w-[360px]">Матч / игрок</TableHead><TableHead>Работа</TableHead><TableHead>Статус</TableHead><TableHead>Приоритет</TableHead><TableHead>Ожидание / работа</TableHead><TableHead>Последняя активность</TableHead><TableHead>Следующая попытка</TableHead><TableHead>Причина</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {visible.length === 0 ? <TableRow><TableCell colSpan={8} className="py-8 text-center text-xs text-muted-foreground">{loading ? "Загрузка очереди…" : "Заданий по выбранным фильтрам нет"}</TableCell></TableRow> : visible.map((item) => (
                  <TableRow key={item.id} className="border-border/50 text-xs">
                    <TableCell className="w-[360px] max-w-[360px] overflow-hidden">
                      {item.event_id && onSelectEvent ? <button title={item.label || item.event_id} className="block w-full truncate text-left font-medium text-primary hover:underline" onClick={() => onSelectEvent(item.event_id!)}>{item.label || item.event_id.slice(0, 8)}</button> : <span title={item.label || item.player || "—"} className="block w-full truncate font-medium">{item.label || item.player || "—"}</span>}
                      {item.player && <div className="text-[10px] text-muted-foreground">{item.player}{(item.related_events || 0) > 1 ? ` · матчей: ${item.related_events}` : ""}</div>}
                      {item.kind === "player_history" && item.source_context && (() => {
                        const history = item.source_context.capabilities.match_history;
                        const description = history
                          ? `${historySourceLabels[history.source] || history.source} · ${history.complete ? "полная" : "частичная"} · ${history.fresh ? "свежая" : "устарела"}`
                          : "нет пригодной истории";
                        return <div className="truncate text-[10px] text-muted-foreground" title={`Причина резервного запроса: ${item.source_context.fallback_reason}`}>
                          История: {description}
                        </div>;
                      })()}
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
                    <TableCell className="max-w-[260px]">
                      <div className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate" title={item.last_error || item.reason || ""}>{item.reason || "—"}</span>
                        {(item.kind === "full_archive" || item.kind === "player_history") && item.status === "blocked" && onRetryBlockedFlashscoreJob && <button
                          className="inline-flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-[10px] hover:bg-muted disabled:opacity-50"
                          disabled={retryingJobId !== null}
                          title="Возобновить задание после устранения причины ошибки"
                          onClick={async () => {
                            setRetryingJobId(item.id);
                            try { await onRetryBlockedFlashscoreJob(item.kind as "full_archive" | "player_history", item.id); }
                            finally { setRetryingJobId(null); }
                          }}
                        ><RefreshCw className={`h-3 w-3 ${retryingJobId === item.id ? "animate-spin" : ""}`} />Повторить</button>}
                      </div>
                    </TableCell>
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
