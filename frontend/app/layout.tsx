import type { Metadata } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";
import { QueryProvider } from "@/lib/query-provider";
import "katex/dist/katex.min.css";
import "./globals.css";

const inter = localFont({
  src: [
    { path: "./fonts/InterVariable.woff2", style: "normal", weight: "100 900" },
    { path: "./fonts/InterVariable-Italic.woff2", style: "italic", weight: "100 900" },
  ],
  display: "swap",
  variable: "--inter-font",
});

export const metadata: Metadata = { title: "PDF2DOCX — document workspace" };
const themeScript = `(()=>{let t='light';try{const s=localStorage.getItem('pdf2docx-theme');t=s==='light'||s==='dark'?s:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}catch{}document.documentElement.dataset.theme=t})()`;

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head>
      <body className={inter.variable}><QueryProvider>{children}</QueryProvider></body>
    </html>
  );
}
