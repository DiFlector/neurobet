"use client";

import { useLayoutEffect, useRef } from "react";
import { cn } from "cn";

interface FitTextProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
  maxPx?: number;
  minPx?: number;
}

/**
 * Одна строка. Пока текст помещается, остаётся maxPx.
 * Если ширина кончилась — уменьшает кегль до minPx, без переноса.
 */
export function FitText({ children, className, title, maxPx = 14, minPx = 9 }: FitTextProps) {
  const ref = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const fit = () => {
      if (el.clientWidth <= 0) return;
      let low = minPx;
      let high = maxPx;
      let best = minPx;
      while (high - low > 0.2) {
        const mid = (low + high) / 2;
        el.style.fontSize = `${mid}px`;
        if (el.scrollWidth <= el.clientWidth + 0.5) {
          best = mid;
          low = mid;
        } else {
          high = mid;
        }
      }
      el.style.fontSize = `${best}px`;
    };

    fit();
    const parent = el.parentElement;
    const observer = new ResizeObserver(fit);
    if (parent) observer.observe(parent);
    return () => observer.disconnect();
  }, [children, maxPx, minPx]);

  return (
    <span
      ref={ref}
      title={title}
      className={cn("block min-w-0 max-w-full overflow-hidden whitespace-nowrap leading-none", className)}
      style={{ fontSize: maxPx }}
    >
      {children}
    </span>
  );
}
