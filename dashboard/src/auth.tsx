import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api, setAccessToken, setUnauthorizedHandler } from "./api";
import { type AuthState, AuthContext } from "./authContext";
import { supabase } from "./supabase";

const SIGNED_OUT: AuthState = { status: "signed-out", message: null };

export function AuthProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  const signOut = useCallback(async () => {
    await supabase().auth.signOut();
    setAccessToken(null);
    setState(SIGNED_OUT);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      void signOut();
    });
    const { data } = supabase().auth.onAuthStateChange((_event, session) => {
      setAccessToken(session?.access_token ?? null);
      if (!session) {
        setState(SIGNED_OUT);
        return;
      }
      // The change handler must not call Supabase itself, so look the handler up after it returns.
      setTimeout(() => {
        api.me().then(
          (handler) => {
            setState({ status: "signed-in", handler });
          },
          (failure: unknown) => {
            setAccessToken(null);
            void supabase().auth.signOut();
            setState({
              status: "signed-out",
              message:
                failure instanceof Error ? failure.message : "Sign in failed.",
            });
          },
        );
      }, 0);
    });
    return () => {
      data.subscription.unsubscribe();
    };
  }, [signOut]);

  const signIn = useCallback(async (email: string, password: string) => {
    const { error } = await supabase().auth.signInWithPassword({
      email,
      password,
    });
    if (error) throw new Error(error.message);
  }, []);

  const value = useMemo(
    () => ({ state, signIn, signOut }),
    [state, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
