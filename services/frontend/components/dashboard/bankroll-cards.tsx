"use client";

import * as React from "react";
import {
  Wallet,
  Landmark,
  ShieldAlert,
  TrendingUp,
  TrendingDown,
  Percent,
  CheckCircle2,
  BarChart3,
  ArrowDownRight,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { BankrollData, PerformanceData } from "@/lib/types";
import { formatCurrency, formatPercent } from "@/lib/formatters";

interface BankrollCardsProps {
  bankroll: BankrollData | null;
  performance: PerformanceData | null;
  loading?: boolean;
}

export function BankrollCards({
  bankroll,
  performance,
  loading = false,
}: BankrollCardsProps) {
  const balance = bankroll?.available_balance ?? bankroll?.balance ?? 1000;
  const equity = bankroll?.total_equity ?? balance;
  const exposure = bankroll?.locked_exposure ?? 0;

  const netPnl = performance?.net_pnl ?? 0;
  // Backend returns ROI in percentage points (e.g. 5.82 means 5.82%).
  const roi = performance?.roi ?? 0;
  const winRate = performance?.win_rate ?? 0;
  const turnover = performance?.total_turnover ?? 0;
  const drawdown = performance?.max_drawdown_pct ?? 0;

  const isPnlPositive = netPnl >= 0;
  const isRoiPositive = roi >= 0;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
      {/* 1. Виртуальный баланс */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Баланс</span>
            <Wallet className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
          </div>
          <div className="font-mono text-sm sm:text-base font-bold tracking-tight text-foreground truncate">
            {formatCurrency(balance)}
          </div>
          <p className="text-[10px] text-muted-foreground truncate">Доступно для ставок</p>
        </CardContent>
      </Card>

      {/* 2. Совокупное Equity */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Капитал счёта</span>
            <Landmark className="h-3.5 w-3.5 text-blue-500 shrink-0" />
          </div>
          <div className="font-mono text-sm sm:text-base font-bold tracking-tight text-foreground truncate">
            {formatCurrency(equity)}
          </div>
          <p className="text-[10px] text-muted-foreground truncate">Баланс + открытые</p>
        </CardContent>
      </Card>

      {/* 3. Открытый риск */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Экспозиция</span>
            <ShieldAlert className="h-3.5 w-3.5 text-amber-500 shrink-0" />
          </div>
          <div className="font-mono text-sm sm:text-base font-bold tracking-tight text-foreground truncate">
            {formatCurrency(exposure)}
          </div>
          <p className="text-[10px] text-muted-foreground truncate">Лимит &le; 20% портфеля</p>
        </CardContent>
      </Card>

      {/* 4. Смоделировано P&L */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Итого прибыль</span>
            {isPnlPositive ? (
              <TrendingUp className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
            ) : (
              <TrendingDown className="h-3.5 w-3.5 text-red-500 shrink-0" />
            )}
          </div>
          <div
            className={`font-mono text-sm sm:text-base font-bold tracking-tight truncate ${
              isPnlPositive
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400"
            }`}
          >
            {isPnlPositive ? `+${formatCurrency(netPnl)}` : formatCurrency(netPnl)}
          </div>
          <p className="text-[10px] text-muted-foreground truncate">Чистый результат</p>
        </CardContent>
      </Card>

      {/* 5. ROI Портфеля */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Доходность</span>
            <Percent className="h-3.5 w-3.5 text-indigo-500 shrink-0" />
          </div>
          <div
            className={`font-mono text-sm sm:text-base font-bold tracking-tight truncate ${
              isRoiPositive
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400"
            }`}
          >
            {formatPercent(roi / 100)}
          </div>
          <p className="text-[10px] text-muted-foreground truncate">К объему оборота</p>
        </CardContent>
      </Card>

      {/* 6. Win Rate */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Процент побед</span>
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
          </div>
          <div className="font-mono text-sm sm:text-base font-bold tracking-tight text-foreground truncate">
            {(winRate * 100).toFixed(1)}%
          </div>
          <p className="text-[10px] text-muted-foreground truncate">Успешных ставок</p>
        </CardContent>
      </Card>

      {/* 7. Оборот ставок */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Оборот</span>
            <BarChart3 className="h-3.5 w-3.5 text-purple-500 shrink-0" />
          </div>
          <div className="font-mono text-sm sm:text-base font-bold tracking-tight text-foreground truncate">
            {formatCurrency(turnover)}
          </div>
          <p className="text-[10px] text-muted-foreground truncate">Суммарный объем</p>
        </CardContent>
      </Card>

      {/* 8. Max Drawdown */}
      <Card className="border-border/60 bg-card/60 hover:bg-card/90 transition-colors">
        <CardContent className="p-3.5 space-y-1">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-[11px] font-medium truncate">Просадка</span>
            <ArrowDownRight className="h-3.5 w-3.5 text-rose-500 shrink-0" />
          </div>
          <div className="font-mono text-sm sm:text-base font-bold tracking-tight text-rose-600 dark:text-rose-400 truncate">
            {(drawdown * 100).toFixed(1)}%
          </div>
          <p className="text-[10px] text-muted-foreground truncate">Макс. просадка</p>
        </CardContent>
      </Card>
    </div>
  );
}
