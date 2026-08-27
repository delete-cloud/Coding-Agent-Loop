"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui Button, themed to the Night Console tokens (01/04 §3):
 * - radii resolve to the 3px token via @theme aliases;
 * - transitions are restricted to color/background/border-color, 120ms
 *   linear (01 §5) — no motion, no ring shadows (focus uses borders);
 * - all colors come from semantic theme aliases mapped in globals.css.
 *
 * Native control elements may appear only in button.tsx, input.tsx, and
 * textarea.tsx; every other directory must compose these components (04 §3).
 */
const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-md text-[13px] transition-[color,background-color,border-color] duration-[120ms] ease-linear outline-none focus-visible:border-ring disabled:pointer-events-none disabled:text-muted-foreground [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-accent",
        ghost: "bg-transparent",
      },
      size: {
        default: "px-[14px] py-[5px]",
        icon: "size-7",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  type = "button",
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      data-slot="button"
      type={asChild ? undefined : type}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
