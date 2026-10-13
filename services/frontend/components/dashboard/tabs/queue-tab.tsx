"use client";

import * as React from "react";
import { Clock, Layers, Zap, CheckCircle2 } from "lucide-react";
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
import { QueueItem, QueueStats } from "@/lib/types";

interface QueueTabProps {
  queue: QueueItem[];
  stats: QueueStats | null;
  loading?: boolean;
}

export function QueueTab({ queue, stats, loading = false }: QueueTabProps) {
  const queueLength = stats?.queue_length ?? queue.length;
  const processedTotal = stats?.processed_total ?? 0;
  const runningCount = stats?.running_count ?? queue.filter((item) => item.status === "PROCESSING").length;

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
    </div>
  );
}
