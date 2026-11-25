import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setAccessToken } from "./api";
import { AuthProvider } from "./auth";
import { useAuth } from "./authContext";
import { dana, Failure, mockApi } from "./testing";

type Listener = (
  event: string,
  session: { access_token: string } | null,
) => void;

const fake = vi.hoisted(() => ({
  listeners: [] as Listener[],
  unsubscribe: vi.fn(),
  signOut: vi.fn(() => Promise.resolve({ error: null })),
  signInWithPassword: vi.fn(),
}));

vi.mock("./supabase", () => ({
  supabase: () => ({
    auth: {
      onAuthStateChange: (listener: Listener) => {
        fake.listeners.push(listener);
        return { data: { subscription: { unsubscribe: fake.unsubscribe } } };
      },
      signOut: fake.signOut,
      signInWithPassword: fake.signInWithPassword,
    },
  }),
}));

function Who() {
  const { state, signIn } = useAuth();
  return (
    <>
      <p>
        {state.status === "signed-in"
          ? `in as ${state.handler.name}`
          : state.status}
      </p>
      {state.status === "signed-out" && state.message && <p>{state.message}</p>}
      <button
        type="button"
        onClick={() => {
          signIn("a@b.c", "pw").catch((failure: unknown) => {
            document.title = failure instanceof Error ? failure.message : "";
          });
        }}
      >
        go
      </button>
    </>
  );
}

beforeEach(() => {
  fake.listeners.length = 0;
  fake.signOut.mockClear();
  fake.unsubscribe.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setAccessToken(null);
});

function announce(session: { access_token: string } | null) {
  for (const listener of fake.listeners) listener("INITIAL_SESSION", session);
}

it("is signed out when there is no session", async () => {
  render(
    <AuthProvider>
      <Who />
    </AuthProvider>,
  );
  expect(screen.getByText("loading")).toBeDefined();
  announce(null);
  expect(await screen.findByText("signed-out")).toBeDefined();
});

it("looks the handler up with the session's token", async () => {
  const fetchMock = mockApi({ "GET /api/me": dana });
  render(
    <AuthProvider>
      <Who />
    </AuthProvider>,
  );
  announce({ access_token: "the-token" });
  expect(await screen.findByText("in as Dana Whitfield")).toBeDefined();
  const [, init] = fetchMock.mock.calls[0] ?? [];
  expect(new Headers(init?.headers).get("Authorization")).toBe(
    "Bearer the-token",
  );
});

it("signs out and says why when the backend knows no such handler", async () => {
  mockApi({
    "GET /api/me": new Failure(401, "Sign in as a claims handler to do this."),
  });
  render(
    <AuthProvider>
      <Who />
    </AuthProvider>,
  );
  announce({ access_token: "a-stranger" });
  expect(
    await screen.findByText("Sign in as a claims handler to do this."),
  ).toBeDefined();
  expect(screen.getByText("signed-out")).toBeDefined();
  expect(fake.signOut).toHaveBeenCalled();
});

it("goes back to sign-in when the backend stops accepting the token", async () => {
  mockApi({
    "GET /api/me": dana,
    "GET /api/claims": new Failure(401, "Sign in to do this."),
  });
  render(
    <AuthProvider>
      <Who />
    </AuthProvider>,
  );
  announce({ access_token: "the-token" });
  await screen.findByText("in as Dana Whitfield");
  const { api } = await import("./api");
  await expect(api.claims()).rejects.toThrow("Sign in to do this.");
  expect(await screen.findByText("signed-out")).toBeDefined();
  expect(fake.signOut).toHaveBeenCalled();
});

it("signs in with Supabase and passes its message on when it fails", async () => {
  fake.signInWithPassword.mockResolvedValue({
    error: { message: "Invalid login credentials" },
  });
  render(
    <AuthProvider>
      <Who />
    </AuthProvider>,
  );
  announce(null);
  (await screen.findByRole("button", { name: "go" })).click();
  await vi.waitFor(() => {
    expect(document.title).toBe("Invalid login credentials");
  });
  expect(fake.signInWithPassword).toHaveBeenCalledWith({
    email: "a@b.c",
    password: "pw",
  });
});

it("stops listening when it is removed", () => {
  const { unmount } = render(
    <AuthProvider>
      <Who />
    </AuthProvider>,
  );
  unmount();
  expect(fake.unsubscribe).toHaveBeenCalled();
});
