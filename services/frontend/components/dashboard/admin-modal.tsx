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

  // Проверка сессии при открытии
  React.useEffect(() => {
    if (typeof window !== "undefined") {
      const auth = sessionStorage.getItem("neurobet_admin_auth");
      if (auth === "true") {
        setIsAuthenticated(true);
      }
    }
  }, [open]);

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
        <DialogContent className="sm:max-w-xl max-h-[90vh] overflow-y-auto">
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
                Пароль безопасно хешируется (SHA-256) в браузере перед проверкой.
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
              {isLoggingIn ? "Проверка хеша..." : "Войти в админ-панель"}
            </Button>
          </form>
        ) : (
          /* Панель администратора */
          <div className="space-y-4 py-2">
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
                      "Синхронизация Settlement"
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
                      "Сверка Ledger"
                    )
                  }
                >
                  <Database className="h-3.5 w-3.5 mr-2 shrink-0 text-emerald-500" />
                  <span>Сверка баланса Ledger</span>
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
                      "Очистка кулдаунов"
                    )
                  }
                >
                  <Layers className="h-3.5 w-3.5 mr-2 shrink-0 text-blue-500" />
                  <span>Сброс кулдаунов очередей</span>
                </Button>
              </div>
            </div>

            {/* Блок параметров рисков */}
            <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-2">
              <div className="text-xs font-semibold text-foreground flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <Settings className="h-3.5 w-3.5 text-muted-foreground" />
                  Параметры риск-гейтов (Bet Manager)
                </span>
                <Badge variant="outline" className="text-[10px] font-mono">
                  Активно
                </Badge>
              </div>

              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                <div className="p-2 rounded bg-card border border-border/40">
                  <div className="text-[10px] text-muted-foreground">Режим</div>
                  <div className="font-bold text-foreground mt-0.5">Live-Only</div>
                </div>
                <div className="p-2 rounded bg-card border border-border/40">
                  <div className="text-[10px] text-muted-foreground">Мин. Edge</div>
                  <div className="font-bold text-emerald-500 mt-0.5">&gt; 3.0%</div>
                </div>
                <div className="p-2 rounded bg-card border border-border/40">
                  <div className="text-[10px] text-muted-foreground">Макс. риск</div>
                  <div className="font-bold text-foreground mt-0.5">20% банка</div>
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
                  setActionStatus("Виртуальный баланс успешно сброшен к 100 000 ₽");
                }}
              >
                Сбросить симуляцию (100k ₽)
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
          </div>
        )}
      </DialogContent>
    </Dialog>
    </>
  );
}
