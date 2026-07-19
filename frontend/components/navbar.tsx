"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

import { siteConfig } from "@/config/site";
import { ThemeSwitch } from "@/components/theme-switch";
import { NotificationsPanel } from "@/components/notifications-panel";
import { CornerBadge } from "@/components/mascots";

// HAWK MODE navbar — slim 48px, navy surface with the feather watermark,
// "HAWK MODE" wordmark in Anton, routes in Barlow Condensed uppercase with
// the active route highlighted (green tint + inset green underline). The
// bell + theme toggle sit on the right. No external links (by design).
export const Navbar = () => {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <header
      className="sticky top-0 z-50 w-full overflow-hidden border-b"
      style={{
        height: "var(--nav-h)",
        background: "var(--navy)",
        borderColor: "var(--border)",
      }}
    >
      <div className="hawk-feather" />
      <div className="relative flex h-full w-full items-center gap-3 px-4 md:px-6">
        {/* Wordmark */}
        <Link href="/" className="relative flex items-center gap-2">
          <CornerBadge size={26} />
          <span
            className="font-display text-lg uppercase tracking-[0.02em] text-white"
            style={{ fontSize: "var(--fs-lg)" }}
          >
            Hawk Mode
          </span>
        </Link>

        {/* Routes */}
        <nav className="relative ml-2 hidden items-center gap-0.5 sm:flex">
          {siteConfig.navItems.map((item) => {
            const active = isActive(item.href);

            return (
              <Link
                key={item.href}
                href={item.href}
                className={clsx(
                  "font-head text-sm font-semibold uppercase tracking-[0.04em] no-underline transition-colors",
                  active ? "text-white" : "text-grey hover:text-white",
                )}
                style={{
                  padding: "6px 10px",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "var(--fs-sm)",
                  ...(active
                    ? {
                        background: "rgba(105,190,40,0.16)",
                        boxShadow: "inset 0 -2px 0 var(--green)",
                      }
                    : {}),
                }}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Right controls */}
        <div className="relative ml-auto flex items-center gap-2">
          <NotificationsPanel />
          <ThemeSwitch />
        </div>
      </div>

      {/* Mobile route row — keeps every route one tap away on narrow screens,
          since the inline nav above is hidden on small viewports. */}
      <nav className="relative flex items-center gap-0.5 overflow-x-auto px-4 sm:hidden">
        {siteConfig.navItems.map((item) => {
          const active = isActive(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "font-head whitespace-nowrap text-xs font-semibold uppercase tracking-[0.04em] no-underline",
                active ? "text-white" : "text-grey",
              )}
              style={{
                padding: "4px 8px",
                borderRadius: "var(--radius-sm)",
                ...(active
                  ? {
                      background: "rgba(105,190,40,0.16)",
                      boxShadow: "inset 0 -2px 0 var(--green)",
                    }
                  : {}),
              }}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
};
