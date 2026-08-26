import type { NextConfig } from 'next';

// 04 §1: Next.js App Router used as a pure SPA — single static export.
// No server actions, no API routes, no dynamic segments.
const nextConfig: NextConfig = {
  output: 'export',
};

export default nextConfig;
