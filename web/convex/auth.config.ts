// Clerk as the Convex auth provider. The Clerk JWT template must be named
// "convex" (see docs/setup-inbox.md). Set with:
//   npx convex env set CLERK_JWT_ISSUER_DOMAIN https://<your-app>.clerk.accounts.dev
export default {
  providers: [
    {
      domain: process.env.CLERK_JWT_ISSUER_DOMAIN!,
      applicationID: "convex",
    },
  ],
};
