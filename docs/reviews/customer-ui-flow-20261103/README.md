# Customer UI work flow, 2026-11-03

The full work path driven through the live customer app by the owner account: sign in, start an agent task, read
the approval gate, approve it, and see the decision in Activity. This closes the part of Phase D that the API
checks could not prove: the screens and the provider session working together in a browser.

## Result

Playwright Chromium against `https://app.officeinference.com`, signed in as `anthony@officeinference.com`.

| Step | Result |
|---|---|
| Cognito login and app shell | pass, "All systems ready" and 7 tasks |
| Do: open "Prepare a supplier invoice" | pass |
| Task started with a supplier invoice | pass |
| Review: approval gate with the drafted action | pass, `review-gate.png` |
| Approve the gate | pass, "Approved. The job has been resumed" |
| Activity: decision and actor | pass, `anthony@officeinference.com` visible |
| Console errors | zero |

Screenshots: `review-gate.png`, `after-approval.png`, `activity.png`. Raw result: `flow-result.json`.

The first attempt used the research task and the model finished without calling the send tool, so no gate was
raised. That is the agent deciding the task did not need an approval, not a failure. The invoice task is
deterministic about its gate, which is why it was used for this check.

## State

- The live edge and API were serving the whole time.
- The five verification probes were re-run after the edge went live: all five pass.
- No test account or data was added for this flow; the owner account is the real one.

## What remains

Phase E: company accounts, documents, email identity and support path.
