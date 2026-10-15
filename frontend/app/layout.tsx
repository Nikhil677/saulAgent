import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "DealPilot | Negotiation Operations",
  description: "AI-powered voice negotiation workspace",
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
