# Parity suite

The suite exists to answer one question: **does this configuration behave the way we promised?**

It runs against any deployment profile and any model assignment, so a model swap, a hardware
change, or a new compliance profile is a measurement instead of an opinion.

## What it covers

1. **Document chat.** A fixed set of questions against a fixed document set, scored on whether the
   right passage is cited.
2. **Access control.** A user without the right role must not be able to reach a document, and the
   attempt must appear in the audit log.
3. **Lane policy.** With the frontier lane off, an outbound call must fail closed. With it on, only
   the permitted data classes may leave, and every call must be recorded with its payload hash.
4. **Workflow.** A multi-step workflow with an approval gate completes, and a denied gate stops it.
5. **Recovery.** Restore from backup onto a clean box, then run checks 1 through 4 again.

Checks 1 and 4 are the ones that catch a bad model swap. Check 3 is the one that keeps a promise
to a client. Check 5 is the one that keeps you out of trouble at 9pm.
