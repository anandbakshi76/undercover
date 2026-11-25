import { type SyntheticEvent, useState } from "react";
import { api, type ModelAllowed, type Setting } from "./api";
import { useAuth } from "./authContext";
import { ConfirmDialog } from "./ConfirmDialog";
import { dateTime, money } from "./format";
import {
  type Appearance,
  setAppearance,
  useAction,
  useAppearance,
  useLoad,
} from "./hooks";
import { PageHeader } from "./PageHeader";
import { ErrorState, Loading } from "./States";

interface Field {
  path: string;
  label: string;
  unit: string;
  step: number;
}

interface Group {
  key: string;
  title: string;
  help: string;
  money?: boolean;
  fields: Field[];
}

const MEASURES = (unit: (name: string) => string): Field[] => [
  { path: "accuracy", label: "Accuracy", unit: unit("accuracy"), step: 0.01 },
  { path: "grade", label: "Grade", unit: unit("grade"), step: 0.01 },
  {
    path: "cost_per_claim",
    label: "Cost per claim",
    unit: unit("cost_per_claim"),
    step: 0.05,
  },
  {
    path: "seconds_per_claim",
    label: "Time per claim",
    unit: unit("seconds_per_claim"),
    step: 0.05,
  },
];

const DIFFERENCE_OR_FRACTION = (name: string) =>
  name === "accuracy" || name === "grade"
    ? "a difference"
    : "a fraction of the old value";

const GROUPS: Group[] = [
  {
    key: "approval_limit",
    title: "Approval limit",
    help: "A payout over this needs a handler's sign-off. A new limit applies to the decisions recorded after the change.",
    money: true,
    fields: [{ path: "", label: "Limit", unit: "US dollars", step: 50 }],
  },
  {
    key: "objectives",
    title: "Business objectives",
    help: "The floors a new version must meet on the held-out claims.",
    fields: [
      { path: "accuracy", label: "Accuracy floor", unit: "0 to 1", step: 0.05 },
      { path: "grade", label: "Grade floor", unit: "0 to 3", step: 0.1 },
    ],
  },
  {
    key: "noise",
    title: "Noise",
    help: "How far a score has to improve before it counts.",
    fields: MEASURES(DIFFERENCE_OR_FRACTION),
  },
  {
    key: "tolerance",
    title: "Tolerance",
    help: "How far a score can get worse in a version that improves another.",
    fields: MEASURES(DIFFERENCE_OR_FRACTION),
  },
  {
    key: "round_limit",
    title: "Spending limit for each round",
    help: "What the improver's own model calls, all of the round's evaluations and the judge may cost.",
    money: true,
    fields: [{ path: "", label: "Limit", unit: "US dollars", step: 0.1 }],
  },
  {
    key: "improver_concurrency",
    title: "Claim agents at once in the improver's evaluations",
    help: "How many claims the improver's evaluations work at the same time.",
    fields: [{ path: "", label: "Claim agents", unit: "1 to 25", step: 1 }],
  },
];

const APPEARANCES: [Appearance, string][] = [
  ["light", "Light"],
  ["dark", "Dark"],
  ["system", "The same as the computer"],
];

function valueOf(setting: Setting, path: string): number {
  const value = setting.value;
  if (path === "") return Number(value);
  return (value as Record<string, number>)[path] ?? 0;
}

function shown(group: Group, setting: Setting, field: Field): string {
  const value = valueOf(setting, field.path);
  return group.money ? money(value) : String(value);
}

function SettingForm({
  group,
  setting,
  canEdit,
  onSaved,
}: Readonly<{
  group: Group;
  setting: Setting;
  canEdit: boolean;
  onSaved: () => void;
}>) {
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      group.fields.map((field) => [
        field.path,
        String(valueOf(setting, field.path)),
      ]),
    ),
  );
  const [confirming, setConfirming] = useState(false);
  const action = useAction();

  const next = Object.fromEntries(
    group.fields.map((field) => [field.path, Number(draft[field.path])]),
  );
  const changed = group.fields.some(
    (field) => next[field.path] !== valueOf(setting, field.path),
  );
  const value = group.fields[0]?.path === "" ? next[""] : next;

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setConfirming(true);
  };
  const save = () =>
    action.act(async () => {
      await api.changeSetting(group.key, value);
      setConfirming(false);
      onSaved();
    });

  return (
    <form className="panel setting" onSubmit={submit}>
      <h2>{group.title}</h2>
      <p className="note">{group.help}</p>
      <div className="setting-fields">
        {group.fields.map((field) => {
          const id = `${group.key}-${field.path || "value"}`;
          return (
            <div key={field.path} className="setting-field">
              <label htmlFor={id}>{field.label}</label>
              <input
                id={id}
                type="number"
                step={field.step}
                disabled={!canEdit}
                value={draft[field.path] ?? ""}
                onChange={(event) => {
                  setDraft({ ...draft, [field.path]: event.target.value });
                }}
              />
              <span className="unit">{field.unit}</span>
            </div>
          );
        })}
      </div>
      <p className="note">
        {setting.changed_by && setting.changed_at
          ? `Last changed by ${setting.changed_by} on ${dateTime(setting.changed_at)}.`
          : "Not changed since the start."}
      </p>
      {canEdit && (
        <button type="submit" disabled={!changed}>
          Save {group.title.toLowerCase()}
        </button>
      )}
      {action.error && <p role="alert">{action.error}</p>}
      {confirming && (
        <ConfirmDialog
          title={`Change ${group.title.toLowerCase()}?`}
          confirmLabel="Change it"
          busy={action.busy}
          onConfirm={() => void save()}
          onCancel={() => {
            setConfirming(false);
          }}
        >
          <ul className="plain">
            {group.fields.map((field) => (
              <li key={field.path}>
                {field.label}:{" "}
                <span className="mono">{shown(group, setting, field)}</span> to{" "}
                <span className="mono">
                  {group.money
                    ? money(next[field.path] ?? 0)
                    : String(next[field.path])}
                </span>
              </li>
            ))}
          </ul>
        </ConfirmDialog>
      )}
    </form>
  );
}

function Models({ models }: Readonly<{ models: ModelAllowed[] }>) {
  return (
    <section className="panel table-panel" aria-label="Models allowed">
      <h2>Models allowed</h2>
      <p className="note">
        The claim agent and the improver may use these models. They are set in a
        file, <span className="mono">data/models.json</span>, and can&apos;t be
        changed here.
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Reasoning efforts</th>
            <th scope="col" className="number">
              Input, per million tokens
            </th>
            <th scope="col" className="number">
              Output, per million tokens
            </th>
          </tr>
        </thead>
        <tbody>
          {models.map((item) => (
            <tr key={item.model}>
              <td className="mono">{item.model}</td>
              <td className="mono">{item.reasoning_efforts.join(", ")}</td>
              <td className="number mono">{money(item.input_price)}</td>
              <td className="number mono">{money(item.output_price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function AppearanceChoice() {
  const appearance = useAppearance();
  return (
    <fieldset className="panel appearance">
      <legend>Appearance</legend>
      <p className="note">Each browser remembers its own choice.</p>
      {APPEARANCES.map(([value, label]) => (
        <label key={value} className="choice">
          <input
            type="radio"
            name="appearance"
            checked={appearance === value}
            onChange={() => {
              setAppearance(value);
            }}
          />
          {label}
        </label>
      ))}
    </fieldset>
  );
}

function describe(value: unknown): string {
  if (typeof value === "object" && value !== null) {
    return Object.entries(value)
      .map(([name, part]) => `${name} ${String(part)}`)
      .join(", ");
  }
  return String(value);
}

export function Settings() {
  const { state } = useAuth();
  const settings = useLoad(api.settings);
  const models = useLoad(api.models);
  const changes = useLoad(api.settingChanges);

  if (state.status !== "signed-in") return null;
  const { handler } = state;
  const canEdit = handler.can_approve_over_limit;
  const error = settings.error ?? models.error ?? changes.error;
  if (error) {
    return (
      <>
        <PageHeader title="Settings" purpose="The platform's settings." />
        <ErrorState
          message={error}
          next="Check that the backend is running, then reload the page."
        />
      </>
    );
  }
  if (!settings.data || !models.data || !changes.data) {
    return <Loading what="the settings" />;
  }
  const refresh = () => {
    settings.refresh();
    changes.refresh();
  };

  return (
    <>
      <PageHeader
        title="Settings"
        purpose="The approval limit, the improver's objectives and limits, the models allowed and the appearance."
      />
      {canEdit ? null : (
        <p className="note notice">
          {handler.name} can&apos;t change the settings. Only a handler who can
          sign off payouts over the limit may.
        </p>
      )}
      {GROUPS.map((group) => {
        const setting = settings.data?.find((item) => item.key === group.key);
        return setting ? (
          <SettingForm
            key={`${group.key}-${JSON.stringify(setting.value)}`}
            group={group}
            setting={setting}
            canEdit={canEdit}
            onSaved={refresh}
          />
        ) : null;
      })}
      <Models models={models.data} />
      <AppearanceChoice />
      <section
        className="panel table-panel"
        aria-label="Changes to the settings"
      >
        <h2>Changes to the settings</h2>
        {changes.data.length === 0 ? (
          <p className="note">
            No setting has been changed. A change will be listed here with who
            made it and when.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Setting</th>
                <th scope="col">From</th>
                <th scope="col">To</th>
                <th scope="col">By</th>
              </tr>
            </thead>
            <tbody>
              {changes.data.map((change) => (
                <tr key={`${change.key}-${change.changed_at}`}>
                  <td className="mono">{dateTime(change.changed_at)}</td>
                  <td>
                    {GROUPS.find((group) => group.key === change.key)?.title ??
                      change.key}
                  </td>
                  <td className="mono">{describe(change.old)}</td>
                  <td className="mono">{describe(change.new)}</td>
                  <td>{change.changed_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
