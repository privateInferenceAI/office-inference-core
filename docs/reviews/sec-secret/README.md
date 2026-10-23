# Secret rotation without a redeploy

**Date:** 2026-10-23. **Status:** SEC-SECRET-001 is verified.

## What is stored

The client connector token is in AWS Secrets Manager in badj account 426063972668:

`arn:aws:secretsmanager:us-east-1:426063972668:secret:oi-core/connector-token-GdnTVu`

The gateway reads its credential file on every call. The connector reads its token from an environment file
through systemd. Neither contains the token in an image or in the repository.

## Rotation

`scripts/rotate_connector_secret.py` fetches the current secret, writes the token file mode 600, rewrites
`connector.env` mode 600 and restarts the connector service. It does not rebuild or redeploy either service.

The token was changed from `client-connector-token` to `client-connector-token-2` in Secrets Manager, then
the script ran on the tenant box.

- Old token, direct connector call: 401.
- New token, direct connector call: 200.
- Job `4795a9935e1744a481410971e496e40b` after rotation: succeeded, audit row 1228 records `sent: true`.

## The rotation script

```
rotate_connector_secret.py --secret-id oi-core/connector-token \
    --token-file /home/ubuntu/client/connector.token \
    --env-file /home/ubuntu/client/connector.env --service client-connector
```

The first run failed because boto3 had no Region. The script now defaults to us-east-1 when `AWS_REGION` and
`AWS_DEFAULT_REGION` are both unset.

The first restart also failed with `Address already in use`: a manually started connector still held port
8094. The old process was stopped and the systemd service came up cleanly. That is an operator step the
script does not do.

## Files

- `rotation.json`: secret ARN, old and new token results, job and audit row.
