# _template

Copy this directory to add a service. Fill in `service.yaml`, then register the service in the
three places the convention asks for:

1. `docs/service-inventory.md`, the list of what exists and which build it runs in.
2. `packs/catalog.yaml`, in the pack that should turn it on.
3. `deploy/office/compose/docker-compose.yml`, with a profile: `tools` for something that runs
   when called, as `audit` and `keys` do, and no profile for a long-running service.

Then copy `services/audit/` for the shape of a real one: a `Dockerfile`, the code, and a README
that says what it is for.
