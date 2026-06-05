output "frontend_url" {
  value       = "https://${azurerm_container_app.frontend.ingress[0].fqdn}"
  description = "The public URL of the RecoveryHub React frontend portal."
}

output "backend_url" {
  value       = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
  description = "The public URL of the RecoveryHub API service."
}
