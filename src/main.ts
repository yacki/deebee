import { createApp } from "vue";
import { installUi } from "./ui";
import { addCollection } from "@iconify/vue";
import { icons } from "@iconify-json/lucide";

// Keep the complete icon set local so the workbench remains usable on private
// networks without contacting Iconify's public API at runtime.
addCollection(icons);
// Both workspaces share the same component library, locale and theme.
const pagePath = location.pathname.replace(/\/+$/, "");
if (pagePath.endsWith("/admin") || location.hash === "#access" || location.hash === "#access-user") {
  void import("./components/AccessWorkspace.vue").then(({default: AccessWorkspace}) => installUi(createApp(AccessWorkspace)).mount("#app"));
} else {
  void Promise.all([import("./App.vue"), import("./styles.css")]).then(([{default: App}]) => installUi(createApp(App)).mount("#app"));
}
