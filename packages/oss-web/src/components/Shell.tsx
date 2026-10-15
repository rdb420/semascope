import { Link, useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api, ApiError } from "../api";

export function useUser() {
  return useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    retry: false,
    staleTime: 60_000,
  });
}

export function Shell({ children }: { children: ReactNode }) {
  const user = useUser();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const loggedIn = user.isSuccess;
  const authError = user.error instanceof ApiError && user.error.status === 401;

  async function logout() {
    await api.logout();
    await qc.invalidateQueries();
    navigate({ to: "/login" });
  }

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            Semascope
          </Link>
          <nav className="ml-auto flex items-center gap-4 text-sm">
            {loggedIn ? (
              <>
                <span className="text-zinc-500">{user.data.username}</span>
                {user.data.is_staff && (
                  <a href="/admin/" className="hover:underline">
                    Admin
                  </a>
                )}
                <a href="/api/docs" className="hover:underline">
                  API
                </a>
                <button onClick={logout} className="hover:underline">
                  Sign out
                </button>
              </>
            ) : (
              <Link to="/login" className="hover:underline">
                Sign in
              </Link>
            )}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        {authError && location.pathname !== "/login" ? <SignInPrompt /> : children}
      </main>
    </div>
  );
}

function SignInPrompt() {
  return (
    <div className="mx-auto max-w-md rounded-lg border border-zinc-200 bg-white p-6 text-center dark:border-zinc-800 dark:bg-zinc-900">
      <p className="mb-4">Sign in to search this Semascope.</p>
      <Link
        to="/login"
        className="inline-block rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
      >
        Sign in
      </Link>
    </div>
  );
}
