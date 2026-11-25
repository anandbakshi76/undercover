import { useAuth } from "./authContext";
import { ClaimPage } from "./ClaimPage";
import { Configuration } from "./Configuration";
import { EvaluationPage } from "./EvaluationPage";
import { Improvement } from "./Improvement";
import { type Route, useRoute, useTheme } from "./hooks";
import { Queue } from "./Queue";
import { Scoreboard } from "./Scoreboard";
import { Settings } from "./Settings";
import { SignIn } from "./SignIn";

function Page({ route }: Readonly<{ route: Route }>) {
  switch (route.page) {
    case "claim":
      return <ClaimPage key={route.claimId} claimId={route.claimId} />;
    case "evaluation":
      return <EvaluationPage key={route.runId} runId={route.runId} />;
    case "configuration":
      return <Configuration />;
    case "improvement":
      return <Improvement />;
    case "settings":
      return <Settings />;
    case "scoreboard":
      return <Scoreboard />;
    case "queue":
      return <Queue />;
  }
}

export function App() {
  const route = useRoute();
  const { theme, toggle } = useTheme();
  const { state, signOut } = useAuth();
  if (state.status === "loading") return <p className="loading">Loading...</p>;
  if (state.status === "signed-out") return <SignIn message={state.message} />;
  const { handler } = state;
  return (
    <div className="app">
      <header className="top">
        <a className="brand" href="#/">
          UnderCover <span className="tagline">Claims</span>
        </a>
        <nav aria-label="Main">
          <a
            href="#/"
            aria-current={route.page === "queue" ? "page" : undefined}
          >
            Queue
          </a>
          <a
            href="#/scoreboard"
            aria-current={
              route.page === "scoreboard" || route.page === "evaluation"
                ? "page"
                : undefined
            }
          >
            Scoreboard
          </a>
          <a
            href="#/configuration"
            aria-current={route.page === "configuration" ? "page" : undefined}
          >
            Configuration
          </a>
          <a
            href="#/improvement"
            aria-current={route.page === "improvement" ? "page" : undefined}
          >
            Improvement
          </a>
          <a
            href="#/settings"
            aria-current={route.page === "settings" ? "page" : undefined}
          >
            Settings
          </a>
          <span className="who">
            <strong>{handler.name}</strong>
            <span className="note">{handler.role}</span>
          </span>
          <button
            type="button"
            className="theme-toggle"
            aria-pressed={theme === "dark"}
            onClick={toggle}
          >
            Dark mode
          </button>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => {
              void signOut();
            }}
          >
            Sign out
          </button>
        </nav>
      </header>
      <main>
        <Page route={route} />
      </main>
    </div>
  );
}
