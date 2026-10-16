# Email intake that survives the vendor

Research report, 2026-10-16. Prepared for the decision "how does Office Inference build email
intake so the client still owns it after we leave." Every factual claim carries a URL. Claims I
could not verify are marked **UNVERIFIED** and the gap is named instead of filled.

---

## 1. Headline recommendation

**Register the application in the client's own tenant, read one licensed service mailbox with
delegated `Mail.Read`, and never operate a mailbox, relay, or app registration on Office
Inference infrastructure.**

Named components and where each lives:

| Component | Where it lives | Who owns it after handover |
|---|---|---|
| Intake address (`ap@client.com`, unchanged) | Client's Microsoft 365 tenant | Client |
| Mailbox `ap@client.com` | Client's Exchange Online | Client |
| App registration `Office Inference Intake` | Client's Microsoft Entra ID | Client |
| Enterprise application / service principal | Client's Microsoft Entra ID | Client |
| Consent grant (`oauth2PermissionGrant`) | Client's Microsoft Entra ID | Client |
| Client secret or certificate | Client's own box, in the deployment's secret store | Client |
| Refresh token | Client's own box, encrypted at rest | Client |
| Polling daemon | Client's box or client's cloud account | Client |
| Consent, rotation, revocation | Client's admin, in the Entra admin center | Client |

Three findings drove this, and two of them are not obvious.

**Finding one: a shared mailbox cannot be used for this, and that is what forces the design.**
Microsoft's shared-mailbox documentation says a shared mailbox "isn't intended for direct sign-in
by using its associated user account. Always block sign-in for the shared mailbox account and
keep it blocked," and "Every shared mailbox has a corresponding user account with a
system-generated password that isn't known or intended for use."
([Microsoft 365 admin: About shared mailboxes](https://learn.microsoft.com/en-us/microsoft-365/admin/email/about-shared-mailboxes)).
Delegated OAuth needs the mailbox to complete a sign-in once to mint the refresh token. A shared
mailbox cannot. So the moment you place intake on a shared mailbox you are pushed onto
application permissions, and application permissions are exactly where Microsoft Graph's
`Mail.Read` reaches every mailbox in the tenant.

**Finding two: delegated permission on a licensed user mailbox is already scoped to one mailbox,
and it is the only option that is scoped without PowerShell.** Graph's `Mail.Read` delegated
permission reads the signed-in user's mail. It needs no Exchange RBAC grant, no management scope,
and no cmdlet. Application permission is the one that needs scoping, and the scoping mechanism is
PowerShell only. Choosing delegated permissions therefore removes the PowerShell problem rather
than solving it.

**Finding three: the app registration cannot be moved later.** A Microsoft Q&A answer
accepted by the question author states app registrations cannot be migrated between tenants;
multi-tenant apps can be *added* to another tenant as an enterprise application but "all
configuration and management must still be done in the home tenant"
([Microsoft Q&A: Can we migrate applications under App Registrations to another tenant?](https://learn.microsoft.com/en-ca/answers/questions/2259370/can-we-migrate-applications-under-app-registration)).
I could not find a Microsoft Learn page that states this as product documentation, so treat the
Q&A as strong but secondary evidence, and treat the operational conclusion as certain anyway: it
must be created in the client's tenant from the start.

The requirement "the client may take over completely and the vendor is out of the picture" is
therefore satisfied, with one qualification recorded in section 8: the design still depends on
Microsoft, and on the client's own administrator noticing a break. It does not depend on Office
Inference, which is what was asked.

---

## 2. Application registration in someone else's tenant

### 2.1 Microsoft Entra ID

**Can it be transferred?** No. There is no supported move of an app registration between tenants.
The accepted answer on Microsoft Q&A is explicit and gives the workaround: register multi-tenant
in the home tenant, then add the enterprise application in the resource tenant, accepting that
"all configuration and management must still be done in the home tenant"
([Microsoft Q&A](https://learn.microsoft.com/en-ca/answers/questions/2259370/can-we-migrate-applications-under-app-registration)).
Microsoft's own cross-tenant article describes only the *service principal* being created in the
second tenant from the multi-tenant app
([Create an enterprise application from a multitenant application](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/create-service-principal-cross-tenant)).
A service principal in the client's tenant pointing at an application object in the vendor's
tenant is precisely the disqualified design: the client cannot rotate the secret, cannot change
the redirect URI, and deleting their service principal does not stop the vendor's app object
existing.

So the rule is: **the application object must be created in the client's tenant.** There is no
after-the-fact fix.

**What consent actually is.** Admin consent is a protocol step. The app sends the admin to
`https://login.microsoftonline.com/{tenant}/v2.0/adminconsent`, and on approval the app receives
`admin_consent=True` plus the tenant GUID
([Admin consent on the Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/v2-admin-consent)).
One detail matters for us: "In order to request app permissions, you must use the `/.default`
value." Delegated permissions do not require that, so a delegated-permission design can use
dynamic consent and show the administrator a short, readable consent screen. That is a real
usability difference at a 5-to-50-person client.

**Who can approve.** Microsoft treats tenant-wide admin consent as a sensitive operation. Microsoft:
"When you grant tenant-wide admin consent to an application, you give the application access to
the permissions requested on behalf of the whole organization. Granting admin consent on behalf
of an organization is a sensitive operation"
([Grant tenant-wide admin consent to an application](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)).
For app-only permissions there is a further narrowing: where the admin consent *workflow* is
enabled, reviewers can deny or block requests but "only Global Administrators can approve admin
consent requests for apps requesting for Microsoft Graph app roles (application permissions)"
([Configure the admin consent workflow](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-admin-consent-workflow)).
That is another point against application permissions. Asking a small business to have a Global
Administrator available is a bigger ask than asking for the Application Administrator or Cloud
Application Administrator role.

**The professional way for the vendor to do the setup** is the partner model, not a shared
password. Microsoft's Granular Delegated Admin Privileges (GDAP) is designed for exactly this:
a partner gets time-bound, role-scoped administrative access to a customer tenant, and the access
expires on a date the customer can see. **UNVERIFIED**: I did not fetch the GDAP documentation in
this pass, so I am naming it as the pattern to check rather than describing its mechanics. The
alternative without Partner Center is a temporary guest account with Cloud Application
Administrator, deleted at handover, which is simpler and leaves a cleaner audit trail for a
five-figure engagement.

**What the client needs to manage it without us:**

- Application Administrator or Cloud Application Administrator to add or rotate a secret.
- The app's **Object ID** and **Application (client) ID** from *Enterprise applications*, not
  from *App registrations*. Microsoft calls this out because the pages show different values:
  "Don't use the IDs from the App Registrations page, as it shows different values"
  ([RBAC for Applications in Exchange Online](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)).
- The ability to see and revoke the grant, which Microsoft exposes three ways: the Entra admin
  center under Enterprise applications, the Graph endpoints `GET /v1.0/oauth2PermissionGrants`
  and `GET /v1.0/servicePrincipals/{id}/appRoleAssignments`
  ([List oauth2PermissionGrants](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-list)),
  and Microsoft Graph PowerShell (`Get-MgOauth2PermissionGrant`,
  `Get-MgServicePrincipalAppRoleAssignment`).

### 2.2 Google Workspace

**Can it be transferred?** **UNVERIFIED, and I suspect not.** I did not find a Google page
documenting transfer of an OAuth client ID between Google Cloud projects or between
organizations. Google does document moving a *project* between organizations and folders
(`resourcemanager.projects.update`), which is a different operation and does not move an OAuth
client to a different project. I would not plan on transfer. Create it in the client's project.

**The verification escape hatch is real and documented.** Google publishes an explicit exemption
list. The relevant entry:

> "Internal Use apps: The app is only used by people in your Google Workspace or Cloud Identity
> organization. The project must be owned by the organization, and its OAuth Consent Screen must
> be configured for internal use. In this case, your app might need approval from an organization
> administrator. Note: Your app will not be subject to the unverified app screen or the 100-user
> cap if it's designated as internal-only."
> — [When is verification not needed](https://support.google.com/cloud/answer/13464323)

That is a strong, citable basis for the client-owned-project design on Google.

**But there is a contradiction I could not resolve, and it is material.** The Gmail scope page
classifies `gmail.readonly` as **restricted** and then says flatly: "If you store restricted
scope data on servers (or transmit), then you must go through a security assessment"
([Choose Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)). The
Google Cloud help centre separately says apps requesting restricted scopes "must complete
Google's OAuth app verification" and that "Apps requesting restricted scopes data need to
complete 're-verification' annually"
([OAuth App Verification Help Center](https://support.google.com/cloud/answer/9110914)). Neither
page carves out Internal-only apps from the security assessment, and the exemption page does not
mention the assessment at all. A developer-forum thread from four days before this report is
asking the same question, which suggests it is live and unsettled
([Google Developer forums: Personal-use restricted-scope assessment applicability](https://discuss.google.dev/t/personal-use-restricted-scope-assessment-applicability/401948)).

**What to do about it:** do not design around the assumption that Internal-only exempts you from
CASA. Put the question to Google Cloud support in writing before quoting a Google Workspace
client, and keep the answer. Mark this as the single largest open risk on the Google leg.

**Domain-wide delegation is the wrong tool here, and Google says so.** Domain-wide delegation
lets a service account impersonate any user. Google's own warning: "With domain-wide delegation,
the app has access to the data belonging to all of your users. We recommend setting up a regular
review of service accounts and deleting any accounts no longer in use"
([Control API access with domain-wide delegation](https://support.google.com/a/answer/162106)).
It also requires a **super admin**, which is a heavier ask than the Entra side, and it is
configured in the Admin console under Security > Access and data control > API controls > Manage
Domain Wide Delegation. If we ever recommend DWD we are recommending that a 12-person accounting
firm grant tenant-wide impersonation to a vendor-written client. Don't.

**Where the admin revokes it.** Admin console > Security > Access and data control > API controls,
under "Manage App Access." Google documents that the app list shows an **Ownership** column with
values "third party, internal, or Google owned," and an access setting of Trusted, Limited,
Specific Google data, or Blocked
([Control which apps access Google Workspace data](https://support.google.com/a/answer/7281227)).
Two operational details from that page worth putting in a runbook: app details "typically appear
24–48 hours after authorization," and the page requires the **Service Settings administrator**
privilege.

---

## 3. Reading one mailbox as a background service

Three candidate designs. I compared them on the six axes asked for.

### 3.1 The finding that eliminates the obvious delegated candidate

A shared mailbox has no usable credential. Microsoft: "A shared mailbox isn't intended for direct
sign-in by using its associated user account. Always block sign-in for the shared mailbox account
and keep it blocked"; "Every shared mailbox has a corresponding user account with a system-generated
password that isn't known or intended for use"
([About shared mailboxes](https://learn.microsoft.com/en-us/microsoft-365/admin/email/about-shared-mailboxes)).
Delegated auth of any kind needs one interactive sign-in. So *delegated + shared mailbox is not a
design*, and any proposal that assumes it is wrong.

Also note the shared-mailbox limits on that page: a shared mailbox "doesn't require a separate
license" but needs a licensed user to access it, "supports a maximum of 25 users," and "Email sent
from a shared mailbox can't be encrypted because the mailbox doesn't have its own security
context."

### 3.2 Comparison

| | **A. Delegated OAuth + refresh token, licensed service user** | **B. Application permission scoped by Exchange RBAC for Applications** | **C. IMAP with OAuth (XOAUTH2)** |
|---|---|---|---|
| Exact scope | `Mail.Read` (delegated) + `offline_access`, acting as one user | `Mail.Read` (application) plus an Exchange Online resource scope limiting it to one mailbox | `IMAP.AccessAsUser.All` (delegated), acting as one user |
| What it reaches if misused | That one mailbox. The refresh token is bound to user + client and cannot be widened without a new consent | Every mailbox **unless** the RBAC scope is configured; and see the union problem below | That one mailbox |
| Does it survive a password change | Yes. Microsoft's revocation table marks confidential-client tokens "Stays alive" for *user* password change, SSPR, and admin reset via the Entra admin center. They are revoked by *admin reset via the Microsoft 365 admin center* ([Refresh tokens](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)) | Yes, entirely. No user credential is involved | Same as A |
| Does it survive an MFA change | Only if Conditional Access does not demand interactive MFA on the token endpoint. ROPC-style and background flows cannot satisfy MFA; Microsoft states ROPC "is incompatible with multifactor authentication" ([ROPC](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth-ropc)) | Yes. "MFA does not apply to Service Principals" ([ROPC](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth-ropc)) | Same as A |
| Credential lifetime | Refresh token default 90 days, "for all other scenarios," and it "replace[s] itself with a fresh token upon every use." Access token 60–90 minutes | Client secret or certificate. A secret has a fixed expiry; a certificate typically one year. **AADSTS7000222** = "The provided client secret keys are expired" | Same as A |
| What the client's admin must do to maintain it | Nothing routine, if the poller never idles 90 days. Re-consent if it does | Nothing routine, but the initial PowerShell setup is the burden | Same as A |
| Setup cost for the client | App registration, one consent click, one secret created | App registration, Entra consent, then four PowerShell steps as `Organization Management` | App registration, consent, plus a mailbox protocol that is on a deprecation path |
| Verdict | **Recommended** | Technically the strongest containment, operationally the weakest fit | Acceptable fallback; adds no capability A lacks |

### 3.3 Why not Exchange RBAC for Applications, given it is Microsoft's own answer

It is a sound mechanism, and it is the correct answer for a mid-size client with IT staff. Four
things make it the wrong default for a 5-to-50-person business.

**It does not narrow anything on its own. It is additive.** Microsoft says so directly:

> "The permissions assigned using Application RBAC act in addition to grants you make in
> Microsoft Entra ID. Microsoft Entra permissions can only be constrained using Application
> Access Policies. In other words, the assigned permissions are a union operation on the
> permissions from Microsoft Entra ID and the permissions assigned in Exchange Online RBAC. Each
> authority can act independently."
> — [RBAC for Applications in Exchange Online](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)

So the sequence is: grant `Mail.Read` in Entra, then create the scope and role assignment in
Exchange, and then **remember to remove the Entra grant**. Microsoft's own worked example of
getting this wrong shows the failure: "While the `Mail.Read` from Microsoft Entra-only allows
access to Mailbox A, the RBAC assignment allows access to everything except A. In effect, this
assignment allows access to everything." An administrator who does three of the four steps has
built something that looks scoped and is not. That is a bad thing to hand a non-technical client.

**It is PowerShell only.** The documented steps are `New-ServicePrincipal`,
`New-ManagementScope`, `New-ManagementRoleAssignment`, and
`Test-ServicePrincipalAuthorization`, after `Connect-ExchangeOnline`. It requires membership in
the **Organization Management** role group, or the Exchange Administrator role in Entra
([same page](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)). There
is no equivalent admin-center surface.

**Changes take up to two hours to apply.** "Changes to app permissions are subject to cache
maintenance that varies between 30 minutes and 2 hours depending on the app's recent usage." A
client debugging an access problem will not see their fix take effect and will conclude the whole
thing is broken.

**It breaks Autodiscover.** "Currently, the AutoDiscover service can't be accessed when using
RBAC Application roles." Anything relying on Autodiscover is out.

One useful property in it supports our recommendation: RBAC for Applications
supports **MS Graph and EWS**, and the role list includes `Application Mail.Read` whose own
description is "Allows the app to read email in all mailboxes without a signed-in user." That
description is the case against using application permission unscoped, in Microsoft's own words.

Two properties worth recording if we ever do use it: `New-ManagementRoleAssignment` accepts
`-RecipientAdministrativeUnitScope`, so scope can be an Entra administrative unit rather than a
mailbox filter; and deleting the service principal in Entra automatically removes the Exchange
side, "This removal deletes any assignments made to these service principals but leave management
scopes unaffected."

### 3.4 IMAP with OAuth

Viable, and the option Zammad actually uses in production (section 4). It reaches the same single
mailbox as delegated Graph. The costs are that it is a second protocol to maintain, that
Microsoft is progressively retiring Basic authentication across protocols and has a separate
timeline for SMTP AUTH
([Exchange Online to retire Basic auth for Client Submission](https://techcommunity.microsoft.com/blog/exchange/exchange-online-to-retire-basic-auth-for-client-submission-smtp-auth/4114750)),
and that on Google the IMAP path went through the Less Secure Apps shutdown
([Google Workspace Updates](https://workspaceupdates.googleblog.com/2023/09/winding-down-google-sync-and-less-secure-apps-support.html)).
Graph is the forward path. Keep IMAP as a documented fallback, not the primary.

### 3.5 Change notifications instead of polling

Worth knowing but not recommended for an on-prem box. Webhook subscriptions require a public HTTPS
`notificationUrl` with a valid TLS certificate that Microsoft Graph can reach, for validation and
for every subsequent notification
([Receive change notifications through webhooks](https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks)).
The subscription also carries its own delivery access token, described as generally expiring
within an hour, and "If an access token expires, notifications aren't delivered." That is a second
expiring thing to monitor, on top of the refresh token. For one mailbox and a poll interval of a
minute or two, polling is simpler and has fewer moving parts. **UNVERIFIED**: the widely cited
maximum mail subscription lifetime of about 4,230 minutes. The `subscription` resource page points
to a "Subscription lifetime" reference I could not retrieve (the page 404s), so I am not asserting
the number.

---

## 4. Does anything comparable solve this?

Yes, and the pattern is more common than expected. Two distinct families exist.

### 4.1 The "client creates the app in their own tenant" family

**Zammad** is the clearest published example, and it is a self-hosted helpdesk, which is the
closest analogue to what we are building. Its Microsoft 365 setup guide tells the *customer's*
administrator to open the Entra portal, "Create a new app by going to Applications > App
registrations and select New registration," choose a supported account type (single tenant is
listed first and supported), paste the callback URL Zammad provided as the redirect URI, create a
client secret and set an expiry, then add **delegated** Graph permissions: `email`, `openid`,
`profile`, `offline_access`, `IMAP.AccessAsUser.all`, `SMTP.Send`. The doc labels admin consent
"optional," notes it "is only required for tenants that require admin consent," and recommends
setting **Assignment required** to Yes plus restricting *Users and groups* as a hardening step.
Crucially, the note on that hardening step explains its purpose: it "will protect your tenant
users from —to admins— unwanted permission changes (e.g. requesting more permissions than
originally noted)"
([Zammad: Register an OAuth App](https://admin-docs.zammad.org/en/latest/channels/microsoft365/accounts/register-app.html)).

That page is close to a drop-in template for our runbook, including the "make sure to copy the
**value**, not the ID" warning on the secret, which is a real support-ticket generator.

**Odoo** does the same on the Google side. Its documentation walks the customer through creating a
Google Cloud project, configuring the OAuth consent screen, choosing user type, and creating
credentials, with an explicit warning that "Personal Gmail Accounts are only allowed to be
External User Type, which means Google may require an approval, or for Scopes to be added on.
However, using a Google WorkSpace account allows for Internal User Type to be used." It then has
the customer paste the client ID and secret into Odoo's settings
([Odoo: Connect Gmail to Odoo using Google OAuth](https://www.odoo.com/documentation/18.0/applications/general/email_communication/google_oauth.html)).

Two things to take from Odoo's version. First, its docs put the credential *in the customer's
database*, not in Odoo's service. Second, it names the internal-user-type advantage in plain
language, which is the same escape hatch Google's exemption page formalises.

**Paperless-ngx** does it too, and states the position in one sentence: "you will need to create a
'developer' app with the respective provider and obtain the client ID and client secret," with
token refreshing "handled automatically"
([Paperless-ngx usage docs](https://github.com/paperless-ngx/paperless-ngx/blob/main/docs/usage.md)).
There is also a dedicated wiki page for the app-setup procedure
([Paperless-ngx wiki: Email OAuth App Setup](https://github.com/paperless-ngx/paperless-ngx/wiki/Email-OAuth-App-Setup)).

### 4.2 The "we told you it's hard" family, as the warning label

**Docspell** is the useful cautionary example because it states its own limitations plainly. Its
email settings page states: "Passwords to your e-mail accounts are stored in plain-text
in docspell's database." On Gmail OAuth it explains the design and then the failure: the access
token "expires. Docspell doesn't support updating the access token. It could be worked around by
setting up a cron-job" that regenerates the token and PUTs it back over the REST API, and it
publishes the cron script
([Docspell: E-Mail Settings](https://docspell.org/docs/webapp/emailsettings/)).

That page also contains a claim worth flagging as wrong in a way that matters. It says of the
Gmail refresh token, "The Refresh Token doesn't expire and can be used to generate new access
tokens." Google's own OAuth overview says the opposite in general terms: "Limits apply to the
number of refresh tokens that are issued per client-user combination, and per user across all
clients, and these limits are different. If your application requests enough refresh tokens to go
over one of the limits, older refresh tokens stop working"
([Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2)).
**UNVERIFIED**: the specific limits (the commonly cited numbers are 100 refresh tokens per
client-user pair and 50 across all clients for a user, and a 7-day expiry for apps left in
"Testing" publishing status). I could not retrieve a Google page stating those figures, so treat
them as folklore until confirmed. The design lesson stands regardless: refresh tokens are a
limited, consumable resource, and a bug that re-consents on every restart will silently exhaust
them. Store the token, never re-mint it in a retry loop.

### 4.3 What the pattern tells us

The self-hosted products that have thought about this converge on the same answer we did: **the
customer creates the OAuth application in the customer's own project, and the product stores the
resulting credential locally.** Nobody in this survey hands the customer a vendor-hosted app
registration and calls it self-hosted. That is a useful competitive fact as well as a
design confirmation.

**UNVERIFIED / open:** I did not complete the survey of the commercial on-premises capture
vendors (Kofax/Tungsten, M-Files, DocuWare, Laserfiche). Their pages are largely
gated or JavaScript-rendered. Whether any of them publishes a "register the app in your own
tenant" procedure is the one part of this section I cannot answer, and it is the part most likely
to contain a reusable procedure from a mature product. Worth a follow-up pass.

---

## 5. The mailbox-on-the-box option, priced

This is the only design where no third party can read the mail, so it needs a real answer rather
than a dismissal. It is viable in one configuration and a liability in the others.

### 5.1 What it takes, and what breaks

**MX and DNS.** An MX record's value must be a hostname, not an IP literal, and it must not be a
CNAME. RFC 2181 §10.3: "The domain name used as the value of a NS resource record, or part of the
value of a MX resource record must not be an alias"
([RFC 2181](https://www.rfc-editor.org/rfc/rfc2181)). Lower preference wins; equal preferences are
tried in unspecified order. A null MX (`MX 0 .`, [RFC 7505](https://www.rfc-editor.org/rfc/rfc7505))
is the way to say "this domain accepts no mail" and is not what you want here.

**The three things that actually break it, in order of how often:**

1. **Inbound port 25 on the ISP.** This is the decisive constraint. Most US residential and many business
   broadband plans either block inbound 25, place the connection behind carrier-grade NAT, or both.
   **UNVERIFIED**: I could not find authoritative published inbound-port-25 policies for Comcast,
   Verizon, AT&T, Spectrum, Cox, or Frontier in this pass. That absence is itself the finding: you
   cannot determine whether a given client's ISP will work without asking the ISP directly, in
   writing, before quoting the design. Treat "does your business internet provider allow inbound
   TCP/25 and can we get a static IP with a reverse DNS record" as a mandatory discovery question.
2. **In cloud accounts, inbound 25 is fine but outbound 25 is blocked.** AWS blocks outbound port
   25 to public IPv4 and IPv6 addresses by default and requires a support request to remove the
   restriction ([Amazon EC2 service quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html)).
   For inbound, AWS's own knowledge-base article says the restriction "works on the instance
   level" and "AWS doesn't block inbound traffic on the Network Load Balancer"
   ([AWS re:Post: Remove port 25 restrictions](https://repost.aws/knowledge-center/ec2-port-25-throttle)).
   So a cloud-hosted receiver can accept mail immediately, and cannot send a bounce, a
   confirmation, or a forward without a support case. **UNVERIFIED**: Azure and Google Cloud
   inbound-25 behaviour, which I did not fetch. Their outbound SMTP restrictions are well known;
   inbound is the question and it needs checking before a cloud deployment is quoted.
3. **Spam and phishing volume with no filtering.** An unfiltered Postfix will deliver malware and
   phishing straight into the invoice queue, where a downstream LLM and a human approver both read
   it. This is the risk that turns a cost question into a liability question.

**TLS is the easy part.** Let's Encrypt issues certificates for a mail hostname, certbot renews
them, and the rate limits are published
([Let's Encrypt rate limits](https://letsencrypt.org/docs/rate-limits/)). STARTTLS is opportunistic
and downgradable ([RFC 3207](https://www.rfc-editor.org/rfc/rfc3207)); MTA-STS
([RFC 8461](https://www.rfc-editor.org/rfc/rfc8461)) is what makes it enforceable, via a
`_mta-sts` TXT record and an HTTPS policy file. **UNVERIFIED**: the current MTA-STS adoption rate,
which decides whether enforcing it costs you legitimate senders. For a single invoice address fed
mostly by known suppliers, DNS-based allowlisting is a more practical control than MTA-STS.

**Spam filtering has a licensing constraint that is easy to miss.** Spamhaus's free public mirrors
are "provided free of charge for **non-commercial** use by small and medium sized organisations,"
your query volume "must not exceed volumes reasonably expected in circumstances of non-commercial
use," queries must come from an identifiable recursive resolver, and "Queries originating from
large shared hosting environments are not accepted." Commercial use "will require a subscription
to our Datafeed Service"
([Spamhaus DNS Blocklists Fair Use Policy](https://www.spamhaus.org/blocklists/dnsbl-fair-use-policy/)).
A mail server run for a business is commercial use. Budget for the Datafeed subscription, and
budget for the query-source requirement, which a containerised resolver in a cloud account can
easily violate.

Also budget for the false-positive conversation. rspamd and ClamAV both have documented memory and
CPU floors; **UNVERIFIED**, I did not fetch them, but a filtered stack is not a 1 GB VM. And
someone has to review quarantine. In a 12-person firm, that someone is the office manager.

**Deliverability only matters if you send.** A receive-only design does not need sending
reputation. It does still need to send bounces and possibly acknowledgements, and that is where it
gets expensive. Google's requirements for *all* senders, since 1 February 2024, are: SPF **or**
DKIM, a valid PTR record, and a TLS connection
([Email sender guidelines](https://support.google.com/a/answer/81126)). The hardest requirement
is the PTR record, because it means asking the ISP or cloud provider to set reverse DNS, which is a
ticket and sometimes a paid feature. For 5,000+ messages a day you additionally need SPF **and**
DKIM **and** DMARC and From-domain alignment. A single invoice address will never approach that
volume, so the low-volume rules apply.

### 5.2 Cost table

**UNVERIFIED where noted.** I did not fetch current pricing pages for most line items in this pass;
the numbers below are the shape of the cost, not quotes. Verify before putting a figure in a
proposal.

| Line item | Rough magnitude | Note |
|---|---|---|
| Static IP / business-plan uplift | 0–30 USD/month | **UNVERIFIED.** Some business plans include one; some charge. Depends entirely on ISP |
| Cloud instance for the receiver | 10–40 USD/month | Plus a public IPv4 charge, which AWS now bills for separately, **UNVERIFIED** amount |
| Reverse DNS (PTR) | usually free, sometimes a support ticket | Required only if the box sends |
| TLS certificate | 0 | Let's Encrypt |
| Spamhaus Datafeed | paid, **UNVERIFIED** | Commercial use requires it |
| Spam filtering stack | 0 in licences | Real cost is the VM size (4 GB+ RAM is realistic) |
| **Ongoing attention** | **2–6 hours/month** | Patching, cert renewal, quarantine review, blocklist delisting, deliverability triage when a supplier's mail bounces. This is an estimate, not a measurement; treat it as the figure to argue about |

That last row is the headline. The mailbox-on-the-box design is cheap in dollars and
expensive in the one resource a 12-person business does not have. It also puts the client on the
hook for a mail server they cannot operate, which is a support relationship we would be creating
at the exact moment we are trying to end one.

### 5.3 Grey-cloud gateways, and one that is different in kind

A filtering gateway can read the mail by definition. That is what filtering means. So a
third-party spam filter does not preserve the property this section exists to preserve.

But there is an important distinction between two kinds of third party.

**Vendor-operated filter** (Proofpoint, Barracuda, SpamTitan, MXguarddog): the operator is a
company the client does not control, holding mail content. Disqualified for the same reason the
vendor-hosted mailbox is disqualified.

**Client-account-hosted relay** (the client's *own* AWS account, using SES email receiving or SES
Mail Manager): the operator is Amazon, but the account, the S3 bucket, the rules, and the bill all
belong to the client, and the client can revoke our access independently. This is a different
thing. **UNVERIFIED**: whether SES Mail Manager can relay to an arbitrary SMTP endpoint, and
whether SES email receiving can forward to an arbitrary host. Plain SES receiving stores to S3,
publishes to SNS, or invokes Lambda, and its MX record points at
`inbound-smtp.<region>.amazonaws.com`
([Publishing an MX record for Amazon SES email receiving](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html)).
That is enough to ingest without any vendor in the path, even if it is not a relay to the client's
own box. Worth a verification pass if a client wants the mail to physically land on their premises.

**Cloudflare Email Routing is not a relay to your server.** Its documentation is unambiguous about
destinations: a routing rule pairs an email pattern with "either a verified email address or a
Worker," and "Before you can create a routing rule, you must add and verify at least one
destination address"
([Cloudflare Email Routing: routing rules and addresses](https://developers.cloudflare.com/email-routing/setup/email-routing-addresses/)).
You cannot point it at an arbitrary MX host. It is free and it is an excellent answer to a
different question (forward `ap@` to an existing inbox), not to this one.

### 5.4 Verdict on the box design

**Recommend it only when the client already has business broadband with a static IP and confirmed
inbound port 25, and only paired with a "we monitor the queue" retainer.** In that one case it is
the strongest possible answer to "no third party can read the mail" and it costs almost nothing.
For a client on residential broadband, behind CGNAT, or in a cloud account without a port-25
exemption, it is a support liability. Do not sell it as the default and do not dismiss it as
impossible; make it a conditional option with a stated precondition.

---

## 6. Migration and exit

The exit procedure is short because the design never gave us anything to give back. That is the
point.

### 6.1 The exit sequence

Run in this order. Steps 1–4 are the client's admin; 5–7 prove it.

1. **Remove vendor administrative access.** Delete the guest account, or let the GDAP relationship
   expire, or remove the vendor's role assignment. Verify in Entra under *Roles and administrators*
   and under *External identities > Cross-tenant access settings*.
2. **Rotate the client secret or certificate.** In the client's app registration, *Certificates &
   secrets*: create a new secret, install it on the box, then delete ours. A rotation the vendor
   never sees is the strongest available proof that the vendor's copy is dead.
3. **Re-mint the refresh token, if delegated.** The client's admin signs into the mailbox once, or
   re-runs the consent flow with the new secret, which issues a refresh token we have never held.
   Alternative: delete the app's grant entirely (step 4) and re-grant, which invalidates every
   outstanding refresh token for that client-user pair.
4. **Revoke and, if the client wants, delete.** Under *Enterprise applications > [app] >
   Permissions*, revoke the grant. If the client wants a clean slate, delete the app registration
   and the service principal. Note Microsoft's behaviour: deleting the service principal in Entra
   automatically removes its Exchange RBAC assignments, but "leave[s] management scopes unaffected"
   ([RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)).
   If RBAC was used, delete the management scope too.
5. **Prove the vendor holds nothing.** Enumerate the grants.
6. **Prove we are not on the box.** Confirm no vendor SSH key, no vendor VPN client, no vendor
   monitoring agent, no vendor-owned container registry credentials.
7. **Record it.** Date, actor, and the command output, in a document the client keeps.

### 6.2 The proof commands

Enumeration via Microsoft Graph. These need `Directory.Read.All` delegated or application, and the
signed-in user needs one of: Global Reader, Directory Readers, Application Administrator,
Application Developer, Cloud Application Administrator, Directory Writers, Privileged Role
Administrator, or User Administrator
([List oauth2PermissionGrants](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-list)).

```powershell
# What delegated permissions exist in this tenant, and to which apps?
Get-MgOauth2PermissionGrant -All | Format-Table ClientId, ConsentType, PrincipalId, Scope

# What application (app-only) roles have been granted?
Get-MgServicePrincipal -All |
  ForEach-Object { Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $_.Id } |
  Format-Table PrincipalDisplayName, ResourceDisplayName, AppRoleId

# Every service principal whose display name could be ours
Get-MgServicePrincipal -All | Where-Object DisplayName -match 'Office Inference' |
  Format-List DisplayName, AppId, Id, SignInAudience
```

The plain-language claim to make to the client is: after step 4, `Get-MgServicePrincipal` returns
nothing for our name, and `Get-MgOauth2PermissionGrant` shows no grant whose `ClientId` is our
`AppId`. Those are the two facts that answer "is the vendor still connected."

### 6.3 The audit trail has a shelf life, so export the evidence

This is the part that catches people out. Entra audit retention for Audit (Standard) is **180
days**, raised from 90 days for records generated on or after 17 October 2023. The one-year default
retention for Exchange, SharePoint, OneDrive, and Entra records applies only to users with an E5,
Microsoft Purview Suite, or E5 eDiscovery and Audit add-on licence; "If you have non-E5 users or
guest users in your organization, their corresponding audit records are retained for 180 days"
([Manage audit log retention policies](https://learn.microsoft.com/en-us/purview/audit-log-retention-policies)).

So a 5-to-50-person client on Business Standard has **180 days** of audit history. If the exit
happens and someone asks six months later whether the vendor ever had access, the answer is no
longer in the tenant. Export the consent and service-principal enumeration to a file the client
keeps, on the day of exit. Put that in the runbook as a required step, not an optional one.

---

## 7. Failure modes specific to handover, and who notices

The question "who monitors it if the vendor is gone" has a good answer, and it does not require
the client to understand OAuth.

### 7.1 What breaks, how it looks, and what notices

| Failure | What actually happens | Error you will see | Who should notice |
|---|---|---|---|
| Refresh token idles past 90 days | Token dies. No grace period | `AADSTS700082` — "The refresh token has expired due to inactivity. The token was issued on {issueDate} and was inactive for {time}" | The client's own poller, on its next attempt |
| Service account password changed by admin **via the M365 admin center** | Refresh token revoked. Via the *Entra* admin center, or by the user, confidential-client tokens stay alive | Authentication failure, then re-consent prompts | The poller |
| Conditional Access now demands MFA or a compliant device | Background token acquisition blocked. This is the failure nobody predicts | `AADSTS53003` — "BlockedByConditionalAccess ... The access policy does not allow token issuance" | The poller, but the fix is a policy exclusion, so the message must name Conditional Access explicitly |
| Admin revokes sessions, or a password reset invalidates grants | Token dead | `AADSTS50173` — "The provided grant has expired due to it being revoked, a fresh auth token is needed. The user might have changed or reset their password" | The poller |
| Consent revoked | Token requests fail cleanly | `AADSTS65001` — "DelegationDoesNotExist - The user or administrator hasn't consented to use the application with ID X" | The poller |
| App registration or service principal deleted | Token requests fail | `AADSTS700016` — "The application wasn't found in the directory/tenant"; possibly `AADSTS500011`, "The resource principal named {name} wasn't found in the tenant" | The poller |
| Client secret expires | Token requests fail. Guaranteed to happen eventually if you used a secret | `AADSTS7000222` — "The provided client secret keys are expired" | The poller |
| Mailbox deleted, or its licence removed | Mail stops arriving; the poller may still authenticate fine against an empty or missing mailbox | HTTP `403` from Graph — "The user does not have enough permission or does not have a required license" | Only a **volume** check catches this, not an auth check |
| Mailbox renamed (UPN change) | Depends whether the code addresses the mailbox by UPN or by immutable ID. Addressing by UPN breaks | `404` from Graph | The poller, if the UPN is config, not code |
| Exchange RBAC scope removed (if RBAC is used) | App authenticates fine, reads nothing | HTTP `403`; Microsoft notes scoping failures surface as access denied | Only a **volume** check catches this |
| A filter or rule silently diverts mail | Nothing errors at all | None | Only a volume check, or a supplier asking why nobody paid |

All the AADSTS descriptions above are quoted from
[Microsoft Entra authentication and authorization error codes](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes).
The HTTP 403 wording is from [Microsoft Graph errors](https://learn.microsoft.com/en-us/graph/errors).

### 7.2 The design that answers "who monitors it"

**Yes, there is one, and it is the client's own system doing the monitoring.** The poller is on the
client's box, so it is the natural watchdog. Three checks, in this order of importance:

1. **A liveness heartbeat, not an error check.** The daemon writes a timestamp on every successful
   poll. A separate systemd timer or cron job alerts if the last success is older than N minutes.
   This catches the failures that produce no error at all: scope silently removed, filter rule
   diverting mail, licence pulled, mailbox renamed. Error logging alone will never catch these,
   which is the weakness in every "we log exceptions" answer.
2. **A volume floor.** Expected messages per day, with an alert on zero for longer than the natural
   quiet period, and on a hard drop. Invoice mail has a rhythm; seven days at an 80% decline is a
   signal.
3. **An auth canary, classified.** On failure, show the client one sentence in plain language,
   chosen by matching the AADSTS code. Example mapping to ship with the product:

   - `AADSTS700082` / `AADSTS50173` / `AADSTS65001` → "The connection to `ap@client.com` needs to
     be re-approved. An administrator can fix this in about two minutes. Click here for steps."
   - `AADSTS53003` → "A sign-in policy is blocking the connection. Ask your administrator whether a
     Conditional Access policy was changed."
   - `AADSTS7000222` → "The application credential has expired and needs to be replaced."
   - `AADSTS700016` / `AADSTS500011` → "The application was removed from your directory. It needs to
     be re-created. Contact your support provider."
   - HTTP `403` with a healthy token → "Authentication is fine but the mailbox is not readable.
     Check that the mailbox licence is still assigned."

   The client never reads an AADSTS code. The code maps to an action.

**Where the alert should go is a design decision, and the safe answer is "not to us."** If alerts
route only to Office Inference, then the monitoring quietly reintroduces the dependency we are
trying to remove. Route to the client's own address, and copy us only while the retainer is
active. Put the alert-recipient configuration in the client's own configuration file, documented,
so they can change it after we leave.

**The failure we cannot fully solve.** If the client's only administrator leaves, the tenant
recovers through Microsoft support, not through us. The mitigation is a documented second
administrator and a written record of the app IDs and what they are for. That record is a
deliverable, not documentation debt.

**One more thing to watch for.** Conditional Access is the highest-probability silent killer,
because it is a policy a small business might enable for reasons unrelated to us, and because
Microsoft's own guidance treats MFA as incompatible with unattended flows: "If users need to use
multi-factor authentication (MFA) to log in to the application, they will be blocked instead"
([ROPC](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth-ropc)). Write the
service mailbox and the app into the Conditional Access exclusion, in writing, with a note saying
why, and make re-checking it part of any tenant change. This is a specific reason to prefer the
service-principal path over delegated if the client's security posture later hardens: "MFA does not
apply to Service Principals," from the same Microsoft page.

---

## 8. Runbook: what the client's administrator does

Written so it can be lifted into a document. Microsoft 365 route; Google route follows.

### 8.1 Setup, one time, about 20 minutes

1. **Create the mailbox.** If it does not already exist, create `ap@client.com` as a **regular
   user** with an Exchange Online licence. Do not use a shared mailbox: it has a system-generated
   password and Microsoft instructs you to keep sign-in blocked, which means the integration
   cannot sign in at all
   ([About shared mailboxes](https://learn.microsoft.com/en-us/microsoft-365/admin/email/about-shared-mailboxes)).
   Give it a clear display name, for example "AP Invoice Intake (do not sign in)".
2. **Register the application.** Entra admin center > *Applications > App registrations > New
   registration*. Name it after the system, for example "Office Inference Intake". Account type:
   single tenant. Redirect URI: Web, using the value in the installer's output.
3. **Add one permission.** *API permissions > Add a permission > Microsoft Graph > Delegated
   permissions*, and add **`Mail.Read`**. Then add `offline_access`. Do not add `Mail.ReadWrite`,
   `Mail.Send`, `Mail.Read` **as an application permission**, or `Mail.ReadBasic`. One delegated
   read permission is the whole requirement.
4. **Grant admin consent.** Click *Grant admin consent*. Confirm the permission shows a green
   check. Microsoft's warning about this step is worth reading once: "Granting admin consent on
   behalf of an organization is a sensitive operation, potentially allowing the application's
   publisher access to significant portions of your organization's data"
   ([Grant tenant-wide admin consent](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)).
   What you are approving here is read access to one mailbox.
5. **Create the credential.** *Certificates & secrets*. Prefer **Certificates** over a client
   secret, because a certificate has no silent expiry in the same way and is not a bearer string
   in a config file. If a secret is used, copy the **Value** (not the Secret ID), record the expiry
   date in your own calendar, and set the longest expiry offered.
6. **Record the identifiers.** From **Enterprise applications**, not App registrations, note the
   Application (client) ID and the Object ID. Microsoft warns the two pages show different values
   ([RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)).
7. **Complete the consent once, interactively**, as the service mailbox, so the refresh token can
   be minted. Hand the resulting credential to the installer, which stores it on the box.
8. **Add the app to any Conditional Access policy exclusion.** Ask whoever manages security
   policy. Sign the box up for a policy that requires MFA and the intake stops. See section 7.
9. **Give the vendor access to nothing.** The vendor needs no tenant access after the consent
   click and no access at all after installation. If temporary access is needed during the build,
   create a named guest account with Cloud Application Administrator and put a deletion date on it.

**What the client should receive in writing at this point:** the app name, client ID, object ID,
the single permission granted, the mailbox it reads, the name of the person who consented, and the
date. One page. This is the document that makes the 180-day audit retention window survivable.

### 8.2 Ongoing, essentially nothing

- **If the service keeps polling, the refresh token never expires.** Microsoft's default is 90 days
  for non-SPA scenarios, and "Refresh tokens replace themselves with a fresh token upon every use"
  ([Refresh tokens in the Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)).
  There is nothing to renew on a schedule.
- **If it stops for more than 90 days, it dies.** Expect `AADSTS700082`. Fix: sign in once more as
  the service mailbox and re-consent. Two minutes.
- **If you use a client secret, diarise the expiry.** This is the one predictable maintenance item.
  A certificate removes it.
- **Review the app once a year.** Entra > *Enterprise applications*: confirm the permission list is
  still `Mail.Read` delegated and nothing more. The prompt-and-consent failure mode, where an app
  quietly accrues permissions, takes ten seconds to check.
- **Do not delete the service mailbox** unless you intend to stop intake.
- **Do not remove its licence.** A mailbox without a licence stops working; Graph returns HTTP 403.

### 8.3 If it stops working

The installer's alert names the cause. Handle in this order:

1. Check the alert text. It will name one of: re-approval needed, credential expired, policy
   blocking, application removed, or mailbox not readable.
2. For re-approval: sign in as the service mailbox, re-run the consent link in the installer's
   output, paste the new credential into the configuration, restart the intake service.
3. For a policy block: check *Entra > Protection > Conditional Access* for a recently changed or
   newly enabled policy covering all users, and add the app to the exclusion.
4. For a removed application: it must be re-created, because app registrations cannot be moved or
   restored across tenants. Follow section 8.1 again.
5. If nothing in the alert matches, run the enumeration commands in section 6.2 and send the output
   to your support provider.

### 8.4 Google Workspace route, differences only

1. The client creates a **Google Cloud project owned by the organisation**, not a personal project.
2. OAuth consent screen **User type: Internal**. Google's exemption list states that an Internal
   Use app "will not be subject to the unverified app screen or the 100-user cap," provided "The
   project must be owned by the organization, and its OAuth Consent Screen must be configured for
   internal use"
   ([When is verification not needed](https://support.google.com/cloud/answer/13464323)).
3. OAuth client of type **Web application**, redirect URI from the installer.
4. Scope: `https://www.googleapis.com/auth/gmail.readonly` plus `offline_access`. Google classifies
   `gmail.readonly` as **restricted** and says storing restricted-scope data on a server requires a
   security assessment
   ([Choose Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)).
   **This is unresolved for Internal-only apps (section 2.2). Get Google's answer in writing before
   committing to the configuration.**
5. Do **not** use domain-wide delegation. Google's own note is that it grants access to all users'
   data and requires a super admin
   ([Control API access with domain-wide delegation](https://support.google.com/a/answer/162106)).
6. Revocation and review: Admin console > *Security > Access and data control > API controls >
   Manage App Access*. Requires the Service Settings administrator privilege, and app details may
   take 24–48 hours to appear after authorisation
   ([Control which apps access Google Workspace data](https://support.google.com/a/answer/7281227)).
7. Refresh-token caveat: Google documents that per-client-user and per-user token limits exist and
   that exceeding them stops older tokens working
   ([Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2)).
   Never re-consent in a retry loop; store the token once.

---

## 9. What I could not verify

Listed so the gaps are visible rather than papered over.

1. **Google security assessment for Internal-only restricted-scope apps.** Google's exemption page
   omits it; the Gmail scope page and the OAuth verification page do not carve it out. Design
   risk on the Google leg. Ask Google in writing.
2. **Google refresh-token limits and the 7-day Testing-mode expiry.** Widely cited, not sourced.
3. **Whether a Google OAuth client can be moved between projects or organisations.** I found
   project-move documentation, not client-move documentation, and I would not rely on it existing.
4. **A Microsoft Learn page stating app registrations cannot be transferred between tenants.** The
   Q&A answer is accepted by the author but is community content, not product documentation. The
   operational conclusion is safe; the citation is weaker than I would like.
5. **Microsoft Graph mail subscription maximum lifetime** (commonly quoted as ~4,230 minutes). The
   `subscription` resource page links to a "Subscription lifetime" reference that returns 404.
6. **Current pricing**: AWS Elastic IP and public IPv4 hourly charges, Azure and Google Cloud
   static IP pricing, Spamhaus Datafeed, Mailgun and other gateway tiers. Not fetched.
7. **US ISP inbound-port-25 policies and CGNAT prevalence.** No authoritative published source
   found for Comcast, Verizon, AT&T, Spectrum, Cox, or Frontier. This is the single most important
   unverified item for the on-the-box design, and the reason it must be a discovery question asked
   of the ISP rather than an assumption.
8. **Azure and Google Cloud inbound port 25 behaviour.** Outbound restrictions are well known;
   inbound is unverified.
9. **rspamd and ClamAV documented minimum resource requirements**, and any authoritative
   measurement of abusive-mail proportion at a public MX. Not fetched.
10. **The commercial on-premises capture vendor survey** (Kofax/Tungsten, M-Files, DocuWare,
    Laserfiche, Hyland). Gated or JavaScript-rendered pages. This is the gap most likely to hide a
    reusable procedure.
11. **Whether SES Mail Manager or SES email receiving can relay to an arbitrary external SMTP
    endpoint.** Relevant to a client-account-hosted gateway, which is categorically different from
    a vendor-operated one.
12. **GDAP mechanics.** Named as the correct partner-access pattern, not verified from Microsoft
    documentation in this pass.
13. **MFA-STS adoption rate**, which decides whether enforcing it costs legitimate senders.

---

## 10. One-page summary

**Build it this way.** Intake address stays where it is. Mailbox is a licensed regular user, never
a shared mailbox. App registration named and owned by the client in the client's own Entra ID.
One delegated Graph permission: `Mail.Read`. Client's admin grants consent once. Credential,
preferably a certificate, stored on the client's box. The poller runs on the client's box and writes
a heartbeat the client's own monitoring watches. Alerts go to the client's address, not ours.

**Do not build it this way.** A mailbox on `officeinference.com`. An app registration in our
tenant. A relay through anything we operate. Application permission `Mail.Read` without an Exchange
RBAC scope, because the Entra grant makes the scope a no-op. A shared mailbox with delegated auth,
because it cannot sign in. Domain-wide delegation on Google, because it grants tenant-wide
impersonation.

**Exit is nearly free.** Delete the guest account, rotate the credential, revoke the grant, run two
enumeration commands, export the output. Do it before the 180-day audit window closes out the
evidence.

**The residual dependency is Microsoft, and the client's own admin.** That is unavoidable and it is
materially different from depending on Office Inference, which is what the requirement asked to
remove.
