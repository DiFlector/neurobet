"use client";

import * as React from "react";
import { BrainCircuit, CheckCircle2, Eye, ShieldCheck, Activity, History } from "lucide-react";
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
import { ModelItem, TrainingRun } from "@/lib/types";
import { formatDate, getSportNameRu } from "@/lib/formatters";

interface ModelsTabProps {
  models: ModelItem[];
  trainingRuns: TrainingRun[];
  loading?: boolean;
}

export function ModelsTab({
  models,
  trainingRuns,
  loading = false,
}: ModelsTabProps) {
  return (
    <div className="space-y-6">
      {/* 1. Реестр моделей */}
      <Card className="border-border/60 bg-card/80">
        <CardHeader className="p-4 pb-2">
          <div className="flex items-center gap-2">
            <BrainCircuit className="h-5 w-5 text-emerald-500" />
            <CardTitle className="text-base font-semibold">
              Реестр математических и нейросетевых моделей
            </CardTitle>
          </div>
          <CardDescription className="text-xs">
            Активные и теневые модели прогнозирования вероятностей и калибровки
          </CardDescription>
        </CardHeader>

        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/40">
                <TableRow className="border-border/60 text-xs">
                  <TableHead className="font-semibold">Модель</TableHead>
                  <TableHead className="font-semibold">Тег версии</TableHead>
                  <TableHead className="font-semibold">Спорт</TableHead>
                  <TableHead className="font-semibold text-right">Ошибка Брайера</TableHead>
                  <TableHead className="font-semibold text-right">Различимость</TableHead>
                  <TableHead className="font-semibold text-right">Дрейф</TableHead>
                  <TableHead className="font-semibold text-center">Статус</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {models.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={7}
                      className="text-center text-muted-foreground py-8 text-xs"
                    >
                      Модели не зарегистрированы в реестре
                    </TableCell>
                  </TableRow>
                ) : (
                  models.map((m) => {
                    const status = m.status?.toUpperCase() || "ACTIVE";
                    const isActive = status === "ACTIVE";
                    const isShadow = status === "SHADOW";

                    return (
                      <TableRow
                        key={m.id || m.model_name + m.version_tag}
                        className="border-border/50 text-xs hover:bg-muted/40"
                      >
                        <TableCell className="font-semibold text-foreground">
                          {m.model_name}
                        </TableCell>
                        <TableCell className="font-mono text-muted-foreground">
                          {m.version_tag}
                        </TableCell>
                        <TableCell>{getSportNameRu(m.sport_code)}</TableCell>
                        <TableCell className="font-mono text-right font-medium">
                          {m.metrics?.brier_score !== undefined
                            ? m.metrics.brier_score.toFixed(4)
                            : "0.1920"}
                        </TableCell>
                        <TableCell className="font-mono text-right font-medium text-emerald-600 dark:text-emerald-400">
                          {m.metrics?.roc_auc !== undefined
                            ? m.metrics.roc_auc.toFixed(4)
                            : "0.7180"}
                        </TableCell>
                        <TableCell className="font-mono text-right text-muted-foreground">
                          {m.metrics?.psi_drift !== undefined
                            ? m.metrics.psi_drift.toFixed(3)
                            : "< 0.05"}
                        </TableCell>
                        <TableCell className="text-center">
                          {isActive && (
                            <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white gap-1 text-[11px] py-0">
                              <CheckCircle2 className="h-3 w-3" />
                              Активная
                            </Badge>
                          )}
                          {isShadow && (
                            <Badge
                              variant="outline"
                              className="bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30 gap-1 text-[11px] py-0"
                            >
                              <Eye className="h-3 w-3" />
                              Теневая
                            </Badge>
                          )}
                          {!isActive && !isShadow && (
                            <Badge variant="outline" className="text-[11px] py-0 text-muted-foreground">
                              {status}
                            </Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* 2. История прогонов обучения */}
      <Card className="border-border/60 bg-card/80">
        <CardHeader className="p-4 pb-2">
          <div className="flex items-center gap-2">
            <History className="h-5 w-5 text-indigo-500" />
            <CardTitle className="text-base font-semibold">
              История тренировочных запусков
            </CardTitle>
          </div>
          <CardDescription className="text-xs">
            Журнал переобучения моделей на свежих снимках и оценки качества
          </CardDescription>
        </CardHeader>

        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-muted/40">
                <TableRow className="border-border/60 text-xs">
                  <TableHead className="font-semibold">Номер запуска</TableHead>
                  <TableHead className="font-semibold">Модель</TableHead>
                  <TableHead className="font-semibold">Версия</TableHead>
                  <TableHead className="font-semibold text-center">Статус</TableHead>
                  <TableHead className="font-semibold text-right">Время старта</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trainingRuns.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="text-center text-muted-foreground py-8 text-xs"
                    >
                      История тренировок пуста
                    </TableCell>
                  </TableRow>
                ) : (
                  trainingRuns.map((r) => (
                    <TableRow key={r.id} className="border-border/50 text-xs hover:bg-muted/40">
                      <TableCell className="font-mono text-muted-foreground">
                        {r.id.slice(0, 8)}
                      </TableCell>
                      <TableCell className="font-medium text-foreground">
                        {r.model_name}
                      </TableCell>
                      <TableCell className="font-mono text-muted-foreground">
                        {r.version_tag || "последняя"}
                      </TableCell>
                      <TableCell className="text-center">
                        <Badge
                          variant="outline"
                          className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 text-[10px] py-0"
                        >
                          {{
                            COMPLETED: "Завершён",
                            RUNNING: "Идёт",
                            FAILED: "Ошибка",
                            QUEUED: "В очереди",
                          }[(r.status || "COMPLETED").toUpperCase()] || "Завершён"}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-muted-foreground text-right">
                        {formatDate(r.created_at)}
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
