"use client";

import * as React from "react";
import {
  Shield,
  Lock,
  User,
  LogOut,
  RefreshCw,
  Zap,
  CheckCircle2,
  AlertCircle,
  Cpu,
  Database,
  Layers,
  Settings,
  ChevronRight,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { getApiBase } from "@/lib/api";
import { getSportNameRu } from "@/lib/formatters";
import { AdminBacktestPanel } from "@/components/dashboard/admin-backtest-panel";
import { AdminModelDetail } from "@/components/dashboard/admin-model-detail";

// Хеш SHA-256 от пароля пользователя "!Zz128500246315"
// Пароль нигде не хранится в открытом виде.
const ADMIN_PASSWORD_SHA256 =
  "74a9bec65c78f58d627cdadbac3403f43094f8f952736029fe9dc5978fe9f8ba";
const ADMIN_USERNAME = "diflector";

async function computeSHA256(text: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(text);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

interface AdminModalProps {
  onResetSimulation: () => Promise<void>;
}

interface ModelLiveRecord {
  id: string;
  model_name: string;
  display_name?: string;
  is_composite?: boolean;
  components?: { id?: string; label?: string }[];
  version_tag: string;
  sport_code: string;
  status: string;
  settled: number;
  wins: number;
  win_rate: number | null;
  match_settled?: number;
  match_win_rate?: number | null;
  set_settled?: number;
  set_win_rate?: number | null;
}

function winRateLabel(rate: number | null, settled: number): string {
  if (!settled || rate === null) return "—";
  return `${Math.round(rate * 100)}%`;
}

function winRateClass(rate: number | null, settled: number): string {
  if (!settled || rate === null) return "text-muted-foreground";
  if (rate >= 0.55) return "text-emerald-500";
  if (rate >= 0.5) return "text-amber-500";
  return "text-red-500";
}

export function AdminModal({ onResetSimulation }: AdminModalProps) {
  const [open, setOpen] = React.useState(false);
  const [isAuthenticated, setIsAuthenticated] = React.useState(false);

  // Форма входа
  const [username, setUsername] = React.useState("diflector");
  const [password, setPassword] = React.useState("");
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = React.useState(false);

  // Состояние действий
  const [actionStatus, setActionStatus] = React.useState<string | null>(null);
  const [actionLoading, setActionLoading] = React.useState<string | null>(null);
  const [modelRows, setModelRows] = React.useState<ModelLiveRecord[] | null>(null);
  const [modelsLoading, setModelsLoading] = React.useState(false);
  const [section, setSection] = React.useState<"overview" | "backtest">("overview");
  const [expandedModelId, setExpandedModelId] = React.useState<string | null>(null);

  // Проверка сессии при открытии
  React.useEffect(() => {
    if (typeof window !== "undefined") {
      const auth = sessionStorage.getItem("neurobet_admin_auth");
      if (auth === "true") {
        setIsAuthenticated(true);
      }
    }
  }, [open]);

  React.useEffect(() => {
    if (!open || !isAuthenticated) return;
    let cancelled = false;
    const load = (first: boolean) => {
      if (first) setModelsLoading(true);
      fetch(`${getApiBase()}/models/live-record`, { cache: "no-store" })
        .then(async (res) => {
          if (!res.ok) throw new Error(String(res.status));
          return res.json();
        })
        .then((data) => {
          if (!cancelled) setModelRows(Array.isArray(data.models) ? data.models : []);
        })
        .catch(() => {
          if (!cancelled && first) setModelRows([]);
        })
        .finally(() => {
          if (!cancelled && first) setModelsLoading(false);
        });
    };
    load(true);
    const timer = window.setInterval(() => load(false), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, isAuthenticated]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setIsLoggingIn(true);

    try {
      const trimmedUser = username.trim();
      const hashedPw = await computeSHA256(password);

      if (trimmedUser === ADMIN_USERNAME && hashedPw === ADMIN_PASSWORD_SHA256) {
        setIsAuthenticated(true);
        sessionStorage.setItem("neurobet_admin_auth", "true");
        setPassword("");
        setErrorMsg(null);
      } else {
        setErrorMsg("Неверное имя пользователя или пароль администратора");
      }
    } catch {
      setErrorMsg("Ошибка криптографической проверки пароля");
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleLogout = () => {
    setIsAuthenticated(false);
    sessionStorage.removeItem("neurobet_admin_auth");
    setPassword("");
    setActionStatus(null);
  };

  const executeAdminAction = async (actionId: string, url: string, label: string) => {
    setActionLoading(actionId);
    setActionStatus(null);
    try {
      const res = await fetch(`${getApiBase()}${url}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
      });
      if (res.ok) {
        setActionStatus(`Действие «${label}» успешно выполнено`);
      } else {
        setActionStatus(`Ошибка выполнения «${label}» (HTTP ${res.status})`);
      }
    } catch {
      setActionStatus(`Ошибка сетевого запроса при выполнении «${label}»`);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-8 gap-1.5 px-2.5 text-xs border-indigo-500/40 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-500/20"
        onClick={() => setOpen(true)}
      >
        <Shield className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Админка</span>
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-6xl max-h-[90vh] overflow-y-auto">
        <DialogHeader className="border-b border-border/50 pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-indigo-500/15 text-indigo-500">
                <Shield className="h-4 w-4" />
              </div>
              <DialogTitle className="text-base sm:text-lg font-bold">
                Панель управления платформой
              </DialogTitle>
            </div>
            {isAuthenticated && (
              <Badge className="bg-emerald-600 text-white text-[10px] gap-1 px-2 py-0">
                <CheckCircle2 className="h-3 w-3" /> diflector
              </Badge>
            )}
          </div>
          <DialogDescription className="text-xs text-muted-foreground mt-1">
            {isAuthenticated
              ? "Доступ администратора активен. Управление симуляцией, моделями и очередями."
              : "Для доступа к функциям управления требуется авторизация."}
          </DialogDescription>
        </DialogHeader>

        {!isAuthenticated ? (
          /* Форма авторизации */
          <form onSubmit={handleLogin} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <User className="h-3.5 w-3.5 text-muted-foreground" />
                Пользователь
              </label>
              <Input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="diflector"
                required
                className="text-xs h-9 bg-background/80"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <Lock className="h-3.5 w-3.5 text-muted-foreground" />
                Пароль
              </label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••••••"
                required
                className="text-xs h-9 bg-background/80"
              />
              <p className="text-[10px] text-muted-foreground">
                Пароль проверяется в браузере и не отправляется открытым текстом.
              </p>
            </div>

            {errorMsg && (
              <div className="flex items-center gap-2 p-2.5 rounded-md border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>{errorMsg}</span>
              </div>
            )}

            <Button
              type="submit"
              disabled={isLoggingIn}
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white text-xs h-9 font-medium"
            >
              {isLoggingIn ? "Проверка..." : "Войти в панель управления"}
            </Button>
          </form>
        ) : (
          /* Панель администратора */
          <div className="space-y-4 py-2">
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={section === "overview" ? "default" : "outline"}
                className="h-8 text-xs"
                onClick={() => setSection("overview")}
              >
                Обзор
              </Button>
              <Button
                size="sm"
                variant={section === "backtest" ? "default" : "outline"}
                className="h-8 text-xs"
                onClick={() => setSection("backtest")}
              >
                Бэктест
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto h-8 text-xs gap-1.5 text-muted-foreground hover:text-foreground"
                onClick={handleLogout}
              >
                <LogOut className="h-3.5 w-3.5" />
                <span>Выйти</span>
              </Button>
            </div>
            {section === "backtest" ? (
              <AdminBacktestPanel />
            ) : (
            <>
            <div className="space-y-2">
              <div className="text-xs font-semibold text-foreground">Модели</div>
              <div className="overflow-x-auto rounded-lg border border-border/60">
                <table className="w-full text-xs">
                  <thead className="bg-muted/50 text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Спорт</th>
                      <th className="px-3 py-2 text-left font-medium">Модель</th>
                      <th className="px-3 py-2 text-left font-medium">Роль</th>
                      <th className="px-3 py-2 text-right font-medium">Завершено</th>
                      <th className="px-3 py-2 text-right font-medium">Побед</th>
                      <th className="px-3 py-2 text-right font-medium">% побед в матче</th>
                      <th className="px-3 py-2 text-right font-medium">% побед в сете</th>
                      <th className="px-3 py-2 text-right font-medium">Общий % побед</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelsLoading && (
                      <tr>
                        <td colSpan={8} className="px-3 py-6 text-center text-muted-foreground">
                          Считаю результаты…
                        </td>
                      </tr>
                    )}
                    {!modelsLoading && (modelRows || []).length === 0 && (
                      <tr>
                        <td colSpan={8} className="px-3 py-6 text-center text-muted-foreground">
                          Активных и теневых моделей нет
                        </td>
                      </tr>
                    )}
                    {!modelsLoading &&
                      (modelRows || []).map((row) => {
                        const status = (row.status || "").toUpperCase();
                        const combat = status === "ACTIVE";
                        const expanded = expandedModelId === row.id;
                        return (
                          <React.Fragment key={row.id}>
                          <tr
                            className="cursor-pointer border-t border-border/50 hover:bg-muted/40"
                            onClick={() => setExpandedModelId(expanded ? null : row.id)}
                          >
                            <td className="px-3 py-2">
                              <span className="inline-flex items-center gap-1">
                                <ChevronRight className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`} />
                                {getSportNameRu(row.sport_code)}
                              </span>
                            </td>
                            <td className="px-3 py-2">
                              <div className="font-medium text-foreground">
                                {row.is_composite ? row.display_name || "Составная модель" : row.model_name}
                              </div>
                              {row.is_composite && (
                                <div className="text-[10px] text-muted-foreground">
                                  Состав: {(row.components || []).map((item) => item.label).filter(Boolean).join(" · ")}
                                </div>
                              )}
                              <div className="font-mono text-[10px] text-muted-foreground" title={row.version_tag}>
                                {row.version_tag}
                              </div>
                            </td>
                            <td className="px-3 py-2">
                              <Badge
                                variant="outline"
                                className={
                                  combat
                                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                                    : "border-indigo-500/40 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400"
                                }
                              >
                                {combat ? "Боевая" : "Тень"}
                              </Badge>
                            </td>
                            <td className="px-3 py-2 text-right font-mono">{row.settled}</td>
                            <td className="px-3 py-2 text-right font-mono">{row.wins}</td>
                            <td
                              className={`px-3 py-2 text-right font-mono font-semibold ${winRateClass(row.match_win_rate ?? null, row.match_settled || 0)}`}
                            >
                              {winRateLabel(row.match_win_rate ?? null, row.match_settled || 0)}
                            </td>
                            <td
                              className={`px-3 py-2 text-right font-mono font-semibold ${winRateClass(row.set_win_rate ?? null, row.set_settled || 0)}`}
                            >
                              {winRateLabel(row.set_win_rate ?? null, row.set_settled || 0)}
                            </td>
                            <td
                              className={`px-3 py-2 text-right font-mono font-semibold ${winRateClass(row.win_rate, row.settled)}`}
                            >
                              {winRateLabel(row.win_rate, row.settled)}
                            </td>
                          </tr>
                          {expanded && (
                            <tr className="border-t border-border/40 bg-muted/20">
                              <td colSpan={8}>
                                <AdminModelDetail modelId={row.id} composite={Boolean(row.is_composite)} />
                              </td>
                            </tr>
                          )}
                          </React.Fragment>
                        );
                      })}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-muted-foreground">
                Процент побед — доля решённых ставок, где сторона модели совпала с исходом. Матч и сет считаются отдельно, общий процент складывает их. Строку можно развернуть: там периоды, график побед и средние показатели. Возвраты и ещё не закрытые ставки в процент не входят. Цифры обновляются каждые 5 секунд, пока это окно открыто.
              </p>
            </div>

            {/* Статус выполнения действий */}
            {actionStatus && (
              <div className="p-2.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-xs flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <span>{actionStatus}</span>
              </div>
            )}

            {/* Блок быстрых действий */}
            <div className="space-y-2">
              <div className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                <Zap className="h-3.5 w-3.5 text-amber-500" />
                Управление процессами и расчетом
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={actionLoading === "settlement"}
                  className="h-9 justify-start text-xs border-border/70 hover:bg-muted"
                  onClick={() =>
                    executeAdminAction(
                      "settlement",
                      "/settlement/sync",
                      "Синхронизация расчётов"
                    )
                  }
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 mr-2 shrink-0 ${
                      actionLoading === "settlement" ? "animate-spin" : ""
                    }`}
                  />
                  <span>Расчет открытых ставок</span>
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={actionLoading === "reconcile"}
                  className="h-9 justify-start text-xs border-border/70 hover:bg-muted"
                  onClick={() =>
                    executeAdminAction(
                      "reconcile",
                      "/bankroll/reconcile",
                      "Сверка журнала"
                    )
                  }
                >
                  <Database className="h-3.5 w-3.5 mr-2 shrink-0 text-emerald-500" />
                  <span>Сверка баланса журнала</span>
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={actionLoading === "train"}
                  className="h-9 justify-start text-xs border-border/70 hover:bg-muted"
                  onClick={() =>
                    executeAdminAction(
                      "train",
                      "/models/train",
                      "Переобучение модели"
                    )
                  }
                >
                  <Cpu className="h-3.5 w-3.5 mr-2 shrink-0 text-indigo-500" />
                  <span>Запуск обучения ML</span>
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={actionLoading === "cooldowns"}
                  className="h-9 justify-start text-xs border-border/70 hover:bg-muted"
                  onClick={() =>
                    executeAdminAction(
                      "cooldowns",
                      "/scheduler/cooldowns/clear",
                      "Сброс пауз очереди"
                    )
                  }
                >
                  <Layers className="h-3.5 w-3.5 mr-2 shrink-0 text-blue-500" />
                  <span>Сброс пауз очереди</span>
                </Button>
              </div>
            </div>

            {/* Блок параметров рисков */}
            <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-2">
              <div className="text-xs font-semibold text-foreground flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <Settings className="h-3.5 w-3.5 text-muted-foreground" />
                  Параметры ограничений риска
                </span>
                <Badge variant="outline" className="text-[10px] font-mono">
                  Активно
                </Badge>
              </div>

              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                <div className="p-2 rounded bg-card border border-border/40">
                  <div className="text-[10px] text-muted-foreground">Режим</div>
                  <div className="font-bold text-foreground mt-0.5">Только Live</div>
                </div>
                <div className="p-2 rounded bg-card border border-border/40">
                  <div className="text-[10px] text-muted-foreground">Мин. перевес</div>
                  <div className="font-bold text-emerald-500 mt-0.5">&gt; 3.0%</div>
                </div>
                <div className="p-2 rounded bg-card border border-border/40">
                  <div className="text-[10px] text-muted-foreground">Ставка</div>
                  <div className="font-bold text-foreground mt-0.5">50 ₽ – 20%</div>
                </div>
              </div>
            </div>

            {/* Сброс симуляции & Выход */}
            <div className="flex items-center justify-between pt-2 border-t border-border/50">
              <Button
                variant="destructive"
                size="sm"
                className="h-8 text-xs bg-red-600/15 text-red-600 dark:text-red-400 hover:bg-red-600/25 border border-red-500/30"
                onClick={async () => {
                  await onResetSimulation();
                  setActionStatus("Виртуальный баланс успешно сброшен к 1 000 ₽");
                }}
              >
                Сбросить симуляцию (1 000 ₽)
              </Button>

              <Button
                variant="ghost"
                size="sm"
                className="h-8 text-xs gap-1.5 text-muted-foreground hover:text-foreground"
                onClick={handleLogout}
              >
                <LogOut className="h-3.5 w-3.5" />
                <span>Выйти</span>
              </Button>
            </div>
            </>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
    </>
  );
}
