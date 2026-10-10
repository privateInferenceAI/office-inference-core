# Front end design

The client app is the product's face, and it is the one place a cloud competitor cannot simply
outspend us on features. The goal is not delight. It is **clarity a non-technical person trusts**.

## Principles

1. **Show your work.** Every answer carries its sources. Every action shows what it did and can be
   undone where possible.
2. **Few verbs, big targets.** Ask, Do, Review. Settings exist but are not a verb.
3. **Nothing happens without a receipt.** The Activity screen is one click from anywhere.
4. **Say where data went.** A permanent, honest indicator: stayed here, or left the building.
5. **Plain words.** No jargon, no sparkle icons, no "AI-powered."
6. **The app is a function of the manifest.** A client without the agentic pack never sees Do.

## Screens

### Ask

The default screen. A question box, and under it, the answer with citations.

- Each citation is a chip: document name, page, and a snippet on hover. Clicking opens the source
  at that page.
- If the answer is not in the documents, the app says so plainly and offers to search wider, which
  is a visible, deliberate act rather than a silent guess.
- A small line under each answer: "Answered from 4 documents on this machine, 2.1 seconds." When
  the frontier lane was used: "Answered using a frontier model. What was sent: instructions only."

### Do

Visible only when the client has a task-capable pack.

- A short list of what this system can do for them, in their words: "Draft the recall texts."
  "Chase the overdue invoices." "Prepare the morning brief."
- Each entry shows what it will touch: which systems, which folders, whether it needs approval.
- Starting a task switches to a live view: steps as they happen, with a plain-language line each,
  and a pause button.

### Review

The approval queue, and the screen that makes autonomy tolerable.

- Each item: what the system wants to do, why, the exact content (the email text, the journal
  entry, the file to delete), and what happens if it is approved.
- Approve, Deny, or Ask for changes. A denial records a reason, which the system uses next time.
- Bulk approval for batches that are genuinely identical, with the count shown.

### Activity

The audit record, in plain language.

- Filter by person, by system, by date, by whether it left the building.
- Each row expands to show: the request, the model, the tool calls, the documents read, the
  approval if any, and the payload hash for anything that left.
- An export button that produces the signed record for an examiner.

### Settings

Small on purpose. Who has access, which packs are on, the frontier lane switch, the backup
status, the support tunnel state, and the update channel. Everything else is our job, not theirs.

## The two indicators

Both are permanent, small, and never decorative.

| Indicator | States |
|---|---|
| **Data lane** | "Stayed on this machine" · "Left the building, approved by Dana at 2:14pm" |
| **System state** | "Local models ready" · "Indexing 12 new documents" · "Frontier lane off" |

## What it talks to

Only the assistant API. The app holds no model keys, no database credentials, and no knowledge of
which model answered. That is what lets the model tier, the retrieval stack, and the runtime change
underneath it without a UI rewrite.

## Build or adopt

**Adopt for the lab, build for the client.**

| Surface | Decision | Why |
|---|---|---|
| Client app | **Build**, as a small TypeScript app (React or Next.js) | Every differentiator lives here: citations first, the approval queue, the lane indicator, three verbs. Theming someone else's chat app fights its paradigm |
| Lab console | **Adopt LibreChat** | Debugging, prompt work, and support need a power tool. It is not shipped to clients, so its interface does not matter |

Both talk to the same assistant API. That removes the false choice between building and adopting,
and it keeps our client-facing surface small enough to be excellent.

## Accessibility and the office reality

- Works on a 1366×768 laptop and on a tablet at the front desk.
- Keyboard-first, because front desk staff type faster than they click.
- Large default text. Many users are over 50, and this is a workplace tool, not a toy.
- No dark patterns, no upsells, no notifications that create anxiety.


