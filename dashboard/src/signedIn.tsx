import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import type { Handler } from "./api";
import { AuthContext } from "./authContext";
import { dana } from "./testing";

// Renders a page as a handler who has signed in, for the page tests.
export function renderSignedIn(ui: ReactElement, handler: Handler = dana) {
  return render(
    <AuthContext.Provider
      value={{
        state: { status: "signed-in", handler },
        signIn: () => Promise.resolve(),
        signOut: () => Promise.resolve(),
      }}
    >
      {ui}
    </AuthContext.Provider>,
  );
}
