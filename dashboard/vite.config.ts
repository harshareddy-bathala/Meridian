// In production the platform serves this build from its own origin (D-081), so
// the dashboard only ever requests relative paths. The dev server proxies those
// same paths to a local platform, which keeps development same-origin too and
// means no CORS configuration exists in either mode.
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // MERIDIAN_PLATFORM_URL from the environment or a .env file beside this one.
  const env = loadEnv(mode, ".", "MERIDIAN_");
  const platform = env.MERIDIAN_PLATFORM_URL ?? "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": platform,
        "/healthz": platform,
      },
    },
  };
});
