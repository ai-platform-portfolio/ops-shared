terraform {
  required_version = ">= 1.5.0"
}
variable "message" {
  type    = string
  default = "shared-workflow-fixture"
}
output "message" {
  value = var.message
}
