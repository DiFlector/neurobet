"use client";

import * as React from "react";
import { Landmark, ArrowUpRight, ArrowDownRight, RefreshCw } from "lucide-react";
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
import { LedgerEntry } from "@/lib/types";
import { formatCurrency, formatDate } from "@/lib/formatters";

interface LedgerTabProps {
  ledger: LedgerEntry[];
  loading?: boolean;
}

export function LedgerTab({ ledger, loading = false }: LedgerTabProps) {
  if (!loading && ledger.length === 0) {
    return (
      <Card className="border-dashed border-border/60 bg-card/30">
        <CardContent className="flex flex-col items-center justify-center p-12 text-center">
          <div className="p-3 rounded-full bg-muted/60 text-muted-foreground mb-3">
            <Landmark className="h-8 w-8" />
          </div>
          <h3 className="text-base font-semibold text-foreground">
            Журнал аудита пуст
          </h3>
          <p className="text-xs text-muted-foreground mt-1 max-w-md">
            Финансовый журнал фиксирует все операции с виртуальным балансом и проверяет целостность счёта.
          </p>
        </CardContent>
      </Card>
    );
  }

  function getEntryTypeLabel(type: string): { label: string; variant: "default" | "secondary" | "outline" | "destructive" } {
    switch (type.toUpperCase()) {
      case "INITIAL_DEPOSIT":
      case "DEPOSIT":
        return { label: "Депозит", variant: "default" };
      case "BET_PLACEMENT":
      case "BET":
        return { label: "Списание ставки", variant: "destructive" };
      case "BET_PAYOUT":
      case "WIN":
        return { label: "Выплата выигрыша", variant: "secondary" };
      case "RESET":
      case "SIMULATION_RESET":
        return { label: "Сброс симуляции", variant: "outline" };
      default:
        return { label: type, variant: "outline" };
    }
  }

  return (
    <Card className="border-border/60 bg-card/80">
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader className="bg-muted/40">
              <TableRow className="border-border/60 text-xs">
                <TableHead className="font-semibold">Номер операции</TableHead>
                <TableHead className="font-semibold">Тип операции</TableHead>
                <TableHead className="font-semibold text-right">Сумма</TableHead>
                <TableHead className="font-semibold text-right">Баланс после</TableHead>
                <TableHead className="font-semibold text-right">Время фиксации</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ledger.map((entry) => {
                const amount = Number(entry.amount || 0);
                const isPositive = amount >= 0;
                const typeInfo = getEntryTypeLabel(entry.entry_type);

                return (
                  <TableRow
                    key={entry.id}
                    className="border-border/50 text-xs hover:bg-muted/40"
                  >
                    <TableCell className="font-mono text-muted-foreground">
                      {entry.id.slice(0, 8)}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={typeInfo.variant}
                        className="text-[11px] font-medium py-0 px-2"
                      >
                        {typeInfo.label}
                      </Badge>
                    </TableCell>
                    <TableCell
                      className={`font-mono font-medium text-right ${
                        isPositive
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-red-600 dark:text-red-400"
                      }`}
                    >
                      <span className="inline-flex items-center gap-0.5 justify-end">
                        {isPositive ? (
                          <ArrowUpRight className="h-3 w-3 inline text-emerald-500" />
                        ) : (
                          <ArrowDownRight className="h-3 w-3 inline text-red-500" />
                        )}
                        {isPositive ? `+${formatCurrency(amount)}` : formatCurrency(amount)}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono font-semibold text-foreground text-right">
                      {formatCurrency(entry.balance_after)}
                    </TableCell>
                    <TableCell className="font-mono text-muted-foreground text-right">
                      {formatDate(entry.created_at)}
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
