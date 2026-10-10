# Cloud build

`terraform apply` against the client's own account, with credentials they grant us and can
revoke. Reads the client manifest to decide which modules to create.

Open question before this gets written in earnest: **containers on ECS, on EKS, or plain EC2
running the same compose file as the office build?** ECS is the least operational surface, EKS
is the most portable, and EC2 with compose is the closest to the office build, which keeps one
mental model. The parity suite result matters more than the choice.
