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
  const recent = useQuery(api.users.recentEmails);

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
          <Instructions address={me.proxyAddress} email={me.email} />
        </>
      )}
      {recent && recent.length > 0 && <RecentEmails emails={recent} />}
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

function Instructions({ address, email }: { address: string; email: string }) {
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
          Substack shows <strong>Email change pending</strong>. Open the email Substack sent to{" "}
          <code>{email}</code> and click <strong>Confirm Email Change</strong>.
        </li>
        <li>
          Substack then sends a second confirmation to your proxy address. It appears below under{" "}
          <strong>Mail received at your proxy</strong>. Open it and click its confirmation link.
          Your Substack email changes only after both are confirmed.
        </li>
      </ol>
      <div style={{ padding: "8px 12px", border: "1px solid #999", background: "#fafafa" }}>
        <p style={{ margin: "0 0 4px" }}>
          <strong>What you&apos;ll see on Substack after step 2:</strong>
        </p>
        <p style={{ margin: "0 0 4px" }}>
          <strong>Email change pending</strong>
          <br />
          Step 1 of 2: Open the email we sent to {email} and click &quot;Confirm Email
          Change&quot;. We&apos;ll then send a second confirmation email to {address}. Your email
          won&apos;t change until both are confirmed.
        </p>
        <p style={{ margin: 0 }}>
          This is expected. <strong>Don&apos;t sign up for Substack with {address}</strong> while
          you wait: that creates a separate Substack account instead of changing yours.
        </p>
      </div>
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

type ReceivedEmail = {
  id: string;
  from: string;
  subject: string;
  receivedAt: number;
  text: string | null;
};

function RecentEmails({ emails }: { emails: ReceivedEmail[] }) {
  return (
    <section>
      <h2 style={{ fontSize: 18 }}>Mail received at your proxy</h2>
      <ul style={{ paddingLeft: 16 }}>
        {emails.map((e) => (
          <li key={e.id}>
            <details>
              <summary>
                <strong>{e.subject}</strong> <small>from {e.from}</small>{" "}
                <small>({new Date(e.receivedAt).toLocaleString()})</small>
              </summary>
              {e.text === null ? (
                <p>
                  <small>Body not available yet.</small>
                </p>
              ) : (
                <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  <Linkified text={e.text} />
                </pre>
              )}
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Renders plain text with https:// URLs as links (e.g. a confirmation link). */
function Linkified({ text }: { text: string }) {
  return (
    <>
      {text.split(/(https:\/\/[^\s<>"]+)/g).map((part, i) =>
        part.startsWith("https://") ? (
          <a key={i} href={part} target="_blank" rel="noopener noreferrer">
            {part}
          </a>
        ) : (
          part
        ),
      )}
    </>
  );
}
