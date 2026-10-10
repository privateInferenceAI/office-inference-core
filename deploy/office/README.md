# Office build

The installer is the product on this side. A client without IT staff has to be able to run it,
and it has to be safe to run twice.

The office box is an appliance, not a server with containers on it. That means the installer is
responsible for the things a cloud provider would otherwise do: backups with restore drills,
signed updates with rollback, health reporting, log rotation, and a support path that requires
the client's consent.

Open question: Docker Compose or k3s. Compose is simpler to reason about and matches the current
build. k3s buys rolling updates and self-healing at the cost of a much bigger surface to support.
