"use client";

import { Suspense } from "react";

import { AppFrame } from "@/components/business/app-frame";

/**
 * Single route "/" (04 §1). AppFrame consumes useSearchParams(), which under
 * static export MUST sit inside a <Suspense> boundary — an implementation
 * constraint, not an option (04 §1).
 */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <AppFrame />
    </Suspense>
  );
}
