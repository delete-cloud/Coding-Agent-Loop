"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Native select primitive. Business views must compose this instead of
 * writing a raw <select> (same rule as button/input/textarea).
 */
function Select({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="select"
      className={cn(
        "flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-[13px] outline-none transition-[color,background-color,border-color] duration-[120ms] ease-linear focus-visible:border-ring disabled:pointer-events-none disabled:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export { Select };
