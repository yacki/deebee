import { createApp } from "vue";
import { addCollection } from "@iconify/vue";
import { icons } from "@iconify-json/lucide";
import App from "./App.vue";
import "./styles.css";

// Keep the complete icon set local so the workbench remains usable on private
// networks without contacting Iconify's public API at runtime.
addCollection(icons);
createApp(App).mount("#app");
