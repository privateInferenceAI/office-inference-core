# Services

One directory per service. A service is a container image plus a declaration. The declaration is
what the deploy tooling reads, so it must stay accurate.

## service.yaml fields

| Field | Meaning |
|---|---|
| `name` | Service id, matching the directory name and the inventory |
| `location` | `office`, `cloud`, or `either` |
| `image` | Registry and repository. Digest pinning happens at release time |
| `packs` | Which capability packs turn this service on |
| `depends_on` | Start order |
| `ports` | Internal ports. Nothing publishes to the host except ingress |
| `healthcheck` | How the deploy tooling decides the service is up |
| `config_schema` | The keys this service reads from the manifest |
| `substrate` | Where the implementation differs: office and cloud variants |
| `gpu` | Whether the service needs a GPU, and roughly how much memory |
| `data_class` | The highest class of data this service handles. Drives lane routing |

`data_class` is the one to get right. It is what the router consults before anything leaves
the box, so it is a security declaration rather than a description.
