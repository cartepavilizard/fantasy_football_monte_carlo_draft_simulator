export type SiteConfig = typeof siteConfig;

export const siteConfig = {
  name: "FF Monte Carlo Draft Simulator",
  description: "Make statistically sound picks for every position.",
  navItems: [
    {
      label: "Home",
      href: "/",
    },
    {
      label: "Sources",
      href: "/sources",
    },
    {
      label: "Setup",
      href: "/setup",
    },
    {
      label: "Draft",
      href: "/draft",
    },
  ],
  navMenuItems: [
    {
      label: "Sources",
      href: "/sources",
    },
    {
      label: "Setup",
      href: "/setup",
    },
    {
      label: "Draft",
      href: "/draft",
    },
  ],
  links: {
    github:
      "https://github.com/joewlos/fantasy_football_monte_carlo_draft_simulator",
  },
};
