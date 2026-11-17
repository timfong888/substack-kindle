/* eslint-disable */
/**
 * Generated `api` utility.
 *
 * THIS CODE IS AUTOMATICALLY GENERATED.
 *
 * To regenerate, run `npx convex dev`.
 * @module
 */

import type {
  ApiFromModules,
  FilterApi,
  FunctionReference,
} from "convex/server";
import type * as account from "../account.js";
import type * as digest from "../digest.js";
import type * as feeds from "../feeds.js";
import type * as http from "../http.js";
import type * as inbound from "../inbound.js";
import type * as kindle from "../kindle.js";
import type * as pipeline from "../pipeline.js";
import type * as proxyAddress from "../proxyAddress.js";
import type * as subscriptions from "../subscriptions.js";
import type * as users from "../users.js";

/**
 * A utility for referencing Convex functions in your app's API.
 *
 * Usage:
 * ```js
 * const myFunctionReference = api.myModule.myFunction;
 * ```
 */
declare const fullApi: ApiFromModules<{
  account: typeof account;
  digest: typeof digest;
  feeds: typeof feeds;
  http: typeof http;
  inbound: typeof inbound;
  kindle: typeof kindle;
  pipeline: typeof pipeline;
  proxyAddress: typeof proxyAddress;
  subscriptions: typeof subscriptions;
  users: typeof users;
}>;
export declare const api: FilterApi<
  typeof fullApi,
  FunctionReference<any, "public">
>;
export declare const internal: FilterApi<
  typeof fullApi,
  FunctionReference<any, "internal">
>;
