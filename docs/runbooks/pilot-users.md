# Pilot users: accounts, groups and removal

How a person gets an account on the pilot deployment, what their group means, and how to take the account away.
Cognito is the identity; the rendered access registry is the permission model. Both must name the person, or the
sign-in succeeds and the app shows nothing useful, or the registry is right and no one can sign in.

## Groups

- `staff` reads the `company` root only.
- `exec` reads `company` and `executive`.

The group is a property of the person, decided when the account is created. Nothing a person types in a request
changes it.

## Add a person

1. Create the Cognito account from the operator machine with the credits profile. This makes a temporary
   password; the first sign-in changes it.

   ```python
   import boto3, secrets
   cognito = boto3.client("cognito-idp", region_name="us-east-1")
   pool = "us-east-1_F21GZAkHU"
   email = "person@example.com"
   name = "Person Name"
   group = "staff"  # or exec
   password = "Oi-" + secrets.token_urlsafe(18) + "!9a"
   cognito.admin_create_user(
       UserPoolId=pool, Username=email,
       UserAttributes=[{"Name": "email", "Value": email},
                       {"Name": "email_verified", "Value": "true"},
                       {"Name": "name", "Value": name}],
       MessageAction="SUPPRESS")
   cognito.admin_set_user_password(UserPoolId=pool, Username=email, Password=password, Permanent=False)
   print(password)
   ```

   Run it with `AWS_PROFILE=oi-credits`. Keep the output in the operator's password store, not in a repository.

2. Add the person to the deployment's rendered registry on the box. Edit the manifest's access users and
   re-render, or append to `rendered/access.json` deliberately, then restart the API. The manifest is the source
   of truth for the next render.

   ```bash
   ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@100.60.208.224
   cd /opt/oi-product
   python3 - <<'PY'
   import json
   from pathlib import Path
   path = Path("rendered/access.json")
   document = json.loads(path.read_text())
   document["users"] = [u for u in document.get("users", []) if u.get("subject") != "person@example.com"]
   document["users"].append({"subject": "person@example.com", "name": "Person Name", "groups": ["staff"]})
   path.write_text(json.dumps(document, indent=2) + "\n")
   PY
   docker restart api
   ```

3. Have the person sign in at `https://app.officeinference.com` with the temporary password. Cognito shows the
   change-password page; the person chooses their own password and lands in the app. Verified on 2026-11-03
   with a temporary staff account (`docs/reviews/pilot-user-rehearsal-20261103/`). Confirm the deployment id
   is visible and Ask returns a cited answer.

4. Check the boundary. A `staff` person asking an executive-only question must be denied with a disclosure. An
   `exec` person may see executive content. Record the question and the result.

5. Confirm Activity shows the person's email as the actor for their actions.

## Reset a password

Set a new temporary password with `admin_set_user_password(..., Permanent=False)` using the same snippet as
step 1. Do not send passwords through chat; put them in the password store and tell the person out of band.

## Remove a person

1. Disable or delete the Cognito account: `admin_disable_user` for a pause, `admin_delete_user` for a departure.
2. Remove the identity from `rendered/access.json` and restart the API.
3. Leave the audit chain alone. The person's past actions stay in the record.

## Remove the demo and probe identities before business use

The rendered registry currently also contains `adam.hale@northwind.example`, `zoe.reyes@northwind.example`,
`probe-exec@officeinference.com` and `probe-staff@officeinference.com`. None has a Cognito account, so none can
sign in, but they are not part of the business and should be removed before the first real documents go in.
Remove them the same way as any other identity, then re-run the Phase B checks to confirm nothing in the
acceptance harness needed them.

## Rules

- One account per person. No shared logins.
- The owner account stays `exec`.
- Support never asks for a password. Reset it instead.
- Removing a person does not remove their audit history.
