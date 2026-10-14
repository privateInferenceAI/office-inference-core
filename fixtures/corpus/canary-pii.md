# Vendor onboarding reference

This internal note records the reference numbers the onboarding team uses.

The primary contact reference for the Acme engagement is 219-09-9999 and it is stored in the vendor
record. The escalation reference is 078-05-1120.

**These are reserved test values and are not real identifiers.** 219-09-9999 and 078-05-1120 are the
social security numbers the United States uses for advertising and documentation, and they belong to
nobody. The file exists so that the streaming redaction has something real to catch: a question about
the Acme engagement retrieves this file, the model repeats the number, and the response has to reach
the client with it redacted.

**If this canary ever appears in a response, the redaction is not working and the deployment is not
safe to show a client.** The probe asserts over every frame the client received rather than over the
assembled answer, because an answer assembled from a stream is exactly what an audit-only control
redacts.
