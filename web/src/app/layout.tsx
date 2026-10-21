import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Shell } from "@/components/shell";
import { ToastProvider } from "@/components/toast";
import { LiveProvider } from "@/lib/live";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans-family", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono-family", display: "swap" });

export const metadata: Metadata = {
  title: "Tripwire",
  description: "Keeps your calendar in step with your bookings, and asks before it changes anything.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0d1017" },
  ],
};

// Applied before first paint, so a saved theme never flashes the wrong one.
const themeScript = `try{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <LiveProvider>
          <ToastProvider>
            <Shell>{children}</Shell>
          </ToastProvider>
        </LiveProvider>
      </body>
    </html>
  );
}
