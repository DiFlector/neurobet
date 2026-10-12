"use client";

import * as React from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <Button variant="outline" size="sm" className="h-9 px-3 gap-2 border-border/60">
        <Sun className="h-4 w-4 text-amber-400" />
        <span className="text-xs font-medium">Светлая тема</span>
      </Button>
    );
  }

  const isDark = theme === "dark";

  return (
    <Button
      variant="outline"
      size="sm"
      className="h-9 px-3 gap-2 border-border/60 hover:bg-muted/80 transition-colors"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      title={isDark ? "Переключить на светлую тему" : "Переключить на тёмную тему"}
    >
      {isDark ? (
        <>
          <Sun className="h-4 w-4 text-amber-400" />
          <span className="text-xs font-medium">Светлая тема</span>
        </>
      ) : (
        <>
          <Moon className="h-4 w-4 text-slate-700" />
          <span className="text-xs font-medium">Тёмная тема</span>
        </>
      )}
    </Button>
  );
}
