import { readFileSync } from "node:fs";
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from "@firebase/rules-unit-testing";
import { afterAll, beforeAll, beforeEach, describe, it } from "vitest";

let env: RulesTestEnvironment;

const reading = (n: unknown = 3) => ({
  device_id: "MSU1",
  acquired_at: "2026-07-15T12:00:03-05:00",
  count_male_swd: n,
  serverTime: { ".sv": "timestamp" },
});

beforeAll(async () => {
  env = await initializeTestEnvironment({
    projectId: "demo-swd-web",
    database: { rules: readFileSync("database.rules.json", "utf8") },
  });
});

afterAll(async () => {
  await env.cleanup();
});

beforeEach(async () => {
  await env.clearDatabase();
  await env.withSecurityRulesDisabled(async (ctx) => {
    await ctx.database().ref().set({
      devices: { dev1: "MSU1" },
      users: { u1: { role: "grower" }, admin1: { role: "admin" } },
      readings: { MSU1: { "2026-07-15": { "120003": reading() } } },
    });
  });
});

const db = (uid?: string) =>
  (uid ? env.authenticatedContext(uid) : env.unauthenticatedContext()).database();

describe("devices", () => {
  it("device writes own reading", async () => {
    await assertSucceeds(db("dev1").ref("readings/MSU1/2026-07-15/123003").set(reading()));
  });

  it("device cannot write other device reading", async () => {
    await assertFails(db("dev1").ref("readings/HSH1/2026-07-15/123003").set(reading()));
  });

  it("device heartbeat with server timestamp", async () => {
    await assertSucceeds(
      db("dev1").ref("heartbeat/MSU1").update({
        deviceTime: "2026-07-15T13:00:00-05:00",
        serverTime: { ".sv": "timestamp" },
      }),
    );
  });

  it("device writes own thumbs node", async () => {
    await assertSucceeds(
      db("dev1").ref("thumbs/MSU1/2026-07-15").set({ storagePath: "thumbs/MSU1/2026-07-15.jpg" }),
    );
  });

  it("device cannot write devices mapping or other paths", async () => {
    await assertFails(db("dev1").ref("devices/dev1").set("HSH1"));
    await assertFails(db("dev1").ref("daily/MSU1/2026-07-15").set({ max: 1 }));
  });

  it("rejects negative or fractional count", async () => {
    await assertFails(db("dev1").ref("readings/MSU1/2026-07-15/123003").set(reading(-1)));
    await assertFails(db("dev1").ref("readings/MSU1/2026-07-15/123003").set(reading(1.5)));
    await assertFails(db("dev1").ref("readings/MSU1/2026-07-15/123003").set(reading("3")));
  });

  it("rejects reading missing required fields", async () => {
    await assertFails(
      db("dev1").ref("readings/MSU1/2026-07-15/123003").set({ count_male_swd: 3 }),
    );
  });

  it("rtc fault date path accepted", async () => {
    await assertSucceeds(
      db("dev1")
        .ref("readings/MSU1/2000-01-01/031200")
        .set({ ...reading(), acquired_at: "2000-01-01T03:12:00-06:00" }),
    );
  });
});

describe("users", () => {
  it("unauthenticated cannot read readings", async () => {
    await assertFails(db().ref("readings/MSU1").get());
  });

  it("device account without users entry cannot read", async () => {
    await assertFails(db("dev1").ref("readings/MSU1").get());
  });

  it("grower can read readings but cannot write users", async () => {
    await assertSucceeds(db("u1").ref("readings/MSU1").get());
    await assertFails(db("u1").ref("users/u1/role").set("admin"));
  });

  it("admin can write devices and users", async () => {
    await assertSucceeds(db("admin1").ref("devices/dev2").set("HSH1"));
    await assertSucceeds(db("admin1").ref("users/u2").set({ role: "technician" }));
  });
});
