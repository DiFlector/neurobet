"use client"

import { useState, useRef, useEffect } from "react"
import { createPortal } from "react-dom"
import { OddsHistoryGraph } from "./OddsHistoryGraph"

interface OddsButtonProps {
  eventId: number
  factorId: number
  parameter?: string
  marketPrefix?: string
  coefficient: number
  initialCoefficient?: number
  label: string
  shortLabel?: string
  variant?: "compact" | "full"
  safeMode?: boolean
}

export function OddsButton({
  eventId,
  factorId,
  parameter = "",
  marketPrefix = "",
  coefficient,
  initialCoefficient,
  label,
  shortLabel,
  variant = "compact",
  safeMode = false
}: OddsButtonProps) {
  const [isHovered, setIsHovered] = useState(false)
  const [mounted, setMounted] = useState(false)
  const [popoverPos, setPopoverPos] = useState<{ top: number; left: number; placeBelow: boolean }>({
    top: 0,
    left: 0,
    placeBelow: false
  })
  const buttonRef = useRef<HTMLButtonElement | null>(null)
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    setMounted(true)
  }, [])

  const updatePosition = () => {
    if (!buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    const graphWidth = 320 // w-80 = 320px
    const graphHeight = 250 // estimated height of OddsHistoryGraph

    const spaceAbove = rect.top
    const placeBelow = spaceAbove < graphHeight + 16

    let top = placeBelow ? rect.bottom + 8 : rect.top - graphHeight - 8
    let left = rect.left + rect.width / 2 - graphWidth / 2

    const viewportWidth = window.innerWidth
    if (left < 10) left = 10
    if (left + graphWidth > viewportWidth - 10) left = viewportWidth - graphWidth - 10

    setPopoverPos({ top, left, placeBelow })
  }

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    updatePosition()
    setIsHovered(true)
  }

  const handleMouseLeave = () => {
    timeoutRef.current = setTimeout(() => {
      setIsHovered(false)
    }, 150)
  }

  // Safe mode check: dangerous odds are < 1.1 or > 2.1
  const isDangerous = coefficient < 1.1 || coefficient > 2.1
  if (safeMode && isDangerous) {
    return null
  }

  const displayLabel = shortLabel || label

  // Trend calculation: Compare current coefficient vs initial coefficient in history
  const diff = initialCoefficient !== undefined ? coefficient - initialCoefficient : 0
  const isUp = diff > 0.001
  const isDown = diff < -0.001

  // Color selection based on user directive:
  // Rising (UP) -> Red (#d63031 / #ff7675)
  // Falling (DOWN) -> Green (#00b894 / #55efc4)
  // Unchanged (+-) -> Gray (standard neutral gray + #fdcb6e)
  let buttonStyle = "border-neutral-800 bg-neutral-900/80 text-neutral-200 hover:border-neutral-700 hover:bg-neutral-800"
  let coeffTextStyle = "text-[#fdcb6e]"

  if (isUp) {
    buttonStyle = "border-[#d63031]/50 bg-[#d63031]/15 text-[#ff7675] hover:border-[#d63031] hover:bg-[#d63031]/25"
    coeffTextStyle = "text-[#ff7675]"
  } else if (isDown) {
    buttonStyle = "border-[#00b894]/50 bg-[#00b894]/15 text-[#55efc4] hover:border-[#00b894] hover:bg-[#00b894]/25"
    coeffTextStyle = "text-[#55efc4]"
  }

  if (isHovered) {
    buttonStyle += " scale-[1.02] shadow-lg"
  }

  return (
    <div
      className="relative inline-block w-full"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button
        ref={buttonRef}
        type="button"
        className={`group flex items-center justify-between gap-1.5 rounded-lg border transition-all duration-200 ${buttonStyle} ${
          variant === "compact"
            ? "px-2.5 py-1.5 text-xs min-w-[70px]"
            : "px-3 py-2 text-sm w-full"
        }`}
      >
        <span className="text-[11px] font-medium text-neutral-400 truncate group-hover:text-neutral-300">
          {displayLabel}
        </span>
        <span className={`font-mono font-bold ${coeffTextStyle}`}>
          {coefficient.toFixed(2)}
        </span>
      </button>

      {isHovered && mounted && createPortal(
        <div
          style={{
            position: "fixed",
            top: `${popoverPos.top}px`,
            left: `${popoverPos.left}px`,
            zIndex: 99999,
          }}
          className="pointer-events-auto transition-all animate-in fade-in zoom-in-95 duration-150 shadow-2xl"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <OddsHistoryGraph
            eventId={eventId}
            factorId={factorId}
            parameter={parameter}
            marketPrefix={marketPrefix}
            currentCoeff={coefficient}
            label={label}
          />
        </div>,
        document.body
      )}
    </div>
  )
}
