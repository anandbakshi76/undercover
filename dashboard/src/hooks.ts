import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

export const POLL_MS = 2000;
export const THEME_KEY = "undercover-theme";

export type Theme = "light" | "dark";
export type Appearance = Theme | "system";

const appearanceListeners = new Set<() => void>();

function storedAppearance(): Appearance {
  const stored = localStorage.getItem(THEME_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

function subscribeAppearance(onChange: () => void): () => void {
  appearanceListeners.add(onChange);
  return () => {
    appearanceListeners.delete(onChange);
  };
}

export function setAppearance(next: Appearance): void {
  if (next === "system") localStorage.removeItem(THEME_KEY);
  else localStorage.setItem(THEME_KEY, next);
  for (const listener of appearanceListeners) listener();
}

function systemTheme(): Theme {
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function subscribeSystem(onChange: () => void): () => void {
  const query = matchMedia("(prefers-color-scheme: dark)");
  query.addEventListener("change", onChange);
  return () => {
    query.removeEventListener("change", onChange);
  };
}

export function useAppearance(): Appearance {
  return useSyncExternalStore(subscribeAppearance, storedAppearance);
}

export function useTheme() {
  const appearance = useAppearance();
  const system = useSyncExternalStore(subscribeSystem, systemTheme);
  const theme = appearance === "system" ? system : appearance;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggle = useCallback(() => {
    setAppearance(theme === "dark" ? "light" : "dark");
  }, [theme]);

  return { theme, toggle };
}

export type Route =
  | { page: "queue" }
  | { page: "claim"; claimId: string }
  | { page: "configuration" }
  | { page: "improvement" }
  | { page: "settings" }
  | { page: "scoreboard" }
  | { page: "evaluation"; runId: string };

export function parseRoute(hash: string): Route {
  const claim = /^#\/claims\/([\w-]+)$/.exec(hash);
  if (claim?.[1]) return { page: "claim", claimId: claim[1] };
  const evaluation = /^#\/evaluations\/([\w-]+)$/.exec(hash);
  if (evaluation?.[1]) return { page: "evaluation", runId: evaluation[1] };
  if (hash === "#/configuration") return { page: "configuration" };
  if (hash === "#/improvement") return { page: "improvement" };
  if (hash === "#/settings") return { page: "settings" };
  if (hash === "#/scoreboard") return { page: "scoreboard" };
  return { page: "queue" };
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => {
    window.removeEventListener("hashchange", onChange);
  };
}

export function useRoute(): Route {
  return parseRoute(
    useSyncExternalStore(subscribe, () => window.location.hash),
  );
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useLoad<T>(
  load: () => Promise<T>,
  shouldPoll: (data: T | null) => boolean = () => false,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    load().then(
      (loaded) => {
        setData(loaded);
        setError(null);
      },
      (failure: unknown) => {
        setError(message(failure));
      },
    );
  }, [load]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const poll = shouldPoll(data);
  useEffect(() => {
    if (!poll) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [refresh, poll]);

  return { data, error, refresh };
}

export function useAction() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const act = useCallback(async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }, []);

  return { act, busy, error };
}
