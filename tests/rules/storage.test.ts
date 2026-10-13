import { readFileSync } from "node:fs";
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from "@firebase/rules-unit-testing";
import { afterAll, beforeAll, describe, it } from "vitest";

let env: RulesTestEnvironment;

beforeAll(async () => {
  env = await initializeTestEnvironment({
    projectId: "demo-swd-web",
    storage: { rules: readFileSync("storage.rules", "utf8") },
  });
});

afterAll(async () => {
  await env.cleanup();
});

const put = (uid: string | null, bytes: number, contentType: string) => {
  const ctx = uid ? env.authenticatedContext(uid) : env.unauthenticatedContext();
  return ctx
    .storage()
    .ref("thumbs/MSU1/2026-07-15.jpg")
    .put(new Uint8Array(bytes), { contentType });
};

describe("thumbs", () => {
  it("signed-in jpeg under 500KB allowed", async () => {
    await assertSucceeds(put("dev1", 100 * 1024, "image/jpeg"));
  });

  it("png rejected", async () => {
    await assertFails(put("dev1", 1024, "image/png"));
  });

  it("600KB rejected", async () => {
    await assertFails(put("dev1", 600 * 1024, "image/jpeg"));
  });

  it("unauthenticated upload rejected", async () => {
    await assertFails(put(null, 1024, "image/jpeg"));
  });

  it("signed-in user can read, anonymous cannot", async () => {
    await assertSucceeds(put("dev1", 1024, "image/jpeg"));
    const path = "thumbs/MSU1/2026-07-15.jpg";
    await assertSucceeds(env.authenticatedContext("u1").storage().ref(path).getMetadata());
    await assertFails(env.unauthenticatedContext().storage().ref(path).getMetadata());
  });
});
