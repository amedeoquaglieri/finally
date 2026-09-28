import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

// Production builds are a static export served by FastAPI from the same origin.
// In `next dev`, proxy /api/* to a backend running on :8000 (rewrites are not
// allowed together with `output: "export"`).
const nextConfig: NextConfig = isDev
  ? {
      async rewrites() {
        return [{ source: "/api/:path*", destination: "http://localhost:8000/api/:path*" }];
      },
    }
  : {
      output: "export",
      images: { unoptimized: true },
    };

export default nextConfig;
