variable "resource_group_name" {
  type        = string
  description = "The name of the Resource Group where dashboard app will be deployed."
}

variable "location" {
  type        = string
  default     = "eastus"
  description = "Azure region location to host services."
}

variable "environment_name" {
  type        = string
  default     = "rh-dashboard-env"
  description = "The name of the Azure Container Apps Environment."
}

# --- Azure Container Registry ---
variable "acr_name" {
  type        = string
  description = "The name of the existing Azure Container Registry (ACR) to pull built Docker images from."
}

variable "backend_image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag for the FastAPI backend container."
}

variable "frontend_image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag for the React frontend container."
}

# --- Database Credentials (Secrets) ---
variable "mongodb_uri" {
  type        = string
  sensitive   = true
  description = "MongoDB Connection String for application metadata storage."
}

variable "azure_sql_host" {
  type        = string
  description = "Target Azure SQL Database host address."
}

variable "azure_sql_db" {
  type        = string
  description = "Target Azure SQL Database name containing operational claims."
}

variable "azure_sql_user" {
  type        = string
  description = "Username for target SQL database."
}

variable "azure_sql_password" {
  type        = string
  sensitive   = true
  description = "Password credentials for target SQL database."
}

# --- Authentication ---
variable "azure_client_id" {
  type        = string
  description = "Azure Entra ID Client ID for MSAL authentication."
}

variable "azure_tenant_id" {
  type        = string
  description = "Azure Entra ID Tenant ID."
}
