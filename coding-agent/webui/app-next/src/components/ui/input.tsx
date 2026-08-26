"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui Input, themed to the Night Console tokens (01/04 §3):
 * focus is signaled by the border turning amber (--ring), never by a
 * shadow ring (01 §5); transitions are color-family only, 120ms linear.
 *
 * Native <input> may appear only here (with button.tsx / textarea.tsx);
 * business views must compose this primitive (04 §3).
 */
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-[13px] outline-none transition-[color,background-color,border-color] duration-[120ms] ease-linear placeholder:text-muted-foreground focus-visible:border-ring disabled:pointer-events-none disabled:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
