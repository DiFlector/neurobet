"use client";

import * as React from "react";
import { RotateCcw, AlertTriangle } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

interface ResetSimulationDialogProps {
  onReset: () => Promise<void>;
  isResetting?: boolean;
}

export function ResetSimulationDialog({
  onReset,
  isResetting,
}: ResetSimulationDialogProps) {
  const [open, setOpen] = React.useState(false);

  const handleConfirm = async () => {
    await onReset();
    setOpen(false);
  };

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-9 px-3 gap-2 border-red-500/30 text-red-600 dark:text-red-400 hover:bg-red-500/10 hover:border-red-500/50 transition-colors"
        disabled={isResetting}
        onClick={() => setOpen(true)}
      >
        <RotateCcw className={`h-4 w-4 ${isResetting ? "animate-spin" : ""}`} />
        <span className="text-xs font-medium">Сброс симуляции</span>
      </Button>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent className="sm:max-w-md">
        <AlertDialogHeader>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-full bg-red-500/10 text-red-500">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <AlertDialogTitle>Сброс виртуальной симуляции</AlertDialogTitle>
          </div>
          <AlertDialogDescription className="text-sm pt-2 text-muted-foreground leading-relaxed">
            Вы уверены, что хотите сбросить виртуальный баланс к{" "}
            <span className="font-semibold text-foreground">100 000 ₽</span>?
            Все смоделированные ставки и записи в финансовом журнале будут
            безвозвратно очищены.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="mt-4">
          <AlertDialogCancel className="text-xs">Отмена</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            className="bg-red-600 hover:bg-red-700 text-white text-xs gap-1.5"
            disabled={isResetting}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Подтвердить сброс
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </>
);
}
