import { sites } from "@openai/sites-vite-plugin";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

export default defineConfig({
    server: isCodexSeatbeltSandbox
      ? {
          watch: { useFsEvents: false, usePolling: true },
          proxy: { "/api": "http://127.0.0.1:8000" },
        }
      : { proxy: { "/api": "http://127.0.0.1:8000" } },
    plugins: [vue(), sites()],
});
