export default function InSeasonLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col items-center justify-center gap-4 py-8 md:py-10">
      <div className="inline-block max-w-4xl w-full justify-center">
        {children}
      </div>
    </section>
  );
}
