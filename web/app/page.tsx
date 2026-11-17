"use client";

import { SignInButton, SignUpButton, UserButton } from "@clerk/nextjs";
import {
  Authenticated,
  AuthLoading,
  Unauthenticated,
  useAction,
  useMutation,
  useQuery,
} from "convex/react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../convex/_generated/api";

const card: React.CSSProperties = {
  border: "1px solid #ddd",
  borderRadius: 10,
  padding: "14px 16px",
  margin: "16px 0",
};
const row: React.CSSProperties = { display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" };
const input: React.CSSProperties = { flex: "1 1 220px", padding: "10px 12px", fontSize: 16 };
const button: React.CSSProperties = { padding: "10px 14px", fontSize: 16 };

function errorText(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  // Convex wraps thrown errors; show the human sentence only.
  const m = /Uncaught Error: (.*?)(\n|$)/.exec(msg);
  return m ? m[1] : msg;
}

export default function Home() {
  return (
    <main style={{ maxWidth: 640, margin: "0 auto", padding: "0 16px 48px" }}>
      <h1 style={{ fontSize: 24 }}>Substack to Kindle</h1>
      <AuthLoading>
        <p>Loading…</p>
      </AuthLoading>
      <Unauthenticated>
        <p>Your Substack publications, delivered to your Kindle as one daily digest.</p>
        <p style={row}>
          <SignUpButton mode="modal" />
          <SignInButton mode="modal" />
        </p>
      </Unauthenticated>
      <Authenticated>
        <Onboarding />
      </Authenticated>
    </main>
  );
}

function Onboarding() {
  const state = useQuery(api.account.state);
  const ensureSender = useMutation(api.account.ensureSender);
  const needsSender = state != null && state.senderAddress === null;
  const requested = useRef(false);

  useEffect(() => {
    // Once per page load; ensureSender is idempotent server-side anyway.
    if (needsSender && !requested.current) {
      requested.current = true;
      ensureSender().catch(() => {
        requested.current = false; // allow a retry on the next render
      });
    }
  }, [needsSender, ensureSender]);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <UserButton />
      </div>
      {state === undefined ? (
        <p>Loading…</p>
      ) : state === null ? (
        <p>Setting up your account… this takes a few seconds after sign-up.</p>
      ) : (
        <>
          <KindleStep state={state} />
          <SubscriptionsStep />
          <DigestStep state={state} />
        </>
      )}
    </>
  );
}

type State = NonNullable<ReturnType<typeof useQuery<typeof api.account.state>>>;

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      style={button}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function KindleStep({ state }: { state: State }) {
  const setKindleEmail = useMutation(api.account.setKindleEmail);
  const sendTest = useAction(api.account.sendTest);
  const confirmKindle = useMutation(api.account.confirmKindle);
  const [email, setEmail] = useState(state.kindleEmail ?? "");
  const [store, setStore] = useState(state.amazonStore);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      if (done) setMessage(done);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const save = (e: FormEvent) => {
    e.preventDefault();
    void run(() => setKindleEmail({ kindleEmail: email, amazonStore: store }), "Saved.");
  };

  return (
    <section style={card}>
      <h2 style={{ fontSize: 18, marginTop: 0 }}>
        1. Your Kindle {state.kindleConfirmedAt ? "✓" : ""}
      </h2>

      <form onSubmit={save}>
        <label htmlFor="kindle-email">
          <strong>Your Send-to-Kindle address</strong>
        </label>
        <div style={row}>
          <input
            id="kindle-email"
            type="email"
            inputMode="email"
            autoComplete="off"
            placeholder="yourname@kindle.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={input}
          />
          <select value={store} onChange={(e) => setStore(e.target.value)} style={button} aria-label="Amazon store">
            {state.amazonStores.map((s) => (
              <option key={s} value={s}>
                amazon.{s}
              </option>
            ))}
          </select>
          <button type="submit" style={button} disabled={busy}>
            Save
          </button>
        </div>
        <p style={{ margin: "6px 0 0" }}>
          <small>
            Find it on Amazon under <em>Preferences → Personal Document Settings</em> (link below).
          </small>
        </p>
      </form>

      <p style={{ marginBottom: 4 }}>
        <strong>Approve your personal sender</strong> on Amazon so your Kindle accepts our digests:
      </p>
      {state.senderAddress ? (
        <div style={row}>
          <code style={{ padding: "8px 10px", background: "#f2f2f2", wordBreak: "break-all", flex: "1 1 220px" }}>
            {state.senderAddress}
          </code>
          <CopyButton text={state.senderAddress} />
        </div>
      ) : (
        <p>Preparing your sender address…</p>
      )}
      <ol style={{ paddingLeft: 20 }}>
        <li>Tap Copy above.</li>
        <li>
          <a href={state.amazonUrl} target="_blank" rel="noopener noreferrer">
            Open Amazon&apos;s Kindle settings
          </a>{" "}
          and sign in if asked.
        </li>
        <li>
          If you don&apos;t land there, tap <em>Preferences</em>, then <em>Personal Document Settings</em>.
        </li>
        <li>
          Under <em>Approved Personal Document E-mail List</em>, tap <em>Add a new approved e-mail address</em>,
          paste, and tap <em>Add Address</em>.
        </li>
      </ol>

      <div style={row}>
        <button
          type="button"
          style={button}
          disabled={busy || !state.kindleEmail || !state.senderAddress}
          onClick={() =>
            void run(() => sendTest({}), "Test sent. It usually arrives on your Kindle within a few minutes.")
          }
        >
          {state.testSentAt ? "Send another test" : "Done, send a test"}
        </button>
        {state.testSentAt && !state.kindleConfirmedAt && (
          <button type="button" style={button} disabled={busy} onClick={() => void run(() => confirmKindle({}))}>
            It arrived
          </button>
        )}
      </div>
      {state.testSentAt && !state.kindleConfirmedAt && (
        <p>
          <small>
            Nothing after 10 minutes? Check the sender above is on your approved list exactly, and that your Kindle
            address is right.
          </small>
        </p>
      )}
      {state.kindleConfirmedAt && <p>Your Kindle is connected.</p>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}

function SubscriptionsStep() {
  const subs = useQuery(api.subscriptions.list);
  const add = useAction(api.subscriptions.add);
  const search = useAction(api.subscriptions.search);
  const remove = useMutation(api.subscriptions.remove);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [hits, setHits] = useState<{ name: string; url: string }[]>([]);

  const addOne = async (value: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const r = await add({ input: value });
      setMessage(
        r.status === "added"
          ? `Added ${r.title}.`
          : r.status === "exists"
            ? `${r.title} is already on your list.`
            : "Your list is full.",
      );
      setText("");
      setHits([]);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const value = text.trim();
    if (!value) return;
    // A URL or handle is added directly; words with spaces are a name search.
    if (/\s/.test(value)) {
      setBusy(true);
      setMessage(null);
      try {
        const found = await search({ query: value });
        setHits(found);
        if (found.length === 0) setMessage("No matches. Paste the publication's URL instead.");
      } catch (err) {
        setMessage(errorText(err));
      } finally {
        setBusy(false);
      }
    } else {
      await addOne(value);
    }
  };

  return (
    <section style={card}>
      <h2 style={{ fontSize: 18, marginTop: 0 }}>
        2. Your publications {subs && subs.length > 0 ? `(${subs.length})` : ""}
      </h2>
      <form onSubmit={onSubmit} style={row}>
        <input
          aria-label="Publication URL, handle or name"
          placeholder="URL, handle (bytebytego) or name"
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={input}
        />
        <button type="submit" style={button} disabled={busy}>
          {busy ? "…" : "Add"}
        </button>
      </form>
      <p style={{ margin: "6px 0 0" }}>
        <small>
          Free posts arrive in full; paid posts arrive as their free preview with a link.
        </small>
      </p>
      {hits.length > 0 && (
        <ul style={{ paddingLeft: 16 }}>
          {hits.map((h) => (
            <li key={h.url} style={{ margin: "6px 0" }}>
              {h.name}{" "}
              <button type="button" disabled={busy} onClick={() => void addOne(h.url)}>
                Add
              </button>
            </li>
          ))}
        </ul>
      )}
      {message && <p role="status">{message}</p>}
      {subs && subs.length > 0 && (
        <ul style={{ paddingLeft: 16 }}>
          {subs.map((s) => (
            <li key={s.id} style={{ margin: "4px 0" }}>
              {s.title}{" "}
              <button
                type="button"
                aria-label={`Remove ${s.title}`}
                onClick={() =>
                  remove({ id: s.id }).catch((e: unknown) => setMessage(errorText(e)))
                }
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DigestStep({ state }: { state: State }) {
  const requestDigest = useMutation(api.account.requestDigest);
  const [message, setMessage] = useState<string | null>(null);
  const ready = Boolean(state.kindleConfirmedAt) && state.subscriptionCount > 0;
  return (
    <section style={card}>
      <h2 style={{ fontSize: 18, marginTop: 0 }}>3. Your digest</h2>
      <p>One EPUB a day with the new posts from your publications.</p>
      <button
        type="button"
        style={button}
        disabled={!ready || state.digestRequestedAt !== null}
        onClick={async () => {
          setMessage(null);
          try {
            await requestDigest({});
          } catch (e) {
            setMessage(errorText(e));
          }
        }}
      >
        {state.digestRequestedAt ? "First digest on its way" : "Send my first digest now"}
      </button>
      {!ready && (
        <p>
          <small>Connect your Kindle and add at least one publication first.</small>
        </p>
      )}
      {state.digestRequestedAt && (
        <p>
          <small>Requested {new Date(state.digestRequestedAt).toLocaleTimeString()}. It usually arrives within 15 minutes.</small>
        </p>
      )}
      {state.lastDigestAt && !state.digestRequestedAt && (
        <p>
          <small>Last digest: {new Date(state.lastDigestAt).toLocaleString()}.</small>
        </p>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
