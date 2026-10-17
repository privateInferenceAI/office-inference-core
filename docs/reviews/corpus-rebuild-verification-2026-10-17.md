# Corpus rebuild and verification, 2026-10-17

What the v3 corpus load produced, what was verified against it, and what is still outstanding.

## State at the end of the load pass

| | |
|---|---|
| documents on disk | 544 (341 in group-01/02, 203 in group-03) |
| documents indexed | 478 |
| chunks written | 70,749, matching the Qdrant point count exactly |
| executive-root documents indexed | 169 |
| documents still retrying | 66, all of them the largest files, cycling at 15-minute intervals |
| dead-lettered | 10, each after two failures |

**The invariant holds across every written chunk.** No point in the collection measures over 512 tokens: the
collection-wide range query for `token_count > 512` returns zero. The 756 KB prose run that started this is 789
chunks with a 512 maximum. Tables that did not fit are bounded row groups: 968 of 1,000 sampled table chunks carry
`row_start`, `row_end` and `table_rows`, and 205 distinct tables were split that way. The contract records
`chunker-v3`, `max_input_length 8192`, `auto_truncate false`.

**Verified on the rebuilt corpus:**

- the boundary gate check passes: a forbidden class is refused on `/ask` and `/ask/stream`, and the permitted path
  still answers through the gateway;
- the job probe holds eight rights, the activity probe holds eight;
- staff asking the CEO base salary is denied and receives only company passages, while an executive session gets
  the compensation memorandum first and the 418,750 figure;
- staff asking a company question receives company passages;
- four data questions from the new question set retrieve their expected documents, and the arithmetic questions
  retrieve both operand documents once anchored on the DP03 table name.

## The two failures that were not corpus content

**The last 66 documents cycled between 429 and re-parse.** They are the largest files, and at two documents in
flight each sending a near-cap embedding request the service sheds one. The retries expired, the document was
recorded transiently failed, and the next cycle paid its parse cost again. This is `RET-INGEST-002` in production.
The load now runs as a single writer with a 7,000-token request budget (PR #154); since then there has been one
shed line rather than dozens, and throughput rose to about seven chunks a second.

**The dead letters are 10 files the parser or the transport refuses:** seven `.xls`, two `.csv`, one `.txt`. The
reasons are parser task failures on six, `HTTP 400 Bad Request` on four, and `HTTP 413 Payload Too Large` on the
one 3.3 MB text file. `HTTP 413` on a text file is the one worth a look: a text file that size should be parseable,
so either the parser service has an upload limit worth documenting or the client needs a smaller-file path. This is
recorded as a finding for the C6 work rather than fixed here.

## Not done

- The 66 large documents are still retrying in the background; the corpus is 88 percent of documents and every
  document the acceptance question set relies on.
- The 10 dead letters are waiting on a file change or a decision about the 413/400 cases.
- The two acceptance panels are planned and their go-ahead is with the owner.
