output "shared_vnet_id" { value = module.network.vnet_id }
output "aca_subnet_id" { value = module.network.subnet_ids["aca"] }
output "private_endpoint_subnet_id" { value = module.network.subnet_ids["private_endpoints"] }
output "private_dns_zone_ids" { value = module.network.private_dns_zone_ids }
output "deployment_client_id" {
  value     = module.deployment_identity.workloads["central-deployment"].client_id
  sensitive = true
}
