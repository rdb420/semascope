import { useState, type FormEvent } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(username, password);
      await qc.invalidateQueries({ queryKey: ["me"] });
      navigate({ to: "/" });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Wrong user name or password." : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mx-auto mt-10 max-w-sm space-y-4 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <h1 className="text-lg font-semibold">Sign in</h1>
      <label className="block text-sm">
        User name
        <input
          autoFocus
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mt-1 w-full rounded border border-zinc-300 bg-transparent px-3 py-2 dark:border-zinc-700"
        />
      </label>
      <label className="block text-sm">
        Password
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded border border-zinc-300 bg-transparent px-3 py-2 dark:border-zinc-700"
        />
      </label>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {providers.data?.oidc && (
        <a href={providers.data.oidc.url} className="block w-full rounded border border-zinc-300 px-4 py-2 text-center hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
          Sign in with {providers.data.oidc.name}
        </a>
      )}
      <button
        disabled={busy}
        className="w-full rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
