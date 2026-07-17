import "@/styles/globals.css";
import { Metadata, Viewport } from "next";
import clsx from "clsx";

import { Providers } from "./providers";

import { siteConfig } from "@/config/site";
import { fontSans } from "@/config/fonts";
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
    { media: "(prefers-color-scheme: light)", color: "white" },
    { media: "(prefers-color-scheme: dark)", color: "black" },
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
          "min-h-screen font-sans antialiased",
          fontSans.variable,
        )}
      >
        <Providers themeProps={{ attribute: "class", defaultTheme: "dark" }}>
          <div
            className="
                fixed w-screen h-screen 
                bg-cover bg-no-repeat bg-right bg-fixed
                opacity-100 z-1 hide-on-light
              "
            style={{
              backgroundImage: "url('/football_dark.png')",
            }}
          />
          <div
            className="
                fixed w-screen h-screen 
                bg-cover bg-no-repeat bg-right bg-fixed
                opacity-50 z-1 hide-on-dark
              "
            style={{
              backgroundImage: "url('/football_light.png')",
            }}
          />
          <div className="relative flex flex-col min-h-screen">
            {/* Background image */}

            {/* Content */}
            <Navbar />
            <main className="container relative mx-auto max-w-7xl py-16 px-6 flex-grow z-2">
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
