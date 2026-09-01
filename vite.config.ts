import { sites } from "@openai/sites-vite-plugin";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";
const apiProxy = {
  target: "http://127.0.0.1:8000",
  ws: true,
};

export default defineConfig({
    base: "./",
    server: isCodexSeatbeltSandbox
      ? {
          watch: { useFsEvents: false, usePolling: true },
          proxy: { "/api": apiProxy },
        }
      : { proxy: { "/api": apiProxy } },
    plugins: [vue(), sites()],
});
