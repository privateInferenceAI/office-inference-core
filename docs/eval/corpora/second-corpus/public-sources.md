# Second public corpus

40 real, public documents for testing document search and question answering. This is the
public half of the second evaluation corpus. It does not reuse an issuer from the first
corpus: no Apple filing, and no IRS publication.

Total size is 86.6 MB across 40 files. The largest file is 16.1 MB. Every file is text-bearing
HTML or PDF, not a scan.

## What is here

| Category | Files | What the documents contain |
|---|---|---|
| Annual reports (10-K) | 5 | Audited financial statements, MD&A, risk factors, segment and store tables |
| Quarterly reports (10-Q) | 5 | Condensed financial statements, MD&A, segment tables |
| Current reports (8-K) | 5 | Short current-report prose describing a credit agreement, leadership change, or similar event |
| Proxy statements (DEF 14A) | 5 | Governance, pay-versus-performance, executive compensation and equity tables |
| Exhibit 10 contracts | 10 | Credit agreements, equity plans, severance plan, stockholders agreement, receivables financing, deferred compensation |
| US government PDFs | 10 | 2 GAO reports, 4 Federal Register rules, 2 OSHA publications, 1 FDA guidance, 1 NIST framework |

### Issuers

14 US public companies across seven industries. Each issuer directory holds every document
for that company.

| Issuer | Slug | Industry | Files |
|---|---|---|---|
| Target Corporation | `target-corp` | retail | 3 |
| The Kroger Co. | `kroger-co` | retail | 3 |
| Caterpillar Inc. | `caterpillar-inc` | manufacturing | 2 |
| Deere & Company | `deere-co` | manufacturing | 2 |
| HCA Healthcare, Inc. | `hca-healthcare-inc` | healthcare services | 3 |
| DaVita Inc. | `davita-inc` | healthcare services | 2 |
| Union Pacific Corporation | `union-pacific-corp` | logistics | 1 |
| C.H. Robinson Worldwide, Inc. | `c-h-robinson-worldwide-inc` | logistics | 3 |
| Intuit Inc. | `intuit-inc` | software | 2 |
| Autodesk, Inc. | `autodesk-inc` | software | 2 |
| EOG Resources, Inc. | `eog-resources-inc` | energy | 1 |
| Phillips 66 | `phillips-66` | energy | 2 |
| Duke Energy Corporation | `duke-energy-corp` | utilities | 2 |
| Xcel Energy Inc. | `xcel-energy-inc` | utilities | 2 |

### Government documents

| Agency | Document | Pages |
|---|---|---|
| GAO | GAO-25-107283, Defense Industrial Base: Actions Needed to Address Risks Posed by Dependence on Foreign Suppliers | 38 |
| GAO | GAO-25-106820, Small Business Contracting: NNSA Needs Increased Contractor Oversight to Reduce Reporting Errors | 52 |
| EPA | Federal Register 2026-21051, Significant New Use Rules on Certain Chemical Substances (26-1) | 11 |
| DOL | Federal Register 2026-20447, Transparency in Coverage | 120 |
| FTC | Federal Register 2026-02866, Revision of the Negative Option Rule, Withdrawal of the CARS Rule, Removal of the Non-Compete Rule | 4 |
| OSHA | OSHA 3071, Job Hazard Analysis | 51 |
| OSHA | OSHA 2254, Training Requirements in OSHA Standards | 270 |
| FDA | eCopy Program for Medical Device Submissions: Guidance for Industry and FDA Staff | 30 |
| NIST | The NIST Cybersecurity Framework (CSF) 2.0, NIST.CSWP.29 | 32 |
| USDA | Federal Register 2026-20792, Raisins Produced From Grapes Grown in California; Order Amending Marketing Order No. 989 | 6 |

## Layout

```
public/
  company/<issuer-slug>/<form>_<yyyy-mm-dd>_<description>.<ext>
  government/<agency>/<title-slug>.pdf
  provenance.csv
  README.md
```

File names use the EDGAR filing date, not the fiscal period end. An Exhibit 10 file is named
for the exhibit, for example `EX-10_2026-06-15_credit-agreement.htm`, and its provenance note
names the form it was filed under.

## How it was collected

SEC EDGAR, no API key:

1. `data.sec.gov/submissions/CIK##########.json` gave the filing history for each issuer.
2. I took the most recent 10-K, 10-Q, and DEF 14A for each issuer that needed one.
3. For 8-K, I opened the archive index for the ten most recent 8-Ks, read the item numbers and
   primary-document sizes, and picked a substantive one. Preference went to Item 1.01, 2.03, or
   5.02 filings.
4. For Exhibit 10, I scanned the archive indexes of recent filings for names matching an
   Exhibit 10 pattern, excluded XBRL and consent exhibits, and took the largest qualifying
   contract between 20 KB and 6 MB.
5. Downloads used a real contact User-Agent, one request every 0.25 seconds, and retry with
   exponential backoff on 403 and 429.

Government documents:

1. GAO PDFs came from `files.gao.gov/assets/`. The report numbers came from public search
   results, then the PDFs were fetched directly.
2. Federal Register rules came from the `federalregister.gov` API, with PDFs on `govinfo.gov`.
3. OSHA, FDA, and NIST PDFs came from their public publication URLs. `osha.gov` rejects the
   contact User-Agent, so the two OSHA downloads used a standard browser User-Agent.

The fetch script is at `/tmp/second-corpus-work/fetch_corpus.py`. It is not part of the
corpus. Commands are `resolve`, `download`, `gov`, `manifest`, and `validate`.

## Licensing basis

SEC filings are public records. EDGAR distributes them at no cost, and the exhibits here are
the same files EDGAR serves. They are used for evaluation, with source URLs preserved in
`provenance.csv`. No paywalled source, aggregator, or subscription dataset was used.

US government works are public domain under 17 U.S.C. 105. That covers the GAO reports, the
Federal Register documents, the OSHA publications, the FDA guidance, and the NIST framework.
NIST publishes under the same government-work rule and states that its publications are not
subject to copyright in the United States.

## Exclusions

- No Apple document. Apple was in the first corpus.
- No IRS publication. The first corpus used IRS Publication 15, so I used other agencies.
- No paywalled or subscription source. No SEC filing aggregator.
- No document with a Social Security number or personal financial account. I scanned all 40
  files, including extracted PDF text, for SSN patterns and found none.
- No scanned or image-only PDF. Each PDF has an extractable text layer.
- No exhibit that is a subsidiary list, legal opinion, consent, or XBRL instance.
- Proxy statements contain named executive compensation and share ownership. SEC rules require
  that disclosure and it is public by design. It is not private personal data, so the proxies
  stay in. No home addresses, SSNs, or bank details are present.

## Sources that failed

These attempts failed, and the final corpus uses an equivalent source or a different document.
No category is short because of them.

- `www.gao.gov` and the HTML pages on `files.gao.gov` return 403 from this network. The PDFs
  at `files.gao.gov/assets/gao-XX-XXXXXX.pdf` work.
- `www.osha.gov` returns 403 for the contact User-Agent. A standard browser User-Agent works
  for the PDFs.
- `www.dol.gov` HTML pages return 403. The DOL rule was taken from the Federal Register PDF.
- `www.epa.gov` fails TLS chain validation from this machine. The EPA rule was taken from the
  Federal Register PDF.
- The SBA table of small business size standards link on `sba.gov` now returns 404 for the
  PDF behind it.
- USCIS Form I-9 PDFs return 403.
- The BLS Monthly Labor Review PDF returns 403.
- The FTC business guidance page returns 404, so FTC is represented by a Federal Register rule.

## Validation performed

- 40 files, all non-empty.
- Every HTML file starts with `<`. Every PDF starts with `%PDF`.
- SHA-256 is recorded for every file in `provenance.csv`, and no two files share a hash.
- Largest file is 16,080,282 bytes, under the 25 MB cap.
- Total is 86,603,195 bytes, under the 200 MB cap.
- No SSN patterns in HTML text or extracted PDF text.

## Caveats

- 8-K primary documents are short by nature, roughly 5,000 to 10,000 characters each. They are
  the current report itself, not the exhibits filed with it.
- Union Pacific and EOG Resources contribute one document each. Their recent filings had no
  Exhibit 10 that met the size and format bar, and I did not lower the bar to fill the row.
- Exhibit 10 content is a mix: two credit agreements and one amendment, one equity incentive
  plan, two equity award documents, one severance plan, one stockholders agreement, one
  receivables financing amendment, and one deferred compensation plan.
- Filing dates are EDGAR dates. The fiscal periods the reports cover are earlier.
