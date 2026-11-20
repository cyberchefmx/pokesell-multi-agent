variable "project_id" {
  description = "Google Cloud Project ID hosting PokeSell Ops."
  type        = string
}

variable "region" {
  description = "Primary GCP region for Vertex AI, Cloud Run, and Firestore."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Canonical Cloud Run and IAM service identifier."
  type        = string
  default     = "pokesell-multi-agent"
}

variable "container_image" {
  description = "Artifact Registry container image URI for PokeSell Ops."
  type        = string
  default     = "us-central1-docker.pkg.dev/example-project/pokesell/pokesell-multi-agent:latest"
}
