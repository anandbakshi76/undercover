import { useState } from "react";
import { useAuth } from "./authContext";
import { useAction } from "./hooks";

export function SignIn({ message }: Readonly<{ message: string | null }>) {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const action = useAction();
  const problem = action.error ?? message;

  return (
    <div className="app">
      <header className="top">
        <span className="brand">
          UnderCover <span className="tagline">Claims</span>
        </span>
      </header>
      <main>
        <form
          className="panel sign-in"
          onSubmit={(event) => {
            event.preventDefault();
            void action.act(() => signIn(email, password));
          }}
        >
          <h1>Sign in</h1>
          <p className="note">
            Use your UnderCover email address. Ask the head of claims operations
            if you need an account.
          </p>
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />
          {problem && <p role="alert">{problem}</p>}
          <button type="submit" disabled={action.busy}>
            Sign in
          </button>
        </form>
      </main>
    </div>
  );
}
