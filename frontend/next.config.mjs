import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

/** @param {string} phase */
export default function nextConfig(phase) {
  const development = phase === PHASE_DEVELOPMENT_SERVER;
  return {
    turbopack: { root: import.meta.dirname },
    ...(development ? { allowedDevOrigins: ["127.0.0.1"] } : {}),
    ...(development ? {
      async rewrites() {
        return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
      },
    } : { output: "export" }),
  };
}
