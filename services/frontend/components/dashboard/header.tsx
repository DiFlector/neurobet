"use client";

import * as React from "react";
import { Trophy, Radio, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { ResetSimulationDialog } from "./reset-modal";
import { AdminModal } from "./admin-modal";

interface HeaderProps {
  sseConnected: boolean;
  activeSportName: string;
  onResetSimulation: () => Promise<void>;
  isResetting?: boolean;
}

export function Header({
  sseConnected,
  activeSportName,
  onResetSimulation,
  isResetting,
}: HeaderProps) {
  // Обратный отсчет до следующего сбора парсера Fonbet (случайный джиттер от 5 до 10 секунд каждый раз)
  const getRandomInterval = React.useCallback(
    () => Math.floor(Math.random() * 6) + 5,
    []
  );
  const [parserCountdown, setParserCountdown] = React.useState(7);

  React.useEffect(() => {
    setParserCountdown(getRandomInterval());
    const timer = setInterval(() => {
      setParserCountdown((prev) => (prev <= 1 ? getRandomInterval() : prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [getRandomInterval]);

  return (
    <header className="sticky top-0 z-40 w-full border-b border-border/40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto flex h-16 items-center justify-between px-4 sm:px-6">
        {/* Left: Brand & Sport Badge */}
        <div className="flex items-center gap-3 sm:gap-4">
          <div className="flex items-center gap-2.5 font-bold tracking-tight text-xl">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-500 ring-1 ring-emerald-500/30">
              <Trophy className="h-5 w-5" />
            </div>
            <span className="text-foreground">
              Neuro<span className="text-emerald-500">bet</span>
            </span>
          </div>

          <Badge
            variant="outline"
            className="hidden sm:inline-flex border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium text-xs px-2.5 py-0.5"
          >
            {activeSportName || "Теннис MVP"}
          </Badge>
        </div>

        {/* Right: Data update timer, SSE status, Admin, Reset button & Theme toggle */}
        <div className="flex items-center gap-2 sm:gap-3">
          {/* Data Update Countdown Timer */}
          <div
            className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium border border-blue-500/30 bg-blue-500/10 text-blue-600 dark:text-blue-400 select-none shadow-2xs"
            title="Интервал опроса линии Fonbet (рандомный джиттер от 5 до 10 секунд). Следующий сбор живых коэффициентов через указанное время."
          >
            <RefreshCw
              className="h-3 w-3 text-blue-500 animate-spin"
              style={{ animationDuration: "3s" }}
            />
            <span className="font-mono">Обновление данных: {parserCountdown}с</span>
          </div>

          {/* SSE Connection Pill */}
          <div
            className={`flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium border transition-colors ${
              sseConnected
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                : "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400"
            }`}
          >
            <span className="relative flex h-2 w-2">
              {sseConnected && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
              )}
              <span
                className={`relative inline-flex h-2 w-2 rounded-full ${
                  sseConnected ? "bg-emerald-500" : "bg-amber-500"
                }`}
              ></span>
            </span>
            <Radio className="h-3.5 w-3.5 hidden sm:inline" />
            <span className="hidden xs:inline">
              {sseConnected ? "Стрим активен" : "Подключение..."}
            </span>
          </div>

          {/* Admin Modal */}
          <AdminModal onResetSimulation={onResetSimulation} />

          {/* Reset Simulation Dialog */}
          <ResetSimulationDialog
            onReset={onResetSimulation}
            isResetting={isResetting}
          />

          {/* Light / Dark Mode Toggle */}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
