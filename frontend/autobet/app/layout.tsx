import "./globals.css"
import { ThemeProvider } from "@/components/theme-provider"
import { cn } from "@/lib/utils";
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Нейроставки",
}

// Fonts are plain system stacks defined in globals.css (--font-sans / --font-mono) —
// see the comment there for why next/font/google was dropped (it fetches from Google
// Fonts at `next build` time, which breaks the build with no outbound internet access).

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="ru" suppressHydrationWarning className={cn("antialiased", "font-sans")}>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  )
}
