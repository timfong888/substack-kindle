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

// Substack account settings, where the reader replaces their account email with the proxy.
const SUBSTACK_SETTINGS_URL = "https://substack.com/settings?utm_source=user-menu";

// Flip to true once pass-through forwarding (login codes and account mail to the real inbox)
// ships. Until then, changing the Substack email would cut the reader off from login codes.
const PASS_THROUGH_LIVE = false;

function Instructions({ address }: { address: string }) {
  return (
    <section>
      <h2 style={{ fontSize: 18 }}>Connect Substack</h2>
      {!PASS_THROUGH_LIVE && (
        <p style={{ padding: "8px 12px", border: "1px solid #c00", color: "#c00" }}>
          <strong>Not yet:</strong> forwarding to your own inbox isn&apos;t live. If you change your
          Substack email now, Substack login codes will stop reaching you. Wait for this notice to
          disappear.
        </p>
      )}
      <ol>
        <li>
          Copy your proxy address above: <code>{address}</code>
        </li>
        <li>
          Open{" "}
          <a href={SUBSTACK_SETTINGS_URL} target="_blank" rel="noopener noreferrer">
            your Substack settings
          </a>
          , click <strong>Edit</strong> next to <strong>Email</strong>, paste the proxy address and
          save.
        </li>
        <li>
          Substack sends a confirmation to both addresses. Click the one in your own inbox. Your
          newsletters then arrive here, and every other Substack email (login codes, receipts) is
          forwarded to your own inbox.
        </li>
      </ol>
      <p>
        <small>
          Prefer not to change your Substack email? Add a forwarding rule instead: in Gmail, open
          Settings → Forwarding and add <code>{address}</code> (the confirmation code appears on
          this page), then filter <code>from:substack.com</code> to forward to it.
        </small>
      </p>
    </section>
  );
}
