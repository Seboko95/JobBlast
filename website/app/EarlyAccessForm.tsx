"use client";

import { FormEvent, useState } from "react";

type FormState = "idle" | "submitting" | "success" | "error";

export function EarlyAccessForm() {
  const [state, setState] = useState<FormState>("idle");
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    setState("submitting");
    setMessage("");
    const form = new FormData(formElement);

    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: form.get("email"),
          role: form.get("role"),
          company: form.get("company"),
        }),
      });
      const result = (await response.json()) as { message?: string; error?: string };
      if (!response.ok) throw new Error(result.error || "We could not save your place.");
      setState("success");
      setMessage(result.message || "You’re on the JobBlast early-access list.");
      formElement.reset();
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "Please try again in a moment.");
    }
  }

  return (
    <form className="waitlist-form" onSubmit={submit}>
      <label htmlFor="email">Email address</label>
      <input id="email" name="email" type="email" autoComplete="email" placeholder="you@example.com" required maxLength={254} />

      <label htmlFor="role">I am joining as</label>
      <select id="role" name="role" defaultValue="job-seeker">
        <option value="job-seeker">A job seeker</option>
        <option value="organisation">A college, NGO or training provider</option>
        <option value="employer">An employer or recruiter</option>
        <option value="partner">A potential partner</option>
      </select>

      <div className="honeypot" aria-hidden="true">
        <label htmlFor="company">Company</label>
        <input id="company" name="company" tabIndex={-1} autoComplete="off" />
      </div>

      <button className="button form-button" type="submit" disabled={state === "submitting"}>
        {state === "submitting" ? "Saving your place…" : "Join early access ↗"}
      </button>
      <p className={`form-status ${state}`} role="status" aria-live="polite">{message}</p>
      <small>By joining, you agree to receive JobBlast launch updates. Unsubscribe anytime.</small>
    </form>
  );
}
