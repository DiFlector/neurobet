"use client";

import * as React from "react";
import { Copy, Play, RotateCcw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { deleteBacktestRun, fetchBacktestRun, fetchBacktestRuns, fetchTennisPolicy, getApiBase, startBacktest } from "@/lib/api";
import { BacktestConfigInput, BacktestModelResult, BacktestRunReport } from "@/lib/types";
import { getSportNameRu } from "@/lib/formatters";

interface RegistryModel {
  id: string;
  model_name: string;
  version_tag: string;
  sport_code: string;
  status: string;
  display_name?: string;
}

const DEFAULT_CONFIG: BacktestConfigInput = {
  market: "match_winner",
  match_limit: 100,
  min_edge: 0.02,
  min_probability: 0.6,
  odds_gate_mode: "range",
  min_odds: 1.4,
  max_odds: 2.1,
  short_odds_lo: 1.3,
  short_odds_hi: 1.4,
  mid_odds_lo: 1.6,
  mid_odds_hi: 1.8,
  match_odds_hi: 2.4,
  observe_set: 1,
  confirmations: 2,
  max_odds_staleness_sec: 15,
  execution_latency_sec: 2,
  allow_slippage: true,
  markets: ["match_winner", "set_winner"],
  initial_bankroll: 5000,
  fixed_stake: 100,
  bankruptcy_threshold: 100,
};

function money(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value).toLocaleString("ru-RU")} ₽`;
}

function etaLabel(seconds: number | null, done: number): string {
  if (!done || seconds === null) return "оцениваю время…";
  if (seconds < 5) return "почти готово";
  if (seconds < 60) return `осталось около ${Math.max(1, Math.round(seconds))} с`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `осталось около ${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `осталось около ${hours} ч ${rest} мин` : `осталось около ${hours} ч`;
}

function supportsSets(model?: RegistryModel): boolean {
  if (!model) return true;
  const tag = model.version_tag || "";
  const name = `${model.model_name || ""} ${model.display_name || ""}`.toLowerCase();
  return tag.startsWith("tennis_v2") || name.includes("composite") || name.includes("состав");
}

function marketsFor(
  scope: "model" | "sport",
  model: RegistryModel | undefined,
  roles: { match_model?: string; set_model?: string },
): Array<"match_winner" | "set_winner"> {
  if (scope === "sport") return ["match_winner", "set_winner"];
  if (!model) return ["match_winner"];
  const identity = `${model.model_name} ${model.version_tag}`;
  const markets: Array<"match_winner" | "set_winner"> = [];
  if (roles.match_model === identity) markets.push("match_winner");
  if (roles.set_model === identity) markets.push("set_winner");
  if (markets.length) return markets;
  return supportsSets(model) ? ["match_winner", "set_winner"] : ["match_winner"];
}

function marketLabel(key: string): string {
  if (key === "match_winner") return "Матч";
  if (key === "set_winner") return "Сеты";
  const set = /^set_winner:(\d+)$/.exec(key);
  if (set) return `Сет ${set[1]}`;
  return key;
}

function inProgress(row: { progress?: { state?: string } }): boolean {
  const state = row.progress?.state;
  return state === "queued" || state === "running";
}

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function when(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusLabel(status: string): string {
  if (status === "queued") return "в очереди";
  if (status === "running") return "считается";
  if (status === "completed") return "готово";
  if (status === "failed") return "ошибка";
  return status;
}

function historyHint(item: BacktestRunReport): string {
  const rows = item.results || [];
  const active = rows.find((row) => row.progress && (row.progress.state === "queued" || row.progress.state === "running"));
  if (active?.progress) {
    return active.progress.state === "queued"
      ? "ожидает"
      : `${active.progress.matches_done} из ${active.progress.matches_total}`;
  }
  if (item.status !== "completed" || rows.length === 0) return statusLabel(item.status);
  if (rows.length === 1) return `${rows[0].bets ?? 0} ставок · ROI ${percent(rows[0].roi)}`;
  return `${rows.length} моделей`;
}

export function AdminBacktestPanel() {
  const [models, setModels] = React.useState<RegistryModel[]>([]);
  const [scope, setScope] = React.useState<"model" | "sport">("model");
  const [modelId, setModelId] = React.useState("");
  const [config, setConfig] = React.useState<BacktestConfigInput>(DEFAULT_CONFIG);
  const [report, setReport] = React.useState<BacktestRunReport | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [openRow, setOpenRow] = React.useState<string | null>(null);
  const [history, setHistory] = React.useState<BacktestRunReport[]>([]);
  const [roles, setRoles] = React.useState<{ match_model?: string; set_model?: string }>({});

  const refreshHistory = React.useCallback(async () => {
    setHistory(await fetchBacktestRuns());
  }, []);

  React.useEffect(() => {
    refreshHistory();
  }, [refreshHistory]);

  React.useEffect(() => {
    fetchTennisPolicy().then((policy) => {
      if (!policy) return;
      setConfig((current) => ({
        ...current,
        odds_gate_mode: policy.match_gate_mode === "range" || policy.set_gate_mode === "range" ? "range" : current.odds_gate_mode,
        min_edge: Number(policy.match_min_edge ?? policy.min_edge ?? current.min_edge),
        min_probability: Number(policy.match_min_probability ?? policy.min_probability ?? current.min_probability),
        confirmations: Number(policy.match_confirmations ?? policy.confirmations ?? current.confirmations),
        short_odds_lo: Number(policy.short_odds_lo ?? current.short_odds_lo),
        short_odds_hi: Number(policy.short_odds_hi ?? current.short_odds_hi),
        mid_odds_lo: Number(policy.mid_odds_lo ?? current.mid_odds_lo),
        mid_odds_hi: Number(policy.mid_odds_hi ?? current.mid_odds_hi),
        match_odds_hi: Number(policy.match_odds_hi ?? current.match_odds_hi),
        observe_set: Number(policy.observe_set ?? current.observe_set),
        min_odds: Number(policy.match_min_odds ?? policy.short_odds_lo ?? current.min_odds),
        max_odds: Number(policy.match_max_odds ?? policy.match_odds_hi ?? current.max_odds),
        execution_latency_sec: 2,
      }));
      setRoles({
        match_model: typeof policy.match_model === "string" ? policy.match_model : undefined,
        set_model: typeof policy.set_model === "string" ? policy.set_model : undefined,
      });
    });
  }, []);

  React.useEffect(() => {
    fetch(`${getApiBase()}/models`, { cache: "no-store" })
      .then((res) => res.json())
      .then((rows: RegistryModel[]) => {
        const usable = (Array.isArray(rows) ? rows : []).filter((row) => {
          const status = (row.status || "").toUpperCase();
          return (status === "ACTIVE" || status === "SHADOW") && !String(row.version_tag || "").startsWith("test_");
        });
        const tennisModels = usable.filter((row) => row.sport_code === "tennis");
        setModels(tennisModels);
        if (tennisModels[0]) setModelId(tennisModels[0].id);
      })
      .catch(() => setModels([]));
  }, []);

  const sportModels = models.filter((row) => row.sport_code === "tennis");

  React.useEffect(() => {
    const model = models.find((row) => row.id === modelId);
    const markets = marketsFor(scope, model, roles);
    setConfig((current) => (current.markets?.join() === markets.join() ? current : { ...current, markets }));
  }, [scope, modelId, roles, models]);

  React.useEffect(() => {
    if (!report?.id || (report.status !== "queued" && report.status !== "running")) return;
    const timer = window.setInterval(async () => {
      const next = await fetchBacktestRun(report.id);
      if (!next) return;
      setReport(next);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [report?.id, report?.status]);

  React.useEffect(() => {
    const active = history.some((item) => item.status === "queued" || item.status === "running");
    if (!active && report?.status !== "queued" && report?.status !== "running") return;
    const timer = window.setInterval(refreshHistory, 4000);
    return () => window.clearInterval(timer);
  }, [history, report?.status, refreshHistory]);

  const update = (key: keyof BacktestConfigInput, value: BacktestConfigInput[keyof BacktestConfigInput]) => {
    setConfig((current) => ({ ...current, [key]: value }));
  };

  const applyStored = (item: BacktestRunReport) => {
    const selector = (item.request?.selector || {}) as {
      scope?: string;
      sport_code?: string;
      model_version_id?: string;
    };
    if (selector.scope === "model" || selector.scope === "sport") setScope(selector.scope);
    if (selector.sport_code === "tennis" && selector.model_version_id) setModelId(String(selector.model_version_id));
    if (item.request?.config) setConfig({ ...DEFAULT_CONFIG, ...item.request.config });
  };

  const rememberRun = (started: BacktestRunReport, request: BacktestRunReport["request"]) => {
    const row: BacktestRunReport = {
      ...started,
      sport_code: started.sport_code || "tennis",
      scope: started.scope || scope,
      status: started.status || "queued",
      created_at: started.created_at || new Date().toISOString(),
      request: started.request || request,
      results: started.results || [],
    };
    setHistory((current) => [row, ...current.filter((item) => item.id !== row.id)].slice(0, 25));
    return row;
  };

  const launch = async () => {
    setError(null);
    setNotice(null);
    setCopied(false);
    setSubmitting(true);
    const request = {
      selector: {
        scope,
        sport_code: "tennis",
        model_version_id: scope === "model" ? modelId : undefined,
      },
      config,
    };
    try {
      const started = await startBacktest(request);
      rememberRun(started, request);
      setReport(null);
      setNotice(started.deduplicated
        ? "Такой прогон уже есть в истории. Его можно открыть там."
        : "Прогон в истории и уже идёт. Окно можно закрыть и открыть его позже.");
      await refreshHistory();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось запустить бэктест");
    } finally {
      setSubmitting(false);
    }
  };

  const openRun = async (runId: string) => {
    setError(null);
    setNotice(null);
    const full = await fetchBacktestRun(runId);
    if (!full) {
      setError("Прогон не найден");
      await refreshHistory();
      return;
    }
    applyStored(full);
    setReport(full);
    setOpenRow(null);
  };

  const replay = async (item: BacktestRunReport) => {
    const selector = (item.request?.selector || {}) as {
      scope?: string;
      sport_code?: string;
      model_version_id?: string;
    };
    if (!selector.sport_code || !item.request?.config) {
      setError("У этого прогона нет сохранённых параметров");
      return;
    }
    if (selector.sport_code !== "tennis") {
      setError("Сейчас повтор доступен только для тенниса");
      return;
    }
    setError(null);
    setNotice(null);
    setCopied(false);
    setSubmitting(true);
    applyStored(item);
    const request = {
      selector: {
        scope: selector.scope === "sport" ? "sport" as const : "model" as const,
        sport_code: "tennis",
        model_version_id: selector.model_version_id ? String(selector.model_version_id) : undefined,
      },
      config: { ...DEFAULT_CONFIG, ...item.request.config },
    };
    try {
      const started = await startBacktest(request);
      rememberRun(started, request);
      setReport(null);
      setNotice(started.deduplicated
        ? "Такой прогон уже есть в истории. Его можно открыть там."
        : "Новый прогон в истории. Окно можно закрыть и открыть его позже.");
      await refreshHistory();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось повторить бэктест");
    } finally {
      setSubmitting(false);
    }
  };

  const removeRun = async (item: BacktestRunReport) => {
    const label = `${getSportNameRu(item.sport_code)}, ${when(item.created_at)}`;
    if (!window.confirm(`Удалить бэктест (${label})? Бумажный отчёт исчезнет, боевые ставки не изменятся.`)) return;
    setError(null);
    try {
      await deleteBacktestRun(item.id);
      if (report?.id === item.id) setReport(null);
      await refreshHistory();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось удалить бэктест");
    }
  };

  const copyJson = async () => {
    if (!report) return;
    await navigator.clipboard.writeText(JSON.stringify(report, null, 2));
    setCopied(true);
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground">Что прогонять</span>
          <select
            className="h-9 w-full rounded-md border border-border bg-background px-2"
            value={scope}
            onChange={(event) => {
              const next = event.target.value as "model" | "sport";
              setScope(next);
              const model = sportModels.find((row) => row.id === modelId);
              setConfig((current) => ({ ...current, markets: marketsFor(next, model, roles) }));
            }}
          >
            <option value="model">Одна модель</option>
            <option value="sport">Все боевые и теневые модели тенниса</option>
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground">Вид спорта</span>
          <div className="flex h-9 items-center rounded-md border border-border bg-muted/40 px-2">Теннис</div>
        </label>
        {scope === "model" && (
          <label className="space-y-1 text-xs sm:col-span-2">
            <span className="text-muted-foreground">Модель</span>
            <select
              className="h-9 w-full rounded-md border border-border bg-background px-2"
              value={modelId}
              onChange={(event) => {
                const nextId = event.target.value;
                setModelId(nextId);
                const model = sportModels.find((row) => row.id === nextId);
                setConfig((current) => ({ ...current, markets: marketsFor("model", model, roles) }));
              }}
            >
              {sportModels.map((row) => (
                <option key={row.id} value={row.id}>
                  {(row.status || "").toUpperCase() === "ACTIVE" ? "Боевая" : "Тень"} · {row.display_name || row.model_name} · {row.version_tag}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <NumberField label="Последних матчей" value={config.match_limit} onChange={(value) => update("match_limit", value)} />
        <NumberField label="Мин. перевес" value={config.min_edge} step="0.01" onChange={(value) => update("min_edge", value)} />
        <NumberField label="Мин. вероятность" value={config.min_probability} step="0.01" onChange={(value) => update("min_probability", value)} />
        <NumberField label="Подтверждений" value={config.confirmations} onChange={(value) => update("confirmations", value)} />
        <NumberField label="Начальный баланс" value={config.initial_bankroll} onChange={(value) => update("initial_bankroll", value)} />
        <NumberField label="Ставка, ₽" value={config.fixed_stake} onChange={(value) => update("fixed_stake", value)} />
        <NumberField label="Порог банкротства" value={config.bankruptcy_threshold} onChange={(value) => update("bankruptcy_threshold", value)} />
        <NumberField label="Свежесть линии, с" value={config.max_odds_staleness_sec} onChange={(value) => update("max_odds_staleness_sec", value)} />
        <NumberField label="Задержка, с" value={config.execution_latency_sec} onChange={(value) => update("execution_latency_sec", value)} />
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground">Окно коэффициентов</span>
          <select
            className="h-9 w-full rounded-md border border-border bg-background px-2"
            value={config.odds_gate_mode}
            onChange={(event) => update("odds_gate_mode", event.target.value)}
          >
            <option value="live">Как в лайве</option>
            <option value="range">Свой диапазон</option>
          </select>
        </label>
        {config.odds_gate_mode === "live" ? (
          <>
            <NumberField label="Короткое окно от" value={config.short_odds_lo || 1.3} step="0.01" onChange={(value) => update("short_odds_lo", value)} />
            <NumberField label="Короткое окно до" value={config.short_odds_hi || 1.4} step="0.01" onChange={(value) => update("short_odds_hi", value)} />
            <NumberField label="Среднее окно от" value={config.mid_odds_lo || 1.6} step="0.01" onChange={(value) => update("mid_odds_lo", value)} />
            <NumberField label="Среднее окно до" value={config.mid_odds_hi || 1.8} step="0.01" onChange={(value) => update("mid_odds_hi", value)} />
            <NumberField label="Матч до" value={config.match_odds_hi || 2.4} step="0.01" onChange={(value) => update("match_odds_hi", value)} />
            <NumberField label="Сет только смотреть" value={config.observe_set || 1} onChange={(value) => update("observe_set", value)} />
          </>
        ) : (
          <>
            <NumberField label="Мин. коэффициент" value={config.min_odds} step="0.01" onChange={(value) => update("min_odds", value)} />
            <NumberField label="Макс. коэффициент" value={config.max_odds} step="0.01" onChange={(value) => update("max_odds", value)} />
          </>
        )}
      </div>
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={config.allow_slippage}
          onChange={(event) => update("allow_slippage", event.target.checked)}
        />
        Отклонять ставку, если за время задержки перевес исчез
      </label>
      <div className="space-y-1 text-xs">
        <span className="text-muted-foreground">Рынки</span>
        <div className="flex flex-wrap gap-3">
          {([
            ["match_winner", "Победа в матче"],
            ["set_winner", "Победа в сете"],
          ] as const).map(([id, label]) => (
            <label key={id} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={(config.markets || []).includes(id)}
                onChange={(event) => {
                  const current = new Set(config.markets || []);
                  if (event.target.checked) current.add(id);
                  else current.delete(id);
                  const next = (["match_winner", "set_winner"] as const).filter((item) => current.has(item));
                  if (next.length === 0) return;
                  update("markets", [...next]);
                }}
              />
              {label}
            </label>
          ))}
        </div>
        <p className="text-[10px] text-muted-foreground">
          Логистика tennis_v2.3.0 отмечает матч, составная tennis_v1.0.0 отмечает сеты. В прогоне всех моделей каждая ставит только свой рынок, даже если отмечены оба.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" className="h-8 text-xs" disabled={submitting || (scope === "model" && !modelId)} onClick={launch}>
          <Play className="mr-1 h-3.5 w-3.5" />
          {submitting ? "Ставлю в очередь…" : "Запустить бэктест"}
        </Button>
        <Button size="sm" variant="outline" className="h-8 text-xs" disabled={!report?.request?.config} onClick={() => report && replay(report)}>
          <RotateCcw className="mr-1 h-3.5 w-3.5" />
          Ещё раз
        </Button>
        <Button size="sm" variant="outline" className="h-8 text-xs" disabled={!report} onClick={copyJson}>
          <Copy className="mr-1 h-3.5 w-3.5" />
          {copied ? "Скопировано" : "Копировать JSON"}
        </Button>
        {report && <span className="text-[11px] text-muted-foreground">{statusLabel(report.status)} · {when(report.created_at)}</span>}
      </div>
      {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
      {error && <p className="text-xs text-red-500">{error}</p>}
      {report?.error && <p className="text-xs text-red-500">{report.error}</p>}
      <p className="text-[10px] text-muted-foreground">
        Прогон идёт по полной ленте завершённых матчей и сохранённому на момент среза контексту. Боевой баланс и журнал ставок не меняются. Если баланс падает ниже порога, банкротство учитывается и баланс возвращается к начальному.
      </p>

      {report && (
      <div className="overflow-x-auto rounded-lg border border-border/60">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr>
              <th className="px-2 py-2 text-left font-medium">Модель</th>
              <th className="px-2 py-2 text-left font-medium">Рынок</th>
              <th className="px-2 py-2 text-right font-medium">Матчи</th>
              <th className="px-2 py-2 text-right font-medium">Ставки</th>
              <th className="px-2 py-2 text-right font-medium">Победы</th>
              <th className="px-2 py-2 text-right font-medium">% побед</th>
              <th className="px-2 py-2 text-right font-medium">ROI</th>
              <th className="px-2 py-2 text-right font-medium">Баланс</th>
              <th className="px-2 py-2 text-right font-medium">Просадка</th>
              <th className="px-2 py-2 text-right font-medium">Банкротства</th>
            </tr>
          </thead>
          <tbody>
            {(report?.results || []).length === 0 && (
              <tr>
                <td colSpan={10} className="px-3 py-6 text-center text-muted-foreground">
                  {report?.status === "queued" || report?.status === "running" ? "Прогон ещё считается" : "Откройте прогон из истории"}
                </td>
              </tr>
            )}
            {(report?.results || []).map((row) => (
              <ResultRow
                key={row.model_version_id || row.version_tag}
                row={row}
                open={openRow === row.model_version_id}
                onToggle={() => setOpenRow(openRow === row.model_version_id ? null : row.model_version_id)}
              />
            ))}
          </tbody>
        </table>
      </div>
      )}

      <div className="space-y-2">
        <div className="text-xs font-medium">История</div>
        {history.length === 0 && <p className="text-[11px] text-muted-foreground">Сохранённых прогонов пока нет.</p>}
        <div className="space-y-1">
          {history.map((item) => {
            const selected = report?.id === item.id;
            const scopeText = item.scope === "sport" ? "все модели тенниса" : "одна модель";
            return (
              <div
                key={item.id}
                className={`flex flex-wrap items-center gap-2 rounded-md border px-2 py-1.5 text-[11px] ${selected ? "border-indigo-500/70 bg-indigo-500/10" : "border-border/60"}`}
              >
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => openRun(item.id)}>
                  <span className="font-medium">{when(item.created_at)}</span>
                  <span className="text-muted-foreground"> · {getSportNameRu(item.sport_code)} · {scopeText} · {historyHint(item)}</span>
                </button>
                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-[11px]" onClick={() => openRun(item.id)}>
                  Открыть
                </Button>
                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-[11px]" onClick={() => replay(item)}>
                  <RotateCcw className="mr-1 h-3 w-3" />
                  Ещё раз
                </Button>
                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-[11px]" onClick={() => removeRun(item)}>
                  <Trash2 className="mr-1 h-3 w-3" />
                  Удалить
                </Button>
              </div>
            );
          })}
        </div>
        <p className="text-[10px] text-muted-foreground">
          В истории не больше 25 прогонов. Новый запуск при заполненном списке удаляет самый старый завершённый прогон. Идущий прогон не удаляется.
        </p>
      </div>
    </div>
  );
}

function NumberField({
  label,
  value,
  step = "1",
  onChange,
}: {
  label: string;
  value: number;
  step?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-y-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <Input
        type="number"
        step={step}
        value={value}
        className="h-9 text-xs"
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function ResultRow({ row, open, onToggle }: { row: BacktestModelResult; open: boolean; onToggle: () => void }) {
  if (inProgress(row)) {
    const done = row.progress?.matches_done ?? 0;
    const total = row.progress?.matches_total ?? 0;
    const width = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    const waiting = row.progress?.state === "queued";
    return (
      <tr className="border-t border-border/50">
        <td className="px-2 py-2">
          <div className="font-medium">{row.model_name}</div>
          <div className="font-mono text-[10px] text-muted-foreground">{row.version_tag}</div>
        </td>
        <td colSpan={9} className="px-2 py-2">
          <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
            <span>{waiting ? "Ожидает" : `${done} из ${total} матчей`}</span>
            <span>{waiting ? `0 из ${total}` : etaLabel(row.progress?.eta_seconds ?? null, done)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${width}%` }} />
          </div>
        </td>
      </tr>
    );
  }
  return (
    <>
      <tr className="cursor-pointer border-t border-border/50" onClick={onToggle}>
        <td className="px-2 py-2">
          <div className="font-medium">{row.model_name}</div>
          <div className="font-mono text-[10px] text-muted-foreground">{row.version_tag}</div>
        </td>
        <td className="px-2 py-2">Всего</td>
        <td className="px-2 py-2 text-right font-mono">{row.matches_seen}</td>
        <td className="px-2 py-2 text-right font-mono">{row.bets}</td>
        <td className="px-2 py-2 text-right font-mono">{row.wins}</td>
        <td className="px-2 py-2 text-right font-mono">{percent(row.win_rate)}</td>
        <td className="px-2 py-2 text-right font-mono">{percent(row.roi)}</td>
        <td className="px-2 py-2 text-right font-mono">{money(row.final_balance)}</td>
        <td className="px-2 py-2 text-right font-mono">{money(row.max_drawdown)}</td>
        <td className="px-2 py-2 text-right font-mono">{row.bankruptcies}</td>
      </tr>
      {Object.entries(row.by_market || {}).map(([key, stats]) => (
        <tr key={key} className="border-t border-border/40 bg-muted/10">
          <td className="px-2 py-1.5" />
          <td className="px-2 py-1.5 text-muted-foreground">{marketLabel(key)}</td>
          <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">—</td>
          <td className="px-2 py-1.5 text-right font-mono">{stats.bets}</td>
          <td className="px-2 py-1.5 text-right font-mono">{stats.wins}</td>
          <td className="px-2 py-1.5 text-right font-mono">{percent(stats.win_rate)}</td>
          <td className="px-2 py-1.5 text-right font-mono">{percent(stats.roi)}</td>
          <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">—</td>
          <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">—</td>
          <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">—</td>
        </tr>
      ))}
      {open && (
        <tr className="border-t border-border/40 bg-muted/20">
          <td colSpan={10} className="px-3 py-2 text-[11px] text-muted-foreground">
            <div>
              PnL {money(row.net_pnl)}, оборот {money(row.turnover)}, минимум {money(row.min_balance)}, поражения {row.losses}, возвраты {row.voids}.
            </div>
            <div className="mt-1">
              Отклонения: {Object.entries(row.rejection_counts || {}).map(([key, count]) => `${key} ${count}`).join(", ") || "нет"}
            </div>
            <div className="mt-1">Ставок в отчёте: {(row.bet_records || []).length}. Полный список попадает в JSON.</div>
          </td>
        </tr>
      )}
    </>
  );
}
