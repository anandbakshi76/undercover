# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history
- find and read the sections of the policy wording and the claims manual it needs, as a handler would
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

Our goal is to handle claims straight through, as accurately as our handlers do.

## Working the queue

Claims arrive in batches, so the agent works many at once (see `docs/SWARM.md`):

- On the dashboard a handler starts a dispatch and chooses how many claim agents work at once, from 1 to 25 with 3 to start with. A dispatch works every claim left in progress and every waiting claim, oldest submitted first. Code chooses the claims and keeps track of the run, with no model. Only one dispatch, evaluation or stretch of the improver's work runs at a time, and a second start is refused, saying what is running. If the agent's service isn't running, the start says so.
- Each claim is worked by a claim agent that works that one claim from a clean start.
- Problems are put right automatically. A claim that hits a rate limit or a timeout waits and carries on from where it got to, as the same attempt, and one that is still hitting them after its waits stays in progress for the next dispatch. A claim that fails with any other error goes back in the queue and the same dispatch tries it once more, and a claim that fails twice is left for a handler. One bad claim never stops the dispatch.
- When the agent approves a payout over the limit, it stops and waits for a handler, keeping everything it has worked out. As soon as a handler signs off on the dashboard, the agent picks the claim up from where it stopped and pays it. Between the sign-off and the payment the claim shows as in progress. If the agent isn't running at that moment, the next dispatch picks the claim up. The payment records who signed off and when. The handler who signs off is the one signed in, and only a handler who can sign off over the limit may.
- If the agent is stopped partway through a claim, by a crash, a restart or Ctrl-C, it carries on with that claim from where it got to the next time it works it, and that doesn't count as a new attempt. A dispatch that was stopped is finished by the next dispatch.
- No claim is ever decided or paid twice. A repeat of a decision or payment already recorded for a claim counts as done. A different decision on a decided claim is refused.
- The agent records the tokens and the cost of each model call and the time it spends working on each claim, which leaves out any wait for a handler's sign-off, against the dispatch or evaluation they belong to.

## Checking the agent's work

- On the Scoreboard page a handler starts an evaluation, with N claim agents at once, on one of two labelled sets (the evaluation set of the queue's 25 claims, or the held-out set), whole or just its five `tiny` claims. An evaluation works its own copies of the claims. The copies never show in the queue, a dispatch never carries them on, and nobody can sign them off.
- The scoreboard lists the evaluations, newest first, with the decision accuracy, the accuracy (the decision matches and the payout is within a dollar), the cost of mistakes (a wrongful decline costs $1,500, a wrongful approval its payout whether or not it was paid, a needless referral $50, and a wrong amount on a right approval the difference), the straight-through rate, the model cost per claim, the time per claim and the time for the whole run. A claim the agent didn't decide counts as a referral. Each evaluation opens to its claims, with the handler's decision beside the agent's.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off, decided or needs a handler.
- The current dispatch: each of its claims and where it stands, its model cost and time, and when it finishes a summary built from our records, with no model writing it: how many claims were approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or still in progress.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step, including the sections of the wording and the manual it read.
- The claim agent's configuration, its instructions, model and reasoning effort, in versions, each with its held-out scores and what it changed.
- The scoreboard, with the judge's average grade beside the other measures, and each claim's grade in an evaluation.
- The improver's rounds: where each has got to, the change or recipe waiting for approval as a diff, the scores before and after, the verdict, the cost, and a chart of the versions tried.
- The settings, and who changed them and when.

## Rules

- A payout over the approval limit, $1,000 to start with and a setting, needs a handler's sign-off. A new limit applies to decisions recorded after it.
- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and the claims manual. The wording comes first; the manual covers what the wording leaves open.
- The agent's instructions are written from the policy wording and the manual, never from our handlers' decisions.
- Our handlers' own decisions and reasons on these claims are in `data/evals/`. We use them to check the agent's work. The claim agent must never see them. The improver reads the decisions and reasons on the evaluation set, and only the scores on the held-out set: nothing in the agent reads the held-out set's decisions.
- Amounts are in US dollars.

## Our records

The claims and everything the agent works on are held in UnderCover's database on Supabase. They start from `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## The improver

A second agent, the improver, proposes better versions of the claim agent's configuration.

- A handler who can sign off over the limit starts a round on the Improvement page, and can stop one that is open. One round is open at a time, and it works to the settings as they stood when it started.
- The improver follows a recipe, which ends by improving the recipe. It proposes a change and a handler approves or rejects it, with a reason for a rejection that goes back to the improver. Nothing it proposes takes effect until approved.
- A judge, Jev, grades each explanation against the handler's reason, from F to A with a score from 0 to 3.
- An approved version is evaluated once on the evaluation set and twice on the held-out set. It is kept when it meets the objectives, at least one measure improves by more than its noise and none gets worse by more than its tolerance. Otherwise it is rolled back.
- A round has a spending limit that covers the improver's calls, its evaluations and the judge. The improver cannot change the objectives, the noise and tolerance, the limit, the models allowed, the approval limit, the evaluation sets or the judge.

## Sign-in

Handlers sign in with their email address and password through Supabase Auth, and there is no sign-up. The backend checks the handler on every action a handler takes. Only a handler who can sign off over the limit may start or stop a round, approve or reject a proposal, or change a setting.

## Not for now

- No real payments.
- No messages to customers.