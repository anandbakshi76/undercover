import { createContext, useContext } from "react";
import type { Handler } from "./api";

export type AuthState =
  | { status: "loading" }
  | { status: "signed-out"; message: string | null }
  | { status: "signed-in"; handler: Handler };

export interface Auth {
  state: AuthState;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<Auth>({
  state: { status: "loading" },
  signIn: () => Promise.resolve(),
  signOut: () => Promise.resolve(),
});

export function useAuth(): Auth {
  return useContext(AuthContext);
}
