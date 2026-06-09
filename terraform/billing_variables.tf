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

variable "openai_api_key" {
  type        = string
  sensitive   = true
  description = "OpenAI API key for text-embedding-3-small embeddings and GPT-4o-mini chat completions."
}

variable "openai_embedding_model" {
  type        = string
  default     = "text-embedding-3-small"
  description = "OpenAI embedding model to use for vectorization."
}

variable "openai_chat_model" {
  type        = string
  default     = "gpt-4o-mini"
  description = "OpenAI chat completion model for AI cost analysis."
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
