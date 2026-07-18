import { tv } from "tailwind-variants";

// HAWK MODE: page-title display headings use Anton (--font-display), section
// headings/subtitles use Barlow Condensed (--font-head). The green gradient
// matches the kit's hero wordmark (#69BE28 → bright).
export const title = tv({
  base: "font-display tracking-tight inline uppercase",
  variants: {
    color: {
      violet: "from-[#FF1CF7] to-[#b249f8]",
      yellow: "from-[#FF705B] to-[#FFB457]",
      blue: "from-[#5EA2EF] to-[#0072F5]",
      cyan: "from-[#00b7fa] to-[#01cfea]",
      green: "from-[#7fe030] to-[#69BE28]",
      pink: "from-[#FF72E1] to-[#F54C7A]",
      foreground: "from-white to-[#A5ACAF]",
    },
    size: {
      sm: "text-3xl lg:text-4xl",
      md: "text-[2.3rem] lg:text-5xl leading-9",
      lg: "text-4xl lg:text-6xl",
    },
    fullWidth: {
      true: "w-full block",
    },
  },
  defaultVariants: {
    size: "md",
  },
  compoundVariants: [
    {
      color: [
        "violet",
        "yellow",
        "blue",
        "cyan",
        "green",
        "pink",
        "foreground",
      ],
      class: "bg-clip-text text-transparent bg-gradient-to-b",
    },
  ],
});

export const subtitle = tv({
  base: "font-head w-full md:w-1/2 my-2 text-base lg:text-lg block max-w-full tracking-wide",
  variants: {
    fullWidth: {
      true: "!w-full",
    },
  },
  defaultVariants: {
    fullWidth: true,
  },
});
