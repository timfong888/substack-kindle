# Proxy inbox: provisioning checklist

What the owner does once to bring up the sign-up and proxy-inbox stack in `web/`
(Clerk auth, Convex DB and webhooks, Resend inbound, Next.js on Vercel). Nothing
here is in the repo: every value below is a secret or deployment-specific, so it
goes into the Clerk, Convex, Resend and Vercel dashboards, never into git.

**Dashboards (open these in order):**
1. Convex: <https://dashboard.convex.dev>: create the project, switch the deployment selector to **Production**, then that deployment's Settings → URL & Deploy Key / Environment Variables
2. Clerk: <https://dashboard.clerk.com>: create the app, Integrations → Convex, Webhooks
3. Resend receiving address: <https://resend.com/emails/receiving>
4. Resend webhooks: <https://resend.com/webhooks>
5. Resend API keys: <https://resend.com/api-keys>
6. Vercel new project: <https://vercel.com/new>: import `timfong888/substack-kindle`, Root Directory `web`

`<deployment>` below is your Convex deployment name (e.g. `happy-otter-123`).
Webhooks go to the `.convex.site` host, not `.convex.cloud`.

### No-terminal route

Every step below can be done in the four dashboards, with no local CLI:
- **Convex:** create the project in the dashboard. Switch the deployment selector at the top to
  **Production**, then open that deployment's **Settings → URL & Deploy Key → Generate Production
  Deploy Key**. (Project-level settings only offer *Preview* deploy keys; don't use those here.
  They create a separate deployment per branch with its own URL and empty env vars, so the
  webhooks would never reach it.) Set step 4's variables on the same Production deployment
  under **Settings → Environment Variables** instead of `npx convex env set`.
- **Vercel** deploys the Convex functions on every build. The build command
  (`npx convex deploy --cmd 'npm run build'`) and framework are set in `web/vercel.json`, so the
  only dashboard settings are **Root Directory** = `web` and the environment variables. Add the
  Production key as `CONVEX_DEPLOY_KEY` for the **Production** environment only. Convex refuses
  a Production key in a Vercel Preview build ("Detected a non-production build environment"), so
  branch previews fail until they get their own Preview deploy key; run the smoke test on a
  Production deployment.
  `NEXT_PUBLIC_CONVEX_URL` is then set automatically during the build.
- Run the smoke test on a Production deployment: merge this PR so `main` deploys, or promote a
  deployment of this branch to Production.

## 1. Convex project

1. `cd web && npm ci && npx convex dev` — log in, create the project. This writes
   `web/.env.local` (`CONVEX_DEPLOYMENT`, `NEXT_PUBLIC_CONVEX_URL`) and pushes the
   functions. Leave it running while you do steps 2–3, or rerun it after.
2. Note the deployment URLs: `https://<deployment>.convex.cloud` (client) and
   `https://<deployment>.convex.site` (HTTP actions / webhooks).

## 2. Clerk application

1. Create an application. Sign-in options: **Email** with **email verification link**
   (magic link). Leave Google off for now.
2. Copy the **Frontend API URL** (Dashboard → API keys). This is
   `CLERK_JWT_ISSUER_DOMAIN`.
3. Integrations → **Convex** → activate (or create a JWT template named exactly
   `convex`). `web/convex/auth.config.ts` expects `applicationID: "convex"`.
4. Webhooks → Add endpoint `https://<deployment>.convex.site/clerk-users-webhook`,
   events `user.created`, `user.updated`, `user.deleted`. Copy its signing secret
   (`CLERK_WEBHOOK_SECRET`).
5. Copy the publishable and secret keys for Vercel (step 5).

## 3. Resend inbound

1. Domains → Receiving: enable the managed receiving domain `<id>.resend.app`
   (every local part is accepted). Record the domain, `<id>.resend.app`. This is
   `RESEND_INBOUND_DOMAIN`.
2. Webhooks → Add `https://<deployment>.convex.site/resend-inbound`, event
   `email.received`. Copy the signing secret (`RESEND_WEBHOOK_SECRET`).
3. API Keys → create a key that can read received emails (`RESEND_API_KEY`).

## 4. Convex environment variables

Set on the deployment (run once per deployment, dev and prod):

```
npx convex env set CLERK_WEBHOOK_SECRET   <from 2.4>
npx convex env set CLERK_JWT_ISSUER_DOMAIN <from 2.2>
npx convex env set RESEND_API_KEY         <from 3.3>
npx convex env set RESEND_WEBHOOK_SECRET  <from 3.2>
npx convex env set RESEND_INBOUND_DOMAIN  <from 3.1, e.g. abc123.resend.app>
npx convex env set PIPELINE_SHARED_SECRET "$(openssl rand -hex 32)"
```

Keep a copy of `PIPELINE_SHARED_SECRET` for the Python pipeline's environment
(`convex_inbox.fetch_inbound(secret=...)`). The secret itself is never sent: each
request carries an HMAC over the address, window and time, valid for 5 minutes, so
the pipeline host's clock must be accurate. Then `npx convex deploy` for prod.

## 5. Vercel

1. Import the repo and set **Root Directory** to `web`. Framework and build command come from
   `web/vercel.json`. If the project was imported as "Python", it fails with
   "No python entrypoint found" until Root Directory is `web`.
2. Environment variables (Vercel project → **Settings → Environment Variables → Project** tab),
   values from Clerk **Development** instance → **Configure → API keys**:

   | Key (exact name) | Value source | Type | Environments |
   |---|---|---|---|
   | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | **Publishable key** (`pk_test_…`) | Config | Production, Preview |
   | `CLERK_SECRET_KEY` | **Secret keys → default** (`sk_test_…`) | Secret | Production, Preview |
   | `CONVEX_DEPLOY_KEY` | Convex **Production** deployment → Settings → URL & Deploy Key | Secret | Production |

   The `NEXT_PUBLIC_` prefix is required: without it the page fails with
   "Clerk keys are missing from your environment". `NEXT_PUBLIC_CONVEX_URL` is set by the build.
   `CLERK_JWT_ISSUER_DOMAIN` belongs on Convex, not Vercel.
3. Deploy to **Production**. Environment variables only apply to builds started after they change.

## 6. Smoke test

1. Open the Vercel URL → **Sign up** with an email → click the magic link.
2. The dashboard shows your proxy address (`<slug>-<8 chars>@<id>.resend.app`)
   within a few seconds. If it says "Setting up", check the Clerk webhook's delivery
   log and the Convex logs for `/clerk-users-webhook`.
3. Send any email to that address from another account.
4. Convex dashboard → Data → `receivedEmails`: a row for your user appears, first
   `bodyStatus: "pending"`, then `"stored"` with `html`/`text` filled. If it stays
   pending, check Convex logs for `inbound:fetchBody` (a non-2xx from Resend is
   logged as an error there).
5. Optional: add the address as a Gmail forwarding address. The confirmation code
   appears on the dashboard under "Forwarding confirmations".
