import { createApp } from "vue";
import { addCollection } from "@iconify/vue";
import { icons } from "@iconify-json/lucide";

// Keep the complete icon set local so the workbench remains usable on private
// networks without contacting Iconify's public API at runtime.
addCollection(icons);
// Keep the existing workbench DOM and interaction flow unchanged. The opt-in
// identity workspace has its own route, bundle and scoped styles.
const pagePath = location.pathname.replace(/\/+$/, "");
if (pagePath.endsWith("/admin") || location.hash === "#access" || location.hash === "#access-user") {
  void Promise.all([import("./components/AccessWorkspace.vue"), import("@arco-design/web-vue"), import("@arco-design/web-vue/dist/arco.css")]).then(([{ default: AccessWorkspace }, { default: ArcoVue }]) => {
    createApp(AccessWorkspace).use(ArcoVue).mount("#app");
  });
} else {
  void Promise.all([import("./App.vue"), import("./styles.css")]).then(([{default: App}]) => createApp(App).mount("#app"));
}
