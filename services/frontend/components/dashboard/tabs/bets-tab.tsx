"use client";

import * as React from "react";
import { FileText, CheckCircle2, XCircle, Clock, AlertTriangle } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { BetItem } from "@/lib/types";
import { formatCurrency, formatTime, getSportNameRu, getStatusTextRu } from "@/lib/formatters";
import { getLocalizedParticipantName } from "@/lib/locale-names";

interface BetsTabProps {
  bets: BetItem[];
  loading?: boolean;
}

export function BetsTab({ bets, loading = false }: BetsTabProps) {
  if (!loading && bets.length === 0) {
    return (
      <Card className="border-dashed border-border/60 bg-card/30">
        <CardContent className="flex flex-col items-center justify-center p-12 text-center">
          <div className="p-3 rounded-full bg-muted/60 text-muted-foreground mb-3">
            <FileText className="h-8 w-8" />
          </div>
          <h3 className="text-base font-semibold text-foreground">
            Журнал ставок пуст
          </h3>
          <p className="text-xs text-muted-foreground mt-1 max-w-md">
            Виртуальные ставки формируются автоматически, когда у прогноза есть положительный перевес.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-border/60 bg-card/80">
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader className="bg-muted/40">
              <TableRow className="border-border/60 text-xs">
                <TableHead className="font-semibold">Номер ставки</TableHead>
                <TableHead className="font-semibold">Спорт и рынок</TableHead>
                <TableHead className="font-semibold">Выбор (Исход)</TableHead>
                <TableHead className="font-semibold text-right">Коэффициент</TableHead>
                <TableHead className="font-semibold text-right">Сумма ставки</TableHead>
                <TableHead className="font-semibold text-center">Статус</TableHead>
                <TableHead className="font-semibold text-right">Время размещения</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {bets.map((b) => {
                const isWon = b.status === "WON";
                const isLost = b.status === "LOST";
                const isRejected = b.status === "REJECTED";
                const isPending = b.status === "PENDING" || b.status === "ACCEPTED";

                return (
                  <TableRow key={b.id} className="border-border/50 text-xs hover:bg-muted/40">
                    <TableCell className="font-mono text-muted-foreground">
                      {b.id.slice(0, 8)}
                    </TableCell>
                    <TableCell>
                      <div className="font-medium text-foreground">
                        {getSportNameRu(b.sport_code)}
                      </div>
                      <div className="text-[11px] text-muted-foreground">{b.market}</div>
                    </TableCell>
                    <TableCell className="font-semibold text-foreground">
                      {getLocalizedParticipantName(b.outcome, b.sport_code)}
                    </TableCell>
                    <TableCell className="font-mono font-medium text-right">
                      {Number(b.odds).toFixed(2)}
                    </TableCell>
                    <TableCell className="font-mono font-medium text-right">
                      {formatCurrency(b.stake)}
                    </TableCell>
                    <TableCell className="text-center">
                      {isWon && (
                        <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white gap-1 text-[11px] py-0">
                          <CheckCircle2 className="h-3 w-3" />
                          Выигрыш
                        </Badge>
                      )}
                      {isLost && (
                        <Badge
                          variant="destructive"
                          className="bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30 gap-1 text-[11px] py-0"
                        >
                          <XCircle className="h-3 w-3" />
                          Проигрыш
                        </Badge>
                      )}
                      {isPending && (
                        <Badge
                          variant="outline"
                          className="bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30 gap-1 text-[11px] py-0"
                        >
                          <Clock className="h-3 w-3" />
                          В игре
                        </Badge>
                      )}
                      {isRejected && (
                        <Badge
                          variant="outline"
                          className="bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30 gap-1 text-[11px] py-0"
                          title={b.rejection_reason || "Отклонено риск-менеджером"}
                        >
                          <AlertTriangle className="h-3 w-3" />
                          Отклонена
                        </Badge>
                      )}
                      {!isWon && !isLost && !isPending && !isRejected && (
                        <Badge variant="outline" className="text-[11px] py-0">
                          {getStatusTextRu(b.status)}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-muted-foreground text-right">
                      {formatTime(b.placed_at)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
