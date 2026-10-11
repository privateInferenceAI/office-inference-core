# gateway (keys)

The administration side of the gateway: one virtual key per role, each scoped to that role's model
with a spending ceiling taken from the manifest.

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm keys                      # mint or update every role from the manifest
    ... run --rm keys --set-budget coder 0.000001   # cap one role now; a plain run restores it
    ... run --rm keys --probe analyst               # one small call as that role
    ... run --rm keys --revoke analyst              # delete the key at the gateway and forget it

Idempotent by alias, `deployment:role`, so re-running restores the manifest's policy rather than
minting new secrets. `--revoke` is the exception and the only thing here that removes anything: a key
whose role has been deleted from the manifest lives on at the gateway otherwise, and re-adding the
role name would bring it back.

`docs/manual.md` section 12 is the operational page; section 17 covers adding and removing roles.
