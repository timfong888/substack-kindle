"use client";

import { SignInButton, SignUpButton, UserButton } from "@clerk/nextjs";
import { Authenticated, AuthLoading, Unauthenticated, useQuery } from "convex/react";
import { useState } from "react";
import { api } from "../convex/_generated/api";

export default function Home() {
  return (
    <main>
      <h1 style={{ fontSize: 24 }}>Newsletters to Kindle</h1>
      <AuthLoading>
        <p>Loading…</p>
      </AuthLoading>
      <Unauthenticated>
        <p>Sign in to get your private forwarding address.</p>
        <p style={{ display: "flex", gap: 12 }}>
          <SignInButton mode="modal" />
          <SignUpButton mode="modal" />
        </p>
      </Unauthenticated>
      <Authenticated>
        <Dashboard />
      </Authenticated>
    </main>
  );
}

function Dashboard() {
  const me = useQuery(api.users.current);
  const confirmations = useQuery(api.users.recentConfirmations);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <UserButton />
      </div>
      {me === undefined ? (
        <p>Loading…</p>
      ) : me === null ? (
        <p>Setting up your address… this takes a few seconds after sign-up.</p>
      ) : (
        <>
          <h2 style={{ fontSize: 18 }}>Your proxy address</h2>
          <ProxyAddress address={me.proxyAddress} />
          <Instructions address={me.proxyAddress} />
        </>
      )}
      {confirmations && confirmations.length > 0 && (
        <section>
          <h2 style={{ fontSize: 18 }}>Forwarding confirmations</h2>
          <ul>
            {confirmations.map((c) => (
              <li key={c.id}>
                {c.code ? (
                  <>
                    Code: <strong>{c.code}</strong>
                  </>
                ) : (
                  c.subject
                )}{" "}
                <small>({new Date(c.receivedAt).toLocaleString()})</small>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function ProxyAddress({ address }: { address: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <p style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <code style={{ padding: "4px 8px", background: "#f2f2f2", wordBreak: "break-all" }}>
        {address}
      </code>
      <button type="button" onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </p>
  );
}

function Instructions({ address }: { address: string }) {
  return (
    <section>
      <h2 style={{ fontSize: 18 }}>Send your newsletters here</h2>
      <ol>
        <li>
          <strong>Recommended: add a forwarding rule.</strong> In Gmail, open Settings → Forwarding
          and add <code>{address}</code>. Gmail sends a confirmation code to this address; it
          appears on this page within a minute. Then create a filter (e.g. from:
          <code>substack.com</code>) that forwards matching mail to this address.
        </li>
        <li>
          <strong>Last resort: change your newsletter email.</strong> You can change your Substack
          account email to this address instead. Warning: this is account-wide. Substack login
          links will then go to this address, not your inbox, and Substack logs you out
          everywhere. Prefer the forwarding rule.
        </li>
      </ol>
    </section>
  );
}
