import { createApp } from "vue";
import { addCollection } from "@iconify/vue";
import { icons } from "@iconify-json/lucide";
import App from "./App.vue";
import "./styles.css";

// Keep the complete icon set local so the workbench remains usable on private
// networks without contacting Iconify's public API at runtime.
addCollection(icons);
// Keep the existing workbench DOM and interaction flow unchanged. The opt-in
// identity workspace has its own route, bundle and scoped styles.
const pagePath = location.pathname.replace(/\/+$/, "");
if (pagePath.endsWith("/admin") || location.hash === "#access" || location.hash === "#access-user") {
  void import("./components/AccessWorkspace.vue").then(({ default: AccessWorkspace }) => {
    createApp(AccessWorkspace).mount("#app");
  });
} else {
  createApp(App).mount("#app");
}
