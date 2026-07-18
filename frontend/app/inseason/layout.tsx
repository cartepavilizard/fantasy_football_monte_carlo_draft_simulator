// In-season dashboard layout — the composite's three-column top grid needs
// real width, so this widens from the default max-w-4xl centered slab to a
// max-w-7xl page with the kit's spacing. Still scrollable, still centered
// on very-wide viewports.
export default function InSeasonLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <section
      className="flex flex-col gap-4 px-3 py-6 md:px-4 md:py-8"
      style={{ maxWidth: "var(--max-w, 1280px)", margin: "0 auto", width: "100%" }}
    >
      {children}
    </section>
  );
}
