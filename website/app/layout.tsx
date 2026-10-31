import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "JobBlast — A smarter job search, straight from Telegram",
  description: "Find better-fit jobs, prepare tailored application materials and stay ahead of every deadline with JobBlast.",
  openGraph: {
    title: "JobBlast — Coming soon",
    description: "A smarter job search, straight from Telegram.",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "JobBlast — Coming soon",
    description: "A smarter job search, straight from Telegram.",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body></html>;
}
