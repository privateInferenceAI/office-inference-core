# 0010. The cloud workspace sandbox is a container per task on a dedicated host

- Status: proposed, awaiting owner acceptance
- Phase: WS
- Date: 2026-10-23

## Context

The Cloud Workspace target gives an agent a task-scoped environment: a file workspace, shell and code execution,
and an artifact it can hand back. The environment must not read the deployment's code, rendered environment,
assertion key or another task's files. It must be destroyed when the task ends. The task itself has CPU, memory,
disk, wall-clock and token limits.

The cloud build runs one EC2 instance with Docker Compose. Three substrates were considered:

1. **bubblewrap on the host.** Available on the development machine, unprivileged user namespaces, no Docker
   socket. It is a good local proof, but a container image cannot reliably create the namespaces it needs under
   the default Docker seccomp profile, so the deployment would need the workspace process on the host rather than
   in the composed stack.
2. **A container per task, launched by a workspace service on a dedicated host.** The workspace image is pinned
   like every other image. The service mounts only the task directory, sets network none, drops capabilities,
   applies limits and removes the container and directory when the task ends. The Docker socket lives on the
   workspace host, not in the API.
3. **gVisor or Firecracker.** Stronger isolation, more operational surface. gVisor is a runc-compatible runtime
   with a user-space kernel. Firecracker needs a microVM manager and a separate network and storage path. Both
   are reasonable next steps, not the first build.

## Decision

The cloud build uses **one container per task, launched by a dedicated workspace host**. The API and the runtime
never hold the Docker socket. The workspace host is in the client's account, with its own IAM role and security
group, and only the workspace service listens on it.

Each task runs from a pinned `workspace` image with:

- `--network none` for the first build. Approved web access arrives through the egress proxy, not from the
  sandbox's own network.
- only the task directory mounted, at `/workspace`, read-write. The host code, rendered directory, assertion key,
  database and other task directories are not mounted.
- the host user id, no root in the container, `--cap-drop ALL`, `no-new-privileges`, a read-only root with a
  tmpfs `/tmp`.
- memory, CPU, pid and wall-clock limits. The task directory size is checked before and after.
- no credentials. A tool that needs one is called by the runtime through the gateway, not from inside the
  sandbox.
- removal of the container and the task directory on completion, cancellation or failure. Artifacts are copied
  out before removal.

The office build does not get this layer. It refuses the workspace pack in the manifest validator.

## Consequences

The deployment gains a second host and a second security boundary. That is the point: the Docker socket is a
root-equivalent capability, and it must not sit beside the API that holds the signing key.

The task image becomes part of the supply chain. It is digest-pinned, scanned and updated like the other images.
A task that needs a tool not in the image fails with a typed cause rather than reaching the internet to install
it.

The first build has no network in the sandbox. The web tool and connectors are separate runtime tools that call
the approved egress path; the sandbox cannot bypass them.

## Open decisions

- The workspace host is a second EC2 instance in the first cloud deployment. Whether it becomes an ASG, a
  separate VPC subnet or a managed container service is a Terraform decision for the WS build.
- gVisor is the candidate for the first isolation upgrade. It should be measured against the container runtime
  on the same tasks before it is adopted.
- Artifact storage on the first build is the client's S3 bucket. Local disk is the fallback for a development
  deployment.
