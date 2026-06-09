# --- Azure Billing Integration Variables ---

variable "azure_billing_client_id" {
  type        = string
  description = "Azure Entra ID Application (client) ID for the billing service principal."
}

variable "azure_billing_client_secret" {
  type        = string
  sensitive   = true
  description = "Client secret for the billing service principal. Never logged or printed."
}

variable "azure_subscription_id" {
  type        = string
  description = "Azure subscription ID to query for cost and usage data."
}

variable "azure_billing_account_id" {
  type        = string
  description = "Azure billing account ID (numeric string for EA, UUID for MCA)."
}

variable "azure_billing_account_type" {
  type        = string
  default     = "MOSP"
  description = "Billing account agreement type: EA, MCA, or MOSP."
  validation {
    condition     = contains(["EA", "MCA", "MOSP"], var.azure_billing_account_type)
    error_message = "azure_billing_account_type must be EA, MCA, or MOSP."
  }
}

variable "azure_management_group_id" {
  type        = string
  default     = ""
  description = "Optional Azure Management Group ID for multi-subscription cost queries."
}

# --- AI / Embeddings Variables ---
# Two providers are supported. When azure_openai_endpoint is set the app uses
# Azure OpenAI (Foundry) and openai_embedding_model / openai_chat_model are the
# Azure *deployment* names; otherwise it falls back to OpenAI.com via openai_api_key.

variable "openai_api_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "OpenAI.com API key. Leave blank when using Azure OpenAI (Foundry)."
}

variable "azure_openai_endpoint" {
  type        = string
  default     = ""
  description = "Azure OpenAI (Foundry) resource endpoint, e.g. https://<resource>.openai.azure.com/. When set, the app uses Azure OpenAI instead of OpenAI.com."
}

variable "azure_openai_api_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "API key for the Azure OpenAI (Foundry) resource. Never logged or printed."
}

variable "azure_openai_api_version" {
  type        = string
  default     = "2024-10-21"
  description = "Azure OpenAI REST API version."
}

variable "openai_embedding_model" {
  type        = string
  default     = "text-embedding-3-small"
  description = "Embedding model (OpenAI.com) or deployment name (Azure OpenAI) used for vectorization. Must output 1536 dims to match the Atlas vector index."
}

variable "openai_chat_model" {
  type        = string
  default     = "gpt-5.4-mini"
  description = "Chat model (OpenAI.com) or deployment name (Azure OpenAI) for AI cost analysis."
}

# --- Billing Sync Configuration Variables ---

variable "billing_sync_enabled" {
  type        = string
  default     = "true"
  description = "Enable or disable the billing sync scheduler. Set to 'false' to disable."
}

variable "billing_daily_sync_hour" {
  type        = string
  default     = "2"
  description = "UTC hour (0-23) at which the daily billing sync runs."
}

variable "billing_history_months" {
  type        = string
  default     = "12"
  description = "Number of months of historical billing data to backfill on first run."
}
