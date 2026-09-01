import type { ConnectionDriver } from "./types";

export type ToolbarCapability =
  | "database"
  | "query"
  | "table"
  | "view"
  | "function"
  | "trigger"
  | "event"
  | "privileges"
  | "backup"
  | "session"
  | "test"
  | "settings";

export const driverToolbarCapabilities = {
  mysql: ["database", "query", "table", "view", "function", "trigger", "event", "privileges", "backup"],
  postgresql: ["database", "query", "table", "view", "function", "trigger", "privileges", "backup"],
  redis: ["test", "settings"],
  clickhouse: ["test", "settings"],
  mongodb: ["test", "settings"],
  ssh: ["session", "test", "settings"],
  rdp: ["session", "test", "settings"],
} as const satisfies Record<ConnectionDriver, readonly ToolbarCapability[]>;

export function toolbarCapabilitiesFor(driver: ConnectionDriver): readonly ToolbarCapability[] {
  return driverToolbarCapabilities[driver];
}
