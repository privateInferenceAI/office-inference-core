# Acceptance on the scratch deployment, 2026-10-23

**Status:** accepted. All four probes passed, 24 of 24 rights.

## Target

- Instance: i-096bebd46308d1673, m7i.4xlarge, 52.204.202.66, account 878451098165.
- Deployment root: /opt/oi-restore.
- This is a separate scratch deployment restored from the 2026-10-23 backup, not the live box.
- It is not a blank install. The blank-install path was accepted on 2026-10-22 in
  docs/reviews/fresh-install-2026-10-22/.

## Probes

| Probe | Result |
|---|---|
| interface_probe.py | PASSED: 8 rights |
| activity_probe.py | PASSED: 8 rights |
| access_probe.py | PASSED: 4 rights |
| stream_probe.py | PASSED: 4 rights |

All four exited 0.

The access probe recorded a real refusal in the chain, and the stream probe reported TTFT 487ms of 1.76s with
76 frames and the phone redaction.

## What this proves

A deployment built from the current state, on a separate instance, passes the same acceptance probes used for the
fresh install. The live box was not the target.

## What it does not prove

This was not a from-empty install. The blank-install acceptance remains the 2026-10-22 run. The current code
changes after that run were exercised by the live suite and by the probes here, but a full current-code blank
install is still a separate exercise.

Transcripts: interface_probe.py.txt, activity_probe.py.txt, access_probe.py.txt, stream_probe.py.txt.
