import assert from "node:assert/strict";
import test from "node:test";
import { driverToolbarCapabilities, toolbarCapabilitiesFor } from "../src/toolbarPolicy.ts";

test("each connection type exposes only its supported top-level actions", () => {
  assert.deepEqual(toolbarCapabilitiesFor("mysql"), ["database", "query", "table", "view", "function", "trigger", "event", "privileges", "backup"]);
  assert.deepEqual(toolbarCapabilitiesFor("postgresql"), ["database", "query", "table", "view", "function", "trigger", "privileges", "backup"]);
  assert.deepEqual(toolbarCapabilitiesFor("mssql"), ["database", "query", "view", "function", "trigger", "privileges", "backup"]);
  assert.deepEqual(toolbarCapabilitiesFor("redis"), ["test", "settings"]);
  assert.deepEqual(toolbarCapabilitiesFor("clickhouse"), ["test", "settings"]);
  assert.deepEqual(toolbarCapabilitiesFor("mongodb"), ["test", "settings"]);
  assert.deepEqual(toolbarCapabilitiesFor("ssh"), ["session", "test", "settings"]);
  assert.deepEqual(toolbarCapabilitiesFor("rdp"), ["session", "test", "settings"]);
});

test("non-relational and remote menus never inherit relational object actions", () => {
  const relationalOnly = new Set(["database", "query", "table", "view", "function", "trigger", "event", "privileges", "backup"]);
  for (const driver of ["redis", "clickhouse", "mongodb", "ssh", "rdp"]) {
    assert.equal(toolbarCapabilitiesFor(driver).some(action => relationalOnly.has(action)), false, driver);
  }
  assert.deepEqual(Object.keys(driverToolbarCapabilities).sort(), ["clickhouse", "mongodb", "mssql", "mysql", "postgresql", "rdp", "redis", "ssh"]);
});
