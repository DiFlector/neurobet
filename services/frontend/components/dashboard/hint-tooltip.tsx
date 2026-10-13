"use client";

import * as React from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "cn";

interface HintTooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  side?: "top" | "bottom" | "left" | "right";
  delay?: number;
}

/** Hover/focus hint that works reliably (native title= often fails on dense cards). */
export function HintTooltip({
  content,
  children,
  className,
  side = "top",
  delay = 200,
}: HintTooltipProps) {
  return (
    <Tooltip>
      <TooltipTrigger
        delay={delay}
        render={<span />}
        className={cn("inline-flex cursor-help items-center", className)}
      >
        {children}
      </TooltipTrigger>
      <TooltipContent
        side={side}
        className="max-w-[16rem] whitespace-normal text-left leading-relaxed"
      >
        {content}
      </TooltipContent>
    </Tooltip>
  );
}

export function qualityScoreHint(score: number): string {
  const pct = Number.isFinite(score) ? score.toFixed(0) : "100";
  return (
    `К — индекс качества данных (${pct}%). ` +
    `Показывает, насколько полный и согласованный снимок матча: поля, порядок времени, котировки Fonbet. ` +
    `100% — критических аномалий не найдено; чем ниже, тем больше ошибок или пропусков в данных.`
  );
}
