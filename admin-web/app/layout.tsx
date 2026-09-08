import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LostLink AI 校園管理中心",
  description: "多模態 AI 失物招領管理中心",
  manifest: "/manifest.webmanifest",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
