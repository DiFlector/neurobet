"use client"

import type { LucideIcon } from "lucide-react"
import {
  Circle,
  CircleDot,
  Crosshair,
  Diamond,
  Disc,
  Egg,
  Flag,
  Gamepad2,
  Goal,
  Grid3x3,
  LandPlot,
  Layers,
  Orbit,
  Shield,
  Sun,
  Swords,
  Target,
  Volleyball,
} from "lucide-react"
import { sportMeta, type SportIconName } from "@/lib/sports"
import { cn } from "@/lib/utils"

const ICONS: Record<SportIconName, LucideIcon> = {
  Goal,
  CircleDot,
  Gamepad2,
  Disc,
  Orbit,
  Target,
  Volleyball,
  Swords,
  LandPlot,
  Diamond,
  Egg,
  Sun,
  Grid3x3,
  Flag,
  Crosshair,
  Shield,
}

export function SportIcon({
  sport,
  className,
}: {
  sport: string
  className?: string
}) {
  if (!sport || sport === "all") {
    return <Layers className={cn("w-3.5 h-3.5 shrink-0", className)} strokeWidth={1.75} />
  }
  const meta = sportMeta(sport)
  const Icon = meta ? ICONS[meta.icon] : Circle
  return <Icon className={cn("w-3.5 h-3.5 shrink-0", className)} strokeWidth={1.75} />
}

export function SportName({
  sport,
  className,
  iconClassName,
}: {
  sport: string
  className?: string
  iconClassName?: string
}) {
  const label = sport === "all" ? "Все" : sportMeta(sport)?.id || sport
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <SportIcon sport={sport} className={iconClassName} />
      <span>{label}</span>
    </span>
  )
}
