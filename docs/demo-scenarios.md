# Demo scenarios

The flagship demo has to show, in about ninety seconds, the things a buyer cannot get from a chat
product: documents that stay put, reasoning with citations, a deadline, an action, an approval,
and a receipt.

## Flagship: the denial appeal agent

**Client type:** a dental or medical practice. **Where it runs:** office build, or hybrid with the
frontier lane on for the drafting step.

**The problem it solves.** Insurance claims get denied, and the appeal window is short. The front
desk has to find the explanation of benefits, pull the clinical note that justifies the procedure,
find the payer's own medical policy, write a letter that quotes all three, and file it before the
deadline. Practices leave real money on the table because nobody has the hour it takes.

**The storyboard.**

1. **Intake.** The denial lands in a watched folder or an inbox. The system reads the explanation
   of benefits, extracts the claim number, the patient, the procedure code, the denial reason, and
   the appeal deadline.
2. **Gather.** The agent pulls the clinical note from the practice management system, the
   procedure documentation from the chart, and the payer's policy on that code from the local
   policy library.
3. **Reason.** It compares the denial reason against the clinical documentation and identifies the
   specific paragraph that answers it. If the note does not support the appeal, it says so instead
   of writing a letter that will lose.
4. **Draft.** It writes the appeal letter with three citations: the policy clause, the clinical
   note, and the claim detail. Every sentence that asserts a fact points at its source.
5. **Approve.** The letter appears in the approval queue with the deadline and the citations
   visible. A person reads it and approves. The approval is recorded with their name.
6. **File.** The agent files it through the payer portal with browser assistance, or produces a
   print-and-fax packet when the portal is hostile. Either way, the submission is logged.
7. **Track.** The deadline and expected response date go on a calendar, and the system follows up
   if nothing comes back.

**What the viewer sees:** a denial letter going in one end, and a cited appeal with a deadline and
an approval trail coming out the other. Then the audit screen showing every step, every document
read, every model call, and the approval.

**What it exercises:** document ingestion and retrieval, multi-document reasoning, a long agentic
chain with a checkpoint, the approval gate, the audit sink, and connector work.

**Why it sells:** denials are money, and the appeal window is unforgiving. This is the rare
automation whose value is arithmetic, not convenience.

## Alternate: the quote agent for a machine shop

An RFQ arrives by email with drawings attached. The agent reads the drawings and the quantities,
pulls comparable historical jobs and their actual margins, drafts a quote, flags the line items
where the shop has historically lost money, and routes it for approval. The demo lands well in
manufacturing because everyone in that room has lost a bid by guessing.

## Both demos rest on the same plumbing

Ingestion, retrieval with citations, the agent loop with checkpoints, the approval queue, the
audit sink, and connectors. Build one and the other is a configuration.
