import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" so the build works both on GitHub Pages (/CoinPilot/) and when
// the dist is mirrored to the ledger branch and viewed through raw.githack.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "../dist", emptyOutDir: true },
});
