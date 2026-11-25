import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { type Auth, AuthContext, type AuthState } from "./authContext";
import { SignIn } from "./SignIn";
import { dana, mockApi, summary } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function withAuth(
  ui: ReactElement,
  state: AuthState,
  overrides: Partial<Auth> = {},
) {
  const auth: Auth = {
    state,
    signIn: () => Promise.resolve(),
    signOut: () => Promise.resolve(),
    ...overrides,
  };
  return render(<AuthContext.Provider value={auth}>{ui}</AuthContext.Provider>);
}

const OUT: AuthState = { status: "signed-out", message: null };

it("signs in with an email address and a password", async () => {
  const signIn = vi.fn(() => Promise.resolve());
  withAuth(<SignIn message={null} />, OUT, { signIn });
  fireEvent.change(screen.getByLabelText("Email address"), {
    target: { value: "dana.whitfield@undercover.example" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "a-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await vi.waitFor(() => {
    expect(signIn).toHaveBeenCalledWith(
      "dana.whitfield@undercover.example",
      "a-password",
    );
  });
});

it("shows what Supabase says when the sign-in fails", async () => {
  const signIn = vi.fn(() =>
    Promise.reject(new Error("Invalid login credentials")),
  );
  withAuth(<SignIn message={null} />, OUT, { signIn });
  fireEvent.change(screen.getByLabelText("Email address"), {
    target: { value: "dana.whitfield@undercover.example" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "wrong" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Invalid login credentials",
  );
});

it("shows why a signed-in visitor was sent back, and has no sign-up", () => {
  withAuth(<SignIn message="Sign in as a claims handler to do this." />, OUT);
  expect(screen.getByRole("alert").textContent).toBe(
    "Sign in as a claims handler to do this.",
  );
  expect(screen.queryByText(/sign up|create an account|register/i)).toBeNull();
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeDefined();
});

it("shows the sign-in page instead of any other page when signed out", () => {
  mockApi({
    "GET /api/claims": [summary()],
    "GET /api/dispatches/latest": null,
  });
  window.location.hash = "#/claims/CLM-1001";
  withAuth(<App />, OUT);
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeDefined();
  expect(screen.queryByRole("navigation")).toBeNull();
  window.location.hash = "";
});

it("waits while it finds out who is signed in", () => {
  withAuth(<App />, { status: "loading" });
  expect(screen.getByText("Loading...")).toBeDefined();
  expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();
});

it("shows who is signed in and signs out", async () => {
  mockApi({
    "GET /api/claims": [summary()],
    "GET /api/dispatches/latest": null,
  });
  const signOut = vi.fn(() => Promise.resolve());
  withAuth(<App />, { status: "signed-in", handler: dana }, { signOut });
  expect(screen.getByText("Dana Whitfield")).toBeDefined();
  expect(screen.getByText("Senior Claims Handler")).toBeDefined();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await Promise.resolve();
  });
  expect(signOut).toHaveBeenCalled();
});
