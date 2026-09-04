"use client";

/* eslint-disable @next/next/no-location-assign-relative-destination -- FastAPI sets the session cookie before this full navigation. */

import { useQuery } from "@tanstack/react-query";
import { FormEvent, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api/client";
import { Brand } from "@/components/brand";
import styles from "./login-form.module.css";

type Mode = "signin" | "signup";

export function LoginForm() {
  const { data: config } = useQuery({ queryKey: ["auth-config"], queryFn: api.authConfig });
  const [mode, setMode] = useState<Mode>("signin");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const email = String(values.get("email") || "").trim();
    const password = String(values.get("password") || "");
    const inviteCode = String(values.get("invite-code") || "");
    if (!email.includes("@")) { setError("Enter a valid email address."); emailRef.current?.focus(); return; }
    if (password.length < 8) { setError("Password needs at least 8 characters."); return; }
    setBusy(true); setError("");
    try {
      if (mode === "signup") await api.signup(email, password, inviteCode);
      else await api.login(email, password);
      window.location.href = "/";
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Sign in failed. Try again.");
      setBusy(false);
    }
  }

  const signupOpen = config?.signup_open ?? false;
  return (
    <main className="auth-frame">
      <section className="auth-card" aria-labelledby="auth-heading">
        <div className="auth-brand"><Brand /></div>
        <div>
          <p className="eyebrow">Document workspace</p>
          <h1 id="auth-heading">{mode === "signin" ? "Sign in" : "Create an account"}</h1>
        </div>
        <div className="auth-tabs" role="tablist" aria-label="Authentication mode">
          <button type="button" role="tab" aria-selected={mode === "signin"} onClick={() => { setMode("signin"); setError(""); }}>Sign in</button>
          <button type="button" role="tab" aria-selected={mode === "signup"} disabled={!signupOpen} onClick={() => { setMode("signup"); setError(""); }}>Sign up</button>
        </div>
        {!signupOpen && mode === "signin" && <p className={styles.closed}>Account creation is closed on this server.</p>}
        <form id="auth-form" onSubmit={submit} aria-busy={busy}>
          <label className="auth-field" htmlFor="email"><span>Email</span><input ref={emailRef} id="email" name="email" type="email" autoComplete="email" spellCheck={false} required /></label>
          <div className="auth-field"><label htmlFor="password">Password</label><span className={styles.passwordRow}><input id="password" name="password" type={showPassword ? "text" : "password"} autoComplete={mode === "signup" ? "new-password" : "current-password"} minLength={8} required /><button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? "Hide" : "Show"}</button></span></div>
          {mode === "signup" && <label className="auth-field" htmlFor="invite-code"><span>Invite code</span><input id="invite-code" name="invite-code" type="text" autoComplete="one-time-code" spellCheck={false} required /></label>}
          {error && <p className={styles.error} role="alert">{error}</p>}
          <button className="primary auth-submit" type="submit" disabled={busy}>{busy ? "Working…" : mode === "signin" ? "Sign in" : "Create account"}</button>
        </form>
      </section>
    </main>
  );
}
