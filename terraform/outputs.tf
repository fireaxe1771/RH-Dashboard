output "frontend_url" {
  value       = "https://${local.frontend_app_name}.${azurerm_container_app_environment.aca_env.default_domain}"
  description = "The public URL of the RecoveryHub React frontend portal."
}

output "backend_url" {
  value       = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
  description = "The public URL of the RecoveryHub API service."
}
