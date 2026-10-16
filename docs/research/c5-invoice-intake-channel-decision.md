# C5 invoice intake: which channel to build first

Research report, 2026-10-16. Prepared for the C5 phase decision ("invoice intake with an
approval gate"). Every factual claim below carries a URL. Claims I could not verify are
marked **UNVERIFIED** and gaps are named rather than filled.

---

## 1. Headline recommendation

**Build email first. Build the watched folder second. Do not build Microsoft Graph, the
Gmail API, or an accounting-system API as the C5 intake channel.**

Build email first *as an address we own*, not as a mailbox we log into. The connector is:

> A dedicated inbound address (for example `invoices@client.officeinference.com`) that the
> system operates itself. The client adds a forwarding rule from their existing AP mailbox,
> or gives the address to suppliers. The system owns the mailbox, so the system needs no
> credential to anyone else's mail system.

Three things make this the highest-coverage, lowest-liability first channel:

1. **It is the largest automatable arrival channel.** Email is 40% of invoice volume in the
   only rigorous public survey I found; paper mail is 47% and cannot be automated at all, so
   email is the single largest automatable channel ([Deluxe/Barlow 2023](#src-deluxe)).
2. **Compliance-grade products already chose it first.** Expensify, Ramp, Brex and BILL all
   built email intake, and it is the one channel that requires no credential grant from the
   client's mail system ([Expensify](#src-expensify-free), [Ramp](#src-ramp-receipts),
   [Brex](#src-brex-billpay), [BILL](#src-expensify-bill)).
3. **It stores no credential with reach into anyone else's system.** That is the property
   that matters most for a hash-chained accountable log, and it is the property the other
   channels cannot match. Section 5 works through the alternatives.

---

## 2. Where invoices actually arrive

### 2.1 The one rigorous dataset I found

The Deluxe State of AP & AR Study (fielded December 2023, published January 2024) asked
businesses directly: *"What percentage of the monthly invoices your organization receives
arrive by the following methods?"* Answer, n=793:

| Channel | Share of monthly invoices |
|---|---|
| Paper invoices mailed to us | **47%** |
| e-Invoices received via email | **40%** |
| e-Invoice downloaded from biller portal | 7% |
| EDI invoice | 3% |
| Invoices received through QuickBooks or other accounting software platforms | **2%** |
| Other | 1% |

Source: [Deluxe State of AP & AR Study 2023, Barlow Research, p.9](#src-deluxe). The PDF is
at [deluxe.com](https://www.deluxe.com/content/dam/deluxe/us/en/home/resources/payments/dpx/Deluxe-State-of-AP%20-AR-Study-Barlow-Research-2024.pdf).
The study's own executive summary states "Almost all invoices are received via mail or email."

### 2.2 Read the sample before you rely on it

This matters and it cuts against us. The Deluxe/Barlow sample is **793 businesses with
5M to 500M USD in annual sales**, weighted to a population of 270,722 firms; max error
factor ±3.48% at 95%. SIC industry responses: services 193, wholesale 165, manufacturing
163, construction 90, retail 83, transportation 57, finance 32, agriculture 6, mining 4.
Fielding 4–12 December 2023 ([methodology, p.4](#src-deluxe)).

So:

- The sample is **middle market**, not 5–50-employee small business. Its smallest band is
  5M–10M USD revenue (n=178). That is bigger than most of Office Inference's target clients.
- It is **industry-weighted toward services, wholesale and manufacturing**, with very few
  finance and almost no healthcare or legal. It does **not** support an industry-level claim
  about medical practices, law firms or accounting firms.
- The report gives **no per-industry breakdown of invoice receipt channel**. I checked the
  extracted text of the full deck; the "HOW INVOICES ARE RECEIVED" chart appears once, at
  total-market level only.

### 2.3 What does not exist (state this plainly)

**I could not find any public dataset on invoice arrival channel for businesses with 5–50
employees, and none broken out by industry (medical, legal, accounting, trades).** I searched
for and checked:

- Cleveland Fed / Small Business Credit Survey 2024 Report on Payments. It is a strong
  primary dataset (n=4,853 employer firms, optional payments module on the 2023 SBCS) but it
  surveys *payment acceptance and payment terms*, not *invoice receipt channel*. Its relevant
  finding is that 28% of employer firms use invoice / net-30 terms and 38% are paid in full at
  the time of service. That is useful context for who even receives invoices, not for how.
  [SBCS 2024 Report on Payments](https://www.fedsmallbusiness.org/-/media/project/clevelandfedtenant/fsbsite/reports/2024/2024-report-on-payments_sbcs.pdf)
- APQC invoice benchmarks. They measure *line items invoiced electronically* and are behind
  a membership wall for the useful cuts.
  [APQC measure page](https://www.apqc.org/what-we-do/benchmarking/open-standards-benchmarking/measures/percentage-invoice-line-items-invoiced)
- Multiple AP-automation vendor reports (SAP Concur, MineralTree, Vic.ai, Bottomline/Ardent
  "State of ePayables"). The Concur and MineralTree PDFs either would not download or contain
  no invoice-arrival-channel breakdown. Vendor reports in this category market automation and
  are not a neutral source for arrival shares.

The most defensible public comparisons I found for adjacent facts:

- **AFP 2025 Payments Fraud and Control Survey** (n = corporate practitioners, surveyed
  January 2025, data for 2024): 79% of organizations experienced actual or attempted payments
  fraud; **business email compromise was the number one vector at 63%**; **invoice fraud rose
  to 24% from 14%**; payment method most targeted by BEC was wire transfer (63%, up from 39%);
  checks remained the most fraud-prone payment method (63%). Note the AFP sample skews to
  large corporates, so treat these as directional for small business.
  [Full report PDF](https://www.truist.com/content/dam/truist-bank/us/en/documents/info/cci/2025-afp-payments-fraud-control-survey-report-key-highlights.pdf)
- **FBI IC3 2024 Annual Report**: BEC complaints 21,442 in 2024 (21,489 in 2023, 21,832 in
  2022); BEC losses **2,770,151,146 USD in 2024**, down from 2,946,830,270 in 2023. BEC is
  the second-largest loss category after investment fraud.
  [IC3 2024 Annual Report PDF](https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf)

### 2.4 The conclusion the data does support

Two channels carry 87% of invoice volume: paper mail and email. Paper cannot be automated
remotely. It has to be scanned by a human and lands in one of the electronic channels
eventually, which is why the scanner and shared-folder paths matter as *second-order* entry
points. Accounting software, the channel that looks most attractive because it has a clean
API, is **2% of invoice volume**. That number is the single most important finding in this
report for the C5 decision.

**Evidence gap to close during C5 rather than guess at now:** instrument the first ten
deployments and record the channel each invoice actually arrived by. That gives us our own
5–50-employee dataset, which nobody has published. See section 7.

---

## 3. Comparison table

| | Email — owned address (forward/route in) | Email — IMAP poll of client mailbox | Email — Microsoft Graph | Email — Gmail API | Watched folder on SMB/NFS share | Scanner direct-to-folder | Web upload | Accounting system API |
|---|---|---|---|---|---|---|---|---|
| **What the client must grant** | A forwarding rule, or the address given to suppliers | Mailbox password or OAuth token for the AP mailbox | Azure app registration + admin consent | GCP project + OAuth consent | Share credentials, or a service account | Share credentials typed into the printer | A user login | OAuth app + accounting subscription |
| **Credential the product stores** | **None** | IMAP password or refresh token, in plaintext unless we encrypt it | Client secret + refresh token | Client secret + refresh token | SMB/NFS credential file | **None** (scanner writes to the share) | None (per-user login) | Client secret + refresh token per client |
| **Read-only possible?** | N/A — nothing to scope | Yes, but IMAP has no read-only scope: full mailbox access | Yes: `Mail.ReadBasic` (no body) is weaker, but `Mail.Read` is required to read attachments | Yes: `gmail.readonly` — but it is a **restricted** scope | Yes, share ACL and `ro` mount option | Yes, share ACL | Yes — role-based | Partly; reads are the metered calls |
| **Maintenance burden** | Near zero | OAuth tokens expire hourly; password rotation | Consent renewal, subscription expiry | Token refresh, annual re-verification | Mount recovery on reboot, credential rotation | Same as folder, plus printer admin | Near zero | Token refresh, per-tenant app |
| **Injection surface** | Anyone who knows the address, unless allowlisted | Same, plus the whole mailbox becomes an input | **Tenant-wide by default** unless scoped | Same | Anyone with write access to the share | The printer (weak credential store) | Only authenticated users | Only the accounting vendor |
| **Audit story** | Strong if we log envelope sender, message-id, recipient address, and create the mailbox ourselves | Weak: we are reading a mailbox we do not control | Medium: Purview/Graph audit exists but is the client's, not ours | Medium, same | Strong: file path, mtime, size, we can hash on arrival | Strong, same | **Strongest**: authenticated identity, TLS, request ID | Strong but only for already-keyed records |
| **Evidence someone built this first** | Expensify, Ramp, Brex, BILL all ship it | Paperless-ngx (added v0.0.5), Docspell | — | — | Paperless-ngx (original channel, v0.0.1) | Paperless-ngx documents it as the usual scanner path | Universal baseline | — |
| **Cost / gate to us** | Zero | Zero | Zero (Graph is free with M365) | **CASA security assessment if we store restricted-scope data on servers** | Zero | Zero | Zero | **Now metered — see 4.4** |

---

## 4. What each channel takes to read programmatically

### 4.1 Email: plain IMAP with a password is effectively dead with the two big providers

This is the single most consequential technical fact for the email decision.

**Microsoft.** Basic authentication is disabled in all tenants and cannot be re-enabled by
anyone, including Microsoft support. The affected protocols include POP, IMAP, Exchange Web
Services and Exchange ActiveSync. Microsoft's own note: "The deprecation of basic
authentication also prevents the use of app passwords with apps that don't support two-step
verification." The only path for IMAP/POP/SMTP AUTH now is OAuth 2.0.
[Microsoft Learn: Deprecation of Basic authentication in Exchange Online](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online)

Separately, SMTP AUTH (sending, which is what scanners use) is on its own timeline. Microsoft
announced in April 2024 that it will permanently remove Basic authentication for SMTP AUTH;
the deprecated timeline is now: **end of December 2026, SMTP AUTH Basic Authentication
disabled by default for existing tenants** (admins can still re-enable it for now).
[Microsoft Tech Community: Exchange Online to retire Basic auth for Client Submission](https://techcommunity.microsoft.com/blog/exchange/exchange-online-to-retire-basic-auth-for-client-submission-smtp-auth/4114750)

**Google.** Less Secure Apps (password-only sign-in for third-party apps over IMAP, POP,
CalDAV, CardDAV, SMTP) was turned off for Workspace accounts. The rollout slipped several
times; the final date was **1 May 2025**. After that, IMAP/POP require OAuth.
[Google Workspace Updates, 29 Sep 2023 with 2025 updates](https://workspaceupdates.googleblog.com/2023/09/winding-down-google-sync-and-less-secure-apps-support.html),
[Google Account Help: Less secure apps](https://support.google.com/accounts/answer/6010255)

**What this means concretely.** The old integration ("ask the client for the AP mailbox
password, store it, poll IMAP") is now a dead end for any client on Microsoft 365 or Google
Workspace, which is most of them. Apple App-Specific Passwords still work for consumer
iCloud-style and standard IMAP hosts, and for a genuinely self-hosted mail server (cPanel,
Zoho, Fastmail, a local mail server) plain IMAP with a credential still works. But you cannot
build the product assuming that.

**And a cautionary tale from the field.** Docspell's documentation is explicit that it stores
mail passwords **in plaintext in its database**, and that its OAuth support does not refresh
tokens. Their documented workaround is a cron job that regenerates the Gmail access token and
PUTs it back into the IMAP settings over the REST API. That is a self-hosted product's
honest admission of the maintenance cost.

> "Note: Passwords to your e-mail accounts are stored in plain-text in docspell's database."
> "[Access token expiration is] The problem is that the access token expires. Docspell doesn't
> support updating the access token. It could be worked around by setting up a cron-job…"

Source: [Docspell E-Mail Settings docs](https://docspell.org/docs/webapp/emailsettings/).
Their scan-mailbox feature requires the user to supply IMAP settings and a folder list, with
"Received Since Hours" used because "there are many mails in your inbox and importing them all
at once is not feasible." [Docspell Scan Mailboxes](https://docspell.org/docs/webapp/scanmailbox/)

Paperless-ngx handles this more maturely: it supports OAuth2 for Gmail and Outlook accounts,
states that "refreshing tokens will be handled automatically," and routes the
setup through the *operator's own* developer app: "you will need to create a 'developer' app
with the respective provider and obtain the client ID and client secret."
[Paperless-ngx usage docs, OAuth Email Setup](https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/usage.md)

That last sentence is the key architectural escape hatch for Office Inference, and it applies
to both Microsoft and Google: **if the client registers the OAuth application in their own
tenant or their own Google Cloud project, the vendor verification and CASA requirements that
apply to a multi-tenant commercial app do not apply to us.** It does not remove the token
refreshing, the consent grant, or the shared-mailbox complexity.

### 4.2 Email: Microsoft Graph specifics

| Item | Detail |
|---|---|
| Delegated vs application | Delegated permissions act as a signed-in user. Application permissions act with no user and are tenant-wide by default. |
| The compliance problem | `Mail.Read` as an **application** permission lets the app read **every mailbox in the tenant**. Microsoft's own community answers state this directly: "You can use application permissions to send or read messages from a shared mailbox, but not just the shared mailbox, but the mailbox of all [users]." [Microsoft Learn Q&A](https://learn.microsoft.com/en-us/answers/questions/2149155/permissions-to-access-shared-mailbox-to-read-and-s) |
| The fix | **RBAC for Applications in Exchange Online** (it replaced Application Access Policies). An admin creates a resource scope (a set of mailboxes) and a role assignment binding the app's service principal to that scope. Requires Organization Management role group or Exchange Administrator role. [Microsoft Learn: RBAC for Applications in Exchange Online](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access) |
| What that means for us | Onboarding a client becomes: an Exchange admin creates an app registration, grants consent, creates a management scope containing *one mailbox*, and creates a role assignment. Every step is a PowerShell task the client's IT person may never have done. |
| Cost | Graph API access itself is free with the M365 subscription. **UNVERIFIED** — I did not find a Microsoft page stating this explicitly for mail reads; no per-call pricing page exists. |
| Licensing | Reading mail via Graph requires a licensed mailbox. **UNVERIFIED** for the specific SKU floor (Business Basic vs Standard). |

For a 5–50-person business with no IT staff, "we need your Exchange admin to run four
PowerShell commands" is a real sales blocker, and it is the reason I rank Graph below the
owned-address approach even though Graph is technically the cleanest API.

### 4.3 Email: Gmail API specifics

`gmail.readonly` is a **restricted** scope. Google's documentation is unambiguous: "If you
store restricted scope data on servers (or transmit), then you must go through a security
assessment." Apps requesting restricted scopes must also complete **annual re-verification**.
Additionally, `https://mail.google.com/` should only be requested if the app needs to
permanently delete messages.

Sources: [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes),
[Google OAuth App Verification](https://support.google.com/cloud/answer/9110914),
[Verification requirements](https://support.google.com/cloud/answer/13464321)

**Cost of the CASA assessment: I could not find an authoritative Google-published figure.**
The widely repeated numbers are 15,000–75,000 USD (from 2019 press coverage and a Nylas
security post) and 540–1,800 USD/year for CASA Tier 2 (from community and third-party
sources). These conflict by two orders of magnitude and none of them is a Google price list.
Treat any specific number as **UNVERIFIED**. The verifiable facts are: a security assessment is
required, it is annual, and it gates using restricted scopes for users outside your own
organization.

Again, the escape hatch: `gmail.readonly` under a client-owned GCP project is not the same
product as a verified multi-tenant app. Google's own verification requirements page
distinguishes apps by whether they are distributed to users outside the developer's
organization. I did not find a crisp statement exempting single-tenant installs, so mark the
exemption **UNVERIFIED and worth confirming with Google before relying on it.**

### 4.4 Accounting system APIs: usable, but now metered, and they are the wrong end of the pipe

Two things changed in 2025 that invalidate older comparisons.

**Intuit (QuickBooks Online).** The Intuit App Partner Program (launched 28 July 2025, full
variable fees from 1 November 2025) splits calls into **Core** (writes, free and unlimited)
and **CorePlus** (reads and queries, metered). Tiers:
Builder free with 500,000 CorePlus credits/month and **calls blocked past the limit**;
Silver 300 USD/month with 1M credits and 3.50 USD per 1,000 overage; Gold 1,700 USD/month;
Platinum 4,500 USD/month. Billing is at workspace level across all production apps.
[Apideck summary of the Intuit program](https://radar.apideck.com/blog/quickbooks-api-pricing-and-the-intuit-app-partner-program.md)
. Note this is a competitor's write-up, and the primary Intuit blog post was
Cloudflare-blocked when I tried it, so treat exact figures as needing confirmation against
Intuit's own portal.

The read/write asymmetry is the point: **reading invoices is the expensive direction, and
reading invoices is exactly what an invoice-processing product does.**

**Xero.** Retiring the 15% App Store revenue share, replacing it with five plans (Starter,
Core, Plus, Advanced, Enterprise) based on **connections and API data egress**, effective
**2 March 2026**. Rate limits are 1,000 calls/day/org on Starter and 5,000/day/org on the
other tiers. Plus/Advanced/Enterprise require an app certification and an **initial and annual
security assessment**. Connection caps are 5 (Starter), 50 (Core), 1,000 (Plus), 10,000
(Advanced), unlimited (Enterprise).
[Xero Developer Pricing](https://developer.xero.com/pricing)

Xero is also now **contractually hostile to this use case**:

> "data obtained from Xero's APIs may not be used to train AI/ML models"
> "apps must not use bots or browser extensions to undermine our security controls or
> simulate user actions"

Source: [Xero Developer Pricing / Terms update](https://developer.xero.com/pricing) and
[The Register, 4 Dec 2025](https://www.theregister.com/2025/12/04/xero_api_fees_change/).
One developer quoted in that article calculated his own annual bill at 17,340 USD, up from
free. **I am not asserting that Office Inference's use constitutes "training AI/ML models".
Local inference over a client's own documents is a different thing from training. But it is a
terms question a client's counsel will ask, and it is a reason not to make an accounting API
the first channel.

**And the decisive point, independent of cost:** an accounting-system API gives you invoices
that are *already in the accounting system*. Somebody already keyed them in, or the client
already pays for a capture service. It is a downstream consumer, not an arrival channel. The
one survey that measured this put accounting software at **2% of invoice volume**
([Deluxe/Barlow](#src-deluxe)). Building it first would mean building the smallest channel and
the most expensive one.

### 4.5 File shares: SMB and NFS from a container

**SMB/CIFS.** A Linux container mounts via `mount.cifs` (part of the `cifs-utils` suite),
which "only works in Linux, and the kernel must support the cifs filesystem." It mounts a CIFS
or SMB3 filesystem and talks to Windows servers, NAS appliances, Macs and Azure files.
Credentials go in a credentials file passed with `credentials=`; the man page has a dedicated
SECURITY and CONFIGURATION section covering this.
[mount.cifs(8) man page](https://man7.org/linux/man-pages/man8/mount.cifs.8.html)

Two failure modes the man page itself documents:

- **Cache coherency.** "the client must contend with the fact that activity on other clients
  or the server could change the contents or attributes of a file without the client being
  aware of it." CIFS only avoids client caching when it holds an oplock or lease. A drop
  folder being written by a scanner on the same network is exactly this scenario.
- **Inode numbers.** `serverino` can overflow 32-bit inode fields; the man page recommends
  `noserverino`, at the cost of not detecting hardlinks. This bites naive dedupe by inode.

**NFS** works similarly but UID/GID mapping and root squash are the recurring problems, and
`sec=krb5` requires a Kerberos ticket cache inside the container. I did not find a single
authoritative document covering the container case end to end; the honest statement is that
NFS in a container is doable and the failure modes are permission-mapping failures, which are
the hardest class of problem to debug remotely.

**SMB authentication is being forced toward Kerberos.** NTLM is being retired across Windows;
Microsoft has set out a path to disable it, with enforcement by default. This is live news as
of October 2026, the same week as this report.
[Help Net Security: Microsoft sets a path to switch off NTLM across Windows](https://www.helpnetsecurity.com/2026/02/02/microsoft-windows-ntlm-disabling/),
[Microsoft Q&A: NTLM retirement](https://learn.microsoft.com/en-in/answers/questions/5972148/microsoft-ntlm-retirement).
**UNVERIFIED for our purposes:** whether a Samba mount from a Linux container using a local
(non-domain) service account is affected by the NTLM disablement. This needs testing against a
current Windows Server build before we promise an SMB connector.

**Scanner-specific credential handling.** Xerox's own instructions for configuring scan-to-SMB
walk an administrator through creating a *local Windows user account* on the destination
machine for the scanner to authenticate as, then typing that user's credentials into the
device's embedded web server (or its control panel). The C315's shortcut UI offers
"Authentication: Use assigned username and password… Username / Password."
[Xerox KB0426910: How to Configure Scan to SMB](https://www.support.xerox.com/en-us/article/KB0426910),
[Xerox KB0330118: Configure Scan to Network (SMB)/FTP](https://www.support.xerox.com/en-us/article/en/2158652)

So on the scanner channel, *the printer holds a credential to your file server*. That
credential lives in a device with a poor patch record and a web UI. That is the attack surface
you inherit.

### 4.6 The watched-folder trap: inotify does not work on network filesystems

This is the most important mechanical finding for the second channel, and it is authoritative.

> "Inotify reports only events that a user-space program triggers through the filesystem API.
> As a result, **it does not catch remote events that occur on network filesystems.
> (Applications must fall back to polling the filesystem to catch such events.)**"

Source: [inotify(7) man page, Limitations and caveats](https://man7.org/linux/man-pages/man7/inotify.7.html)

The same page also notes that inotify "provides no information about the user or process that
triggered the inotify event," and cannot distinguish self-triggered events from others, and
that monitoring is not recursive without adding a watch per subdirectory. The file may already
be deleted or renamed by the time your event handler runs, which is precisely the
partially-written-file race.

**Consequence for the design:** any drop-folder connector must be a polling connector with a
settle check (size and mtime stable across two consecutive scans), not an inotify connector.
That is a real engineering difference and it should be in the C5 estimate. It also means the
"A watched folder" bullet in the phase plan is more work than it looks, and it is why I rank it
second rather than first.

### 4.7 Scanners: where the output actually goes

Support documentation shows multifunction devices support at least three destinations:

- **SMB or FTP network folder.** Xerox documents both, with the shortcut configured as
  "Network Folder (for SMB) or FTP" ([Xerox KB0330118](https://www.support.xerox.com/en-us/article/en/2158652)).
- **Scan to email.** Standard on business MFPs.
- **Vendor cloud.** Several vendors offer it; **I did not gather comparable per-vendor
  documentation and am not asserting a feature matrix.**

Two findings that matter:

1. **Scan-to-email is about to break on Microsoft 365.** Because SMTP AUTH Basic
   Authentication is being disabled by default at end of December 2026, and MFPs are classic
   Basic-auth clients, an entire installed base of scanners will need reconfiguration to
   modern auth (or a relay). This is already being flagged in the channel press (
   [Marco: Microsoft SMTP AUTH Deprecation To Cause MFP Scan-to-Email Disruption](https://www.marconet.com/blog/microsoft-smtp-auth-deprecation-to-cause-mfp-scan-to-email-disruption)
   ), and Ricoh published its own advisory:
   [Ricoh: Impact of Microsoft basic authentication deprecation](https://www.ricoh.com/info/2025/0526_1).
   **This is a tailwind for the shared-folder channel and a headwind for the scanner-to-email
   channel.**
2. **There is no public data on which scan destination businesses use most.** I searched for
   it and found only a 2017 Spiceworks thread where one respondent says "Scan to folder would
   be tied with scan to email for second place."
   [Spiceworks community thread](https://community.spiceworks.com/t/what-mfp-features-do-you-use-the-most/603596)
   That is an anecdote, not data. **State the gap rather than asserting a split.**

---

## 5. Security and audit implications

For a product with an append-only hash-chained audit log, the question is not only "what does
this channel cost to build" but "what does this channel let into the system, and what can we
prove about it afterwards."

### 5.1 The injection question

Every candidate channel is a way for an outside party to place content into a system that
reads documents with an LLM. Ranked by how much control we have over who can inject:

1. **Owned email address with a sender allowlist.** We create the mailbox, we control the
   address, and we can reject unknown senders before any file is read. The residual risk is
   spoofing of an allowlisted sender, which SPF/DKIM/DMARC validation on the inbound message
   addresses (we get the authentication results headers).
2. **Web upload.** Only authenticated users. Strongest identity story, but it is not how
   supplier invoices arrive, so it does not solve C5.
3. **Watched folder.** Anyone with write access to the share can inject. Usually that is more
   people than intended.
4. **IMAP poll of the client's mailbox.** Everything in the mailbox is an input. The blast
   radius is the client's entire AP correspondence.
5. **Microsoft Graph with `Mail.Read` application permission.** Tenant-wide by default. This
   is the worst case and it is the default configuration. Section 4.2 has the scoping fix.
6. **Accounting API.** Strongest on injection (only the vendor can write) and weakest on
   coverage.

The AFP data says this risk is not theoretical: 79% of organizations saw actual or attempted
payments fraud in 2024, invoice fraud nearly doubled to 24%, and spoofed email was the most
common BEC technique at 79% ([AFP 2025](#src-afp)). An invoice intake channel *is* a
fraud-attempt surface. The approval gate is the control that makes it survivable; the
allowlist and DMARC check are what keep the queue clean.

### 5.2 Credentials: what must be stored, and where

| Channel | Credential held by the product | Where it should live | Read-only achievable |
|---|---|---|---|
| Owned email address (forward/route in) | **None** | — | N/A |
| IMAP | Mailbox password or OAuth refresh token | OS keyring or an encrypted secret store inside the client's deployment; never in git, never in a config file with the app's read path | **No.** IMAP has no read-only scope. Reading is full-mailbox. |
| Microsoft Graph | Client secret + refresh token (or certificate) | Same, plus the client's Entra app registration | `Mail.Read` needed for attachments; `Mail.ReadBasic` gives metadata only |
| Gmail API | Client secret + refresh token | Same, plus the client's GCP project | `gmail.readonly` — genuinely read-only, but a restricted scope |
| SMB/NFS share | Share username/password in a credentials file (`chmod 600`), or a Kerberos keytab | Host path mounted read-only into the container | **Yes**, via share ACL and `ro` mount option |
| Scanner direct-to-folder | None held by us; the **printer** holds a credential to the share | — | Yes at the share, but the printer's credential is the weak link |
| Accounting API | Client secret + refresh token per client | Same | Partially; read scopes exist but reads are the metered ones |

The pattern is clear: the only channel with no stored credential at all is an address we own.

### 5.3 What has to be logged to satisfy an auditor

For the hash-chained log to be defensible, each intake event needs to chain an
unbroken record from "a document entered the system" to "a human approved it." Per channel,
the fields that are obtainable differ, and that difference is the argument:

- **Email (owned address):** the full RFC 5322 headers as received (From, envelope sender,
  Reply-To, Message-ID, Date, Received chain), the SPF/DKIM/DMARC authentication results, the
  recipient address it arrived at, the raw message hash, and each attachment's hash. This is a
  complete provenance record and we can produce it without asking anyone for access.
- **IMAP poll:** message UID plus UIDVALIDITY, folder name, and the fetch timestamp. Paperless-ngx
  keeps a "Processed Mails" table keyed on the server-supplied UID specifically to avoid
  double-processing, and warns that a server-side UID collision breaks it.
  [Paperless-ngx usage docs](https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/usage.md).
  The weakness for audit is structural: we are reading a mailbox we do not own, so we cannot
  prove the mailbox was not modified before we read it.
- **Watched folder:** file path, size, mtime, and a hash computed on first observation. Note
  that posix mtime comes from the writing client and is not trustworthy; the hash and our own
  observation timestamp are the auditable facts.
- **Graph / Gmail:** `internetMessageId`, `receivedDateTime`, and the provider's immutable
  message id. Both providers log access in their own audit systems (Purview / Gmail audit
  logs), which helps the client but is the client's evidence, not ours.

### 5.4 Attack surface summary

- **Open mail port on the client's network.** If we run our own SMTP listener, we have added
  a listening service. If instead we collect from a hosted address we control, the client's
  internal network gains no new listener. **This is a meaningful argument for the hosted-
  address-first design**, and it should be checked against the "data never leaves their
  infrastructure" constraint: routing supplier email through an address we host means message
  *headers* and transit pass through infrastructure that is ours, not theirs. If that is
  unacceptable for a regulated client, the same design works with an address on a mailbox the
  client already owns plus a forwarding rule. We then poll that one mailbox over IMAP/Graph
  and accept the credential cost. **Flag this for the C5 design review; it is the one place
  where the email-first recommendation interacts with the residency constraint.**
- **Printer-held share credential.** The scanner channel imports a poorly-patched device
  into the trusted path.
- **Tenant-wide mail read.** The Graph default. Avoidable, but only with Exchange admin work.
- **Mount inside a container.** Mounting SMB/NFS inside a container needs the mount performed
  on the host and bind-mounted in, or `CAP_SYS_ADMIN`, which weakens container isolation.
  Prefer host mount plus read-only bind mount.
- **Accounting API tokens.** A durable token with write scope in the client's books is the
  most dangerous credential in the table if scopes are not carefully restricted.

---

## 6. What comparable products did first

### 6.1 Commercial: all four built email intake

| Product | First/primary intake channel | Evidence |
|---|---|---|
| **Expensify** | Email forwarding to `receipts@expensify.com` — offered even on the **free** tier, no workspace required | "Anyone with an Expensify account can: … **Forward receipts** to receipts@expensify.com for automatic scanning." [Expensify Help: Free Features](https://help.expensify.com/articles/new-expensify/getting-started/Free-Features-in-Expensify) |
| **Ramp** | A receipt-forwarding address with **sender verification** and a six-character confirmation code; also supports forwarding from a centralized alias like `invoices@company.com` | "Ramp automatically matches the receipts you send to receipts@ramp.com… Enter the six-character code Ramp emails to that address to verify it." [Ramp Help Center](https://support.ramp.com/hc/en-us/articles/360047979213-How-to-automatically-forward-receipts-to-Ramp) |
| **Brex** | "**automatically forwarded to Brex via email**" to a **unique per-account address**; AI converts the invoice into a draft that a human reviews | "You can upload invoices directly or have them automatically forwarded to Brex via email… forward bills directly from your email to a unique Brex email address assigned to your account." [Brex premium implementation guide, Bill pay](https://www.brex.com/implementation/premium/11-bill-pay) |
| **BILL (Bill.com)** | A **per-account inbox email address**, minimum eight characters before `@bill.com`, given to vendors | "Enter the new Inbox email address, using a minimum of eight (8) characters in front of the @bill.com domain; … Provide this email address to [vendors]." [Amex Vendor Pay help: Set up, use, and maintain your Inbox](https://vendorpayhelp.bill.com/hc/en-us/articles/360006991712-Set-up-use-and-maintain-your-Inbox); independently corroborated by [Expensent's BILL intake guide](https://www.expensent.com/guides/email-receipts-to-bill), which notes the destination is account-specific and must be copied from inside the product |

Notes that sharpen this:

- **Every one of them relies on sender verification or a per-account address.** Ramp requires
  verifying the sending address with a code. Brex and BILL assign a unique address per
  account. This is the allowlist pattern, and it is the industry's own answer to the injection
  question in 5.1.
- **All four pair email or upload intake with a human review step.** Brex explicitly: "our
  system uses AI to translate the form into a draft that you can review and schedule to be
  paid." That is Office Inference's approval gate, independently arrived at.
- **Ramp's current marketing leads with drag-and-drop and OCR accuracy, not email.** "Drag,
  drop, done. Process thousands of invoices in seconds." Email forwarding is described as an
  *optional* automation. [Ramp Bill Pay](https://ramp.com/bill-pay). Read this as: email is the
  baseline everyone ships, and the differentiation has moved elsewhere. That is an argument for
  building it quickly rather than treating it as a differentiator.
- **Public evidence of *why* each chose email first: I found none.** No engineering blog post,
  launch note, or founder interview explaining the choice for any of the four. The evidence
  is the shape of the product, plus the ordering (email forwarding is available on Expensify's
  free tier, which suggests it predates the paid workspace features). **Do not attribute a
  stated rationale to them; there isn't one on the record.**

### 6.2 Open source: folder first, email second

This is the clearest natural experiment available, because both projects' changelogs and docs
record the order.

**Paperless-ngx** ([repo](https://github.com/paperless-ngx/paperless-ngx), created Feb 2022,
~46.8k stars as of 2026-10-16):

- **v0.0.1.** Initial release. The consumption directory is the *original* channel: "The
  primary method of getting documents into your database is by putting them in the consumption
  directory."
- **v0.0.5.** "Added IMAP mail support" (alongside "a crude means of HTTP POST for document
  imports").
- **Later versions** grew the mail consumer into the richer feature: "All new mail consumer
  with customizable filters, actions and multiple account support," then OAuth2 for Gmail and
  Outlook with automatic token refresh.
- **And the docs point scanners at the folder, not at email:** "if you're running this on a
  server and want your scanner to automatically push files to this directory, you'll need to
  setup some sort of service to accept the files from the scanner. Typically, you're looking at
  an FTP server like Proftpd or a Windows folder share with Samba."

Sources: [changelog.md](https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/changelog.md),
[usage.md](https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/usage.md)

**Docspell** ([repo](https://github.com/eikek/docspell), created Jul 2019, ~2.3k stars):
its "Documents → Docspell" page lists channels in this order: web upload (called "the
simplest way, but also the least flexible"), then **scanners/watch directories** ("it can
usually be configured to place scanned documents as image or PDF files on your NAS… run
`dsc watch` as a service"), then Android, then **poll e-mails**, then an SMTP gateway.
Its own repo description names the sources in the same order: "documents, resulting from
scanners, e-mails and other sources."
[Docspell Documents → Docspell](https://docspell.org/docs/feed/)

**Invoice Ninja** ([repo](https://github.com/invoiceninja/invoiceninja), created Nov 2013,
~10.3k stars, no license listed on the API, and is described as source-available): this is **not a
useful comparator for AP intake**, and I want to be explicit about that rather than pad the
section. It is an outbound invoicing product. You issue invoices to your clients. Its
documented import path is CSV file upload with column matching, plus named importers for
Freshbooks, Invoice2go, Invoicely, Wave and Zoho. It has an expenses module but I found no
inbound supplier-invoice capture channel in its documentation.
[Invoice Ninja Import and Export docs](https://invoiceninja.github.io/docs/advanced-topics/import-and-export)

**What the open-source ordering tells us.** The folder came first for Paperless-ngx and
Docspell because the founding users were self-hosters with a scanner and a NAS. The
constituency was people who already had the document on disk. The commercial products'
constituency was accountants and employees who receive invoices in email. **Office Inference's
constituency is the second one, not the first.** That is the strongest single argument for
inverting the open-source order.

---

## 7. Recommendation and build order

### Build first: an inbound email address the system owns

**What C5 ships.** A dedicated address per client. The client either (a) adds a forwarding rule
from their existing AP mailbox, or (b) hands the address to their recurring suppliers. The
system collects the mail, validates SPF/DKIM/DMARC and the sender allowlist, extracts
attachments, hashes the raw message and every attachment, writes the intake event into the
hash chain, runs the workflow, and blocks on human approval.

**Why, from the evidence above:**

1. **Largest automatable channel.** 40% of invoice volume, second only to paper mail at 47%,
   which cannot be automated ([Deluxe/Barlow](#src-deluxe)).
2. **Zero stored credentials.** The only channel in the table with none. For a product whose
   selling point is an accountable, hash-chained audit log, "we hold no credential that reaches
   into your other systems" is the strongest possible opening posture.
3. **Four commercial products independently chose it**, and each hardened it the same way
   (sender verification or per-account addressing). That design has been market-tested.
4. **It sidesteps the two things that would sink a first deployment:** Microsoft's Basic-auth
   shutdown, which killed the old IMAP-password pattern, and Graph's tenant-wide
   `Mail.Read` default, which is a compliance conversation we do not want to have in phase one.
5. **It creates the audit record itself.** We can log envelope sender, message-id, DMARC
   result, and content hash without depending on anyone else's audit system.

**The one open risk to resolve in design review.** If the address is hosted on infrastructure
we operate, message transit passes through us, which tensions with "client data never leaves
their infrastructure." The mitigation is to provision the address inside a mailbox the client
already owns and poll that single mailbox, accepting the credential and OAuth cost in exchange
for residency. **Decide this before C5 starts, because it changes whether we need the Graph or
IMAP connector in phase one after all.** If a client's residency requirement forces the second
option, use Graph with an Exchange RBAC management scope limited to that one mailbox. Never
unscoped `Mail.Read`.

### Build second: a watched folder on a network share

**Why second, not first:**

- It is the landing zone for everything that arrives on paper (47%), because a human or a
  scanner has to digitize it. It is how the paper share actually enters the system.
- It is the only channel in the table where a single connector covers scanner output, manual
  drag-and-drop, and existing client filing conventions.
- Read-only is genuinely achievable (share ACL plus `ro` mount), and the audit record is
  straightforward: path, our observation timestamp, content hash.
- It is also where the open-source products started, so the prior art is thick.

**What it costs, from the evidence:** it is not a directory watcher. Because inotify "does not
catch remote events that occur on network filesystems," it must be a polling connector with a
settle check ([inotify(7)](#src-inotify)). Budget for that. Add the SMB mount recovery
problem, credential rotation, and the fact that NTLM's retirement may change what
authentication your mount uses.

### Explicitly deferred, with reasons

- **Microsoft Graph and the Gmail API as the primary channel.** Per-tenant onboarding,
  admin consent, tenant-wide-permission risk, and (for Google) an annual security assessment.
  Add these when a specific client's residency requirement demands them, not before. Both are
  *the same channel* as the email-first recommendation; they are a transport upgrade, not a new
  capability, which is another reason the email decision is safe to make first.
- **Accounting-system APIs.** 2% of invoice volume, the wrong end of the pipeline, and now
  metered in the read direction on both QuickBooks Online and Xero. Revisit when a client asks
  for reconciliation, which is a *different feature* from intake.
- **Scanner direct-to-folder as a distinct connector.** It is not a distinct connector. The
  scanner writes to a share; the watched folder reads it. Ship documentation for configuring
  the printer, not new code.
- **Web upload.** Cheap to add as a manual fallback alongside the first channel, and worth
  doing for the internal-upload case, but it is not an arrival channel for supplier invoices.

### Instrument the gap

Because no public dataset answers section 2 for 5–50-employee businesses, make C5's first ten
deployments produce the data. Log, per invoice: which channel it arrived by, whether it was
forwarded or addressed directly, sender domain, and whether the approval gate rejected it.
That yields a proprietary dataset that is directly relevant to the next channel decision, and
it is a claim no competitor can match.

---

## Sources

<a id="src-deluxe"></a>
1. **Deluxe State of AP & AR Study, research conducted by Barlow Research Associates,
   January 2024** (data fielded 4–12 December 2023, n=793 firms with 5M–500M USD annual sales,
   weighted, max error ±3.48% at 95%). The invoice-receipt-channel chart is on p.9; methodology
   on p.4.
   https://www.deluxe.com/content/dam/deluxe/us/en/home/resources/payments/dpx/Deluxe-State-of-AP%20-AR-Study-Barlow-Research-2024.pdf
   *Primary survey data. Best available on invoice arrival channel. Middle-market sample, not
   small business. See the caveats in 2.2.*

<a id="src-afp"></a>
2. **2025 AFP Payments Fraud and Control Survey Report, Key Highlights** (Association for
   Financial Professionals; surveyed January 2025, data for 2024). BEC 63%, invoice fraud 24%
   (up from 14%), payments fraud incidence 79%, spoofed email 79% of BEC.
   https://www.truist.com/content/dam/truist-bank/us/en/documents/info/cci/2025-afp-payments-fraud-control-survey-report-key-highlights.pdf
   *Primary survey data. Sample skews to large corporates.*

<a id="src-ic3"></a>
3. **FBI Internet Crime Complaint Center, 2024 Internet Crime Report**. BEC: 21,442 complaints,
   2,770,151,146 USD losses in 2024.
   https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf

4. **Federal Reserve Small Business Credit Survey, 2024 Report on Payments** (n=4,853 employer
   firms; optional payments module on the 2023 SBCS). Payment terms and acceptance, not invoice
   receipt channel.
   https://www.fedsmallbusiness.org/-/media/project/clevelandfedtenant/fsbsite/reports/2024/2024-report-on-payments_sbcs.pdf

<a id="src-ms-basicauth"></a>
5. **Microsoft Learn — Deprecation of Basic authentication in Exchange Online.** "Basic
   authentication is now disabled in all tenants… no one (you or Microsoft support) can
   re-enable Basic authentication in your tenant." Covers POP, IMAP, EWS, EAS, RPS.
   https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online

6. **Microsoft Tech Community — Exchange Online to retire Basic auth for Client Submission
   (SMTP AUTH)**, announced April 2024; current milestone: end of December 2026, SMTP AUTH
   Basic Authentication disabled by default for existing tenants.
   https://techcommunity.microsoft.com/blog/exchange/exchange-online-to-retire-basic-auth-for-client-submission-smtp-auth/4114750

7. **Google — Less secure apps & your Google Account.** "Starting January 2025, less secure
   apps… will no longer be supported for Google Workspace accounts."
   https://support.google.com/accounts/answer/6010255

8. **Google Workspace Updates — Winding down Google Sync and Less Secure Apps support**
   (29 Sep 2023, with updates through April 2025; final disablement 1 May 2025).
   https://workspaceupdates.googleblog.com/2023/09/winding-down-google-sync-and-less-secure-apps-support.html

9. **Microsoft Learn — RBAC for Applications in Exchange Online.** The mechanism that replaced
   Application Access Policies; scopes an app's mailbox access to a defined resource scope.
   Requires Organization Management role group or Exchange Administrator role.
   https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access

10. **Microsoft Learn Q&A — Permissions to access shared mailbox to read and send emails.**
    Confirms application permissions grant access to all mailboxes, not just the shared one.
    https://learn.microsoft.com/en-us/answers/questions/2149155/permissions-to-access-shared-mailbox-to-read-and-s

11. **Gmail API — Scopes.** `gmail.readonly`, `gmail.metadata`, `gmail.modify` and
    `https://mail.google.com/` are restricted. "If you store restricted scope data on servers
    (or transmit), then you must go through a security assessment."
    https://developers.google.com/workspace/gmail/api/auth/scopes

12. **Google — OAuth App Verification** (annual re-verification for restricted scopes).
    https://support.google.com/cloud/answer/9110914

13. **Google — Verification requirements** (brand verification, homepage, privacy policy,
    domain ownership).
    https://support.google.com/cloud/answer/13464321

14. **Intuit App Partner Program pricing, summarized by Apideck** (program launched 28 July
    2025, full variable fees 1 November 2025; Core free/unlimited for writes, CorePlus metered
    for reads; Builder free to 500k credits with calls blocked past the limit, Silver 300
    USD/month, Gold 1,700 USD/month, Platinum 4,500 USD/month; 3.50 USD per 1,000 CorePlus
    overage on Silver).
    https://radar.apideck.com/blog/quickbooks-api-pricing-and-the-intuit-app-partner-program.md
    *Third-party summary. Intuit's own announcement page was Cloudflare-blocked during
    research; confirm figures against Intuit's developer portal before quoting them to a
    client.*

15. **Xero Developer Pricing.** Five plans (Starter/Core/Plus/Advanced/Enterprise) effective
    2 March 2026, based on connections and API egress; rate limits 1,000/day/org (Starter) and
    5,000/day/org (others); security assessment initial and annual on the upper tiers; and the
    policy statement that "data obtained from Xero's APIs may not be used to train AI/ML
    models."
    https://developer.xero.com/pricing

16. **The Register — Xero to start charging developers API usage fees** (4 Dec 2025).
    Independent reporting on the pricing change, including a developer's 17,340 USD/year
    estimate and the AI-training and bot prohibitions in the updated terms.
    https://www.theregister.com/2025/12/04/xero_api_fees_change/

<a id="src-inotify"></a>
17. **inotify(7) Linux man page — Limitations and caveats.** "it does not catch remote events
    that occur on network filesystems. (Applications must fall back to polling the filesystem
    to catch such events.)"
    https://man7.org/linux/man-pages/man7/inotify.7.html

18. **mount.cifs(8) Linux man page.** `cifs-utils`; kernel must support the cifs filesystem;
    CACHE COHERENCY section documents that other clients can change files without this client
    knowing; SECURITY and CONFIGURATION sections cover credential handling.
    https://man7.org/linux/man-pages/man8/mount.cifs.8.html

19. **Xerox KB0426910 — How to Configure Scan to SMB** (published 2024-11-06). Shows the
    create-a-local-user, assign-share-permissions, embed-credentials workflow.
    https://www.support.xerox.com/en-us/article/KB0426910

20. **Xerox KB0330118 — Configure Scan to Network (SMB)/FTP Settings Using the Embedded Web
    Server.** Shortcut type "Network Folder (for SMB) or FTP"; authentication options include
    a stored username and password.
    https://www.support.xerox.com/en-us/article/en/2158652

21. **Ricoh — Update: Impact of Microsoft basic authentication deprecation.** Vendor advisory
    on MFP impact after end of December 2026.
    https://www.ricoh.com/info/2025/0526_1

22. **Marco — Microsoft SMTP AUTH Deprecation To Cause MFP Scan-to-Email Disruption.**
    Channel-press confirmation that scan-to-email on MFPs is a Basic-auth dependency.
    https://www.marconet.com/blog/microsoft-smtp-auth-deprecation-to-cause-mfp-scan-to-email-disruption

23. **Help Net Security — Microsoft sets a path to switch off NTLM across Windows**
    (2 Feb 2026). Relevant to SMB share authentication for a Linux client.
    https://www.helpnetsecurity.com/2026/02/02/microsoft-windows-ntlm-disabling/

<a id="src-expensify-free"></a>
24. **Expensify Help — Use Free Features in Expensify.** "Forward receipts to
    receipts@expensify.com for automatic scanning," available on the free tier.
    https://help.expensify.com/articles/new-expensify/getting-started/Free-Features-in-Expensify

<a id="src-ramp-receipts"></a>
25. **Ramp Help Center — How to automatically forward receipts to Ramp.** Sender verification
    with a six-character code; centralized alias pattern; Gmail forwarding rules.
    https://support.ramp.com/hc/en-us/articles/360047979213-How-to-automatically-forward-receipts-to-Ramp

26. **Ramp — Bill Pay product page.** Confirms email forwarding is offered as an optional
    automation; lead messaging is drag-and-drop and OCR accuracy.
    https://ramp.com/bill-pay

<a id="src-brex-billpay"></a>
27. **Brex — Implementation guide, "11. Bill pay."** "automatically forwarded to Brex via
    email"; unique per-account address found under Bills > Add invoices; AI produces a draft
    that a human reviews.
    https://www.brex.com/implementation/premium/11-bill-pay

28. **Amex Vendor Pay (BILL) Help — Set up, use, and maintain your Inbox.** Per-account inbox
    address, minimum eight characters before @bill.com, provided to vendors.
    https://vendorpayhelp.bill.com/hc/en-us/articles/360006991712-Set-up-use-and-maintain-your-Inbox

<a id="src-expensify-bill"></a>
29. **Expensent — Email Invoices to BILL: Inbox Workflow.** Independent corroboration that
    BILL's intake destination is account-specific and must be read from inside the product.
    https://www.expensent.com/guides/email-receipts-to-bill

30. **Paperless-ngx — changelog.** v0.0.1 consumption directory; "Added IMAP mail support" in
    v0.0.5; later "All new mail consumer with customizable filters, actions and multiple
    account support."
    https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/changelog.md

31. **Paperless-ngx — usage documentation.** "The primary method of getting documents into your
    database is by putting them in the consumption directory"; scanner guidance points at FTP
    or a Samba share; OAuth email setup requires the operator's own developer app; Processed
    Mails tracked by server UID.
    https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/usage.md

32. **Paperless-ngx — configuration documentation.** `PAPERLESS_CONSUMER_*` options,
    `PAPERLESS_EMAIL_TASK_CRON` (default 10-minute polling), OAuth callback configuration.
    https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/configuration.md

33. **Docspell — Documents → Docspell.** Channel list in order: web upload, scanners/watch
    directories, Android, poll e-mails, SMTP gateway.
    https://docspell.org/docs/feed/

34. **Docspell — E-Mail Settings.** "Passwords to your e-mail accounts are stored in plain-text
    in docspell's database"; "Docspell doesn't support updating the access token" with a cron
    workaround documented.
    https://docspell.org/docs/webapp/emailsettings/

35. **Docspell — Scan Mailboxes.** IMAP folder lists, Received Since Hours, glob filters on
    filename and subject, and the warning about re-reading unmoved mail.
    https://docspell.org/docs/webapp/scanmailbox/

36. **Invoice Ninja — Import and Export.** CSV import with column matching, plus named importers
    for other accounting products. Outbound-oriented; no inbound supplier-invoice channel found.
    https://invoiceninja.github.io/docs/advanced-topics/import-and-export

37. **Spiceworks community thread — What MFP features do you use the most?** Single anecdotal
    response on scan-to-folder versus scan-to-email. Cited only to show that no real data
    exists on this split.
    https://community.spiceworks.com/t/what-mfp-features-do-you-use-the-most/603596

38. **APQC — Percentage of invoice line items invoiced using electronic or automatic methods.**
    Benchmark measure page; detailed cuts are member-gated.
    https://www.apqc.org/what-we-do/benchmarking/open-standards-benchmarking/measures/percentage-invoice-line-items-invoiced

*Repository statistics (stars, creation dates) retrieved from the GitHub API on 2026-10-16.*

**Link check (2026-10-16).** Every URL above was requested during research. All resolve except
three that return HTTP 403 to automated requests behind bot protection: the APQC measure page,
BILL's Vendor Pay help centre, and the Intuit announcement blog (which is why the Intuit pricing
figures are sourced to Apideck's summary). Those three load in a normal browser; the content
quoted from the BILL page was retrieved from a cached copy plus independent corroboration
([source 29](#src-expensify-bill)).

---

## Appendix: claims I could not verify

| Claim | Status |
|---|---|
| Invoice arrival channel shares for businesses with 5–50 employees | **No public data found.** The Deluxe/Barlow middle-market study is the closest. |
| Invoice arrival channel shares broken out by industry (medical, legal, accounting, trades) | **No public data found.** The Deluxe study reports channel shares at total-market level only. |
| Which scan destination (SMB folder vs email vs cloud) businesses use most | **No public data found.** Only a 2017 forum anecdote. |
| Why Ramp, Brex, BILL or Expensify chose email intake first | **No public statement found.** The evidence is product shape and feature availability, not a stated rationale. |
| CASA security assessment cost for Gmail restricted scopes | **Conflicting, non-authoritative figures** (540–1,800 USD/year vs 15,000–75,000 USD). No Google price list found. |
| Whether a client-owned single-tenant OAuth app is exempt from Google's restricted-scope verification | **Not confirmed.** Google's pages distinguish apps by distribution to outside users but do not state a crisp single-tenant exemption. Confirm before relying on it. |
| Whether Microsoft Graph mail reads require a specific M365 SKU | **Not verified.** Graph API access appears free with the subscription; no per-call pricing page exists. |
| Whether Office Inference's local inference over client documents breaches Xero's "may not be used to train AI/ML models" clause | **Legal question, not researched.** Local inference is not obviously training, but the clause should be read by counsel before any Xero integration. |
| Whether NTLM disablement affects a Samba/SMB mount from a Linux container using a local service account | **Not verified.** Needs testing against a current Windows Server build before promising an SMB connector. |
| Per-vendor scanner cloud-destination feature matrix (HP, Canon, Ricoh, Xerox, Brother, Epson, Fujitsu) | **Not gathered.** Xerox documentation was checked; the others were not. |
| Whether SAP Concur's or MineralTree's AP reports contain arrival-channel data | **Not established.** Both PDFs failed to download or extract cleanly during research. |
