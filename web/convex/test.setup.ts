/// <reference types="vite/client" />
// Shared convex-test helpers. The file name has two dots, so the Convex
// bundler skips it (and the *.test.ts files) on deploy.
import { Webhook } from "svix";

// Every Convex module except tests/setup files (which contain extra dots).
// (Negative patterns rather than the `!(*.*.*)` extglob from the convex-test
// docs, which Vite 8 does not expand.)
export const modules = import.meta.glob(["./**/*.*s", "!./**/*.test.ts", "!./**/*.setup.ts"]);

/** Base64 test-only Svix secret. Not a real credential. */
export const TEST_SVIX_SECRET = btoa("unit-test-svix-secret-not-real");
export const OTHER_SVIX_SECRET = btoa("a-different-unit-test-secret");
export const INBOUND_DOMAIN = "testinbox.resend.app";

let counter = 0;

/** A request init carrying a real Svix signature over `payload`. */
export function signedRequest(payload: unknown, secret = TEST_SVIX_SECRET): RequestInit {
  const body = JSON.stringify(payload);
  const id = `msg_test_${++counter}`;
  const now = new Date();
  const signature = new Webhook(secret).sign(id, now, body);
  return {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "svix-id": id,
      "svix-timestamp": String(Math.floor(now.getTime() / 1000)),
      "svix-signature": signature,
    },
    body,
  };
}
