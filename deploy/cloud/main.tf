# Office Inference: cloud build
#
# Applies the core system into the CLIENT'S OWN cloud account.
# We never own the account and never pay the bill.
#
# Skeleton. The module structure is the decision worth making first:
#   - network      (VPC, subnets, endpoints, no public egress for data services)
#   - data         (RDS, S3 with Object Lock optional, KMS)
#   - runtime      (the containers, wherever we decide: ECS, EKS, or EC2 + compose)
#   - edge         (ALB, ACM, WAF)
#   - identity     (Cognito or the client's IdP)
#   - observability(CloudWatch, alarms, budget alerts)

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "client_name" { type = string }
variable "deployment_id" { type = string }
variable "manifest_path" {
  type        = string
  description = "Path to the client manifest. Modules read it to decide what to create."
}
variable "region" { type = string }

# TODO: locals that parse the manifest into feature flags, so modules are created only when the
# corresponding pack is present. Example shape:
#
# locals {
#   manifest = yamldecode(file(var.manifest_path))
#   packs    = toset(local.manifest.packs)
#   has      = { for p in local.packs : p => true }
# }

# module "network" { ... }
# module "data"    { ... }
# module "runtime" { ... }
# module "edge"    { ... }
