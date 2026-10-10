# Office Inference: cloud leg (hybrid only)
#
# The smallest possible footprint in the client's account. It exists to carry frontier model
# calls and hold backups. Deliberately NOT here: the document store, the vector database, and
# the chat interface.

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "client_name" { type = string }
variable "deployment_id" { type = string }
variable "region" { type = string }
variable "backup_bucket_name" { type = string }
variable "enable_frontier_path" {
  type        = bool
  default     = true
  description = "Creates the path used for frontier model calls. Model access itself is granted in the client's account."
}

# TODO:
#   - KMS key the CLIENT owns, used for backup encryption (they hold the key, we cannot read it)
#   - S3 bucket with Object Lock optional, versioning on, public access blocked
#   - VPC endpoint for Bedrock if the frontier path routes through their account
#   - IAM role we assume, scoped to this stack, revocable at any time
#   - Budget alert so an unexpected bill is noticed by them, not by us
