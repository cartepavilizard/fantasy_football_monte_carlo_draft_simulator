import "@/styles/globals.css";
import { Metadata, Viewport } from "next";
import clsx from "clsx";

import { Providers } from "./providers";

import { siteConfig } from "@/config/site";
import { fontDisplay, fontHead, fontBody } from "@/config/fonts";
import { Navbar } from "@/components/navbar";

export const metadata: Metadata = {
  title: {
    default: siteConfig.name,
    template: `%s - ${siteConfig.name}`,
  },
  description: siteConfig.description,
  icons: {
    icon: "/favicon.ico",
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#e7ecf1" },
    { media: "(prefers-color-scheme: dark)", color: "#050f1a" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html suppressHydrationWarning lang="en">
      <head />
      <body
        className={clsx(
          "min-h-screen font-body antialiased",
          fontDisplay.variable,
          fontHead.variable,
          fontBody.variable,
        )}
      >
        <Providers themeProps={{ attribute: "class", defaultTheme: "dark" }}>
          {/* HAWK MODE background: navy gradient + faint feather watermark.
              Light theme falls back to the kit's light surface token. */}
          <div
            aria-hidden
            className="fixed inset-0 -z-10 hide-on-light"
            style={{
              background:
                "linear-gradient(120deg, var(--navy) 0%, var(--bg) 55%, var(--surface) 100%)",
            }}
          >
            <div className="hawk-feather" />
          </div>
          <div
            aria-hidden
            className="fixed inset-0 -z-10 hide-on-dark"
            style={{ backgroundColor: "var(--bg)" }}
          />
          <div className="relative flex flex-col min-h-screen">
            <Navbar />
            {/* Full-bleed: every page uses the whole display width */}
            <main className="relative w-full py-6 px-4 flex-grow z-2 md:py-8 md:px-6">
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
