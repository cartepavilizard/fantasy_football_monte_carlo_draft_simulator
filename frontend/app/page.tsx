import { Link } from "@nextui-org/link";
import { Snippet } from "@nextui-org/snippet";
import { Code } from "@nextui-org/code";
import clsx from "clsx";

import { fontMono } from "@/config/fonts";
import { title, subtitle } from "@/components/primitives";
import { HeroMascot } from "@/components/mascots";

export default function Home() {
  return (
    <section className="flex flex-col items-center justify-center gap-8 py-4 md:py-6">
      {/* HAWK MODE hero — navy panel, feather watermark, hero mascot,
          Anton wordmark with the green period. */}
      <div
        className="relative w-full overflow-hidden"
        style={{
          background: "linear-gradient(115deg, var(--navy), #001220 70%)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          padding: "var(--sp-6)",
        }}
      >
        <div className="hawk-feather" />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-5 -top-10"
          style={{
            width: 340,
            height: 340,
            background:
              "radial-gradient(circle, rgba(105,190,40,0.16), transparent 62%)",
          }}
        />
        <div className="relative flex flex-wrap items-center gap-5">
          <div className="shrink-0">
            <HeroMascot size={180} />
          </div>
          <div className="flex-1" style={{ minWidth: 260 }}>
            <div
              className="flex items-center gap-2 font-head font-bold uppercase text-green"
              style={{ letterSpacing: "0.18em", fontSize: "var(--fs-xs)" }}
            >
              <span
                style={{
                  width: 26,
                  height: 2,
                  background: "var(--green)",
                  display: "inline-block",
                }}
              />
              Fantasy Football Draft Simulator
            </div>
            <h1 className="font-display text-display uppercase leading-[0.92]">
              <span className="bg-clip-text text-transparent bg-gradient-to-b from-white to-grey">
                Hawk Mode
              </span>
              <span className="text-green">.</span>
            </h1>
            <p
              className="mt-2 max-w-xl text-[color:var(--text-dim)]"
              style={{ fontSize: "var(--fs-md)" }}
            >
              Make statistically sound picks for every position. Harness the
              power of (kind of sort of) artificial intelligence to draft
              players for your fantasy football team.
            </p>
          </div>
        </div>
      </div>

      <div>
        <Snippet hideCopyButton hideSymbol variant="bordered">
          <span className={clsx("font-mono", fontMono.variable)}>
            Get started by{" "}
            <Link href="/setup">
              <Code color="primary">setting up</Code>
            </Link>{" "}
            a draft
          </span>
        </Snippet>
      </div>
    </section>
  );
}
