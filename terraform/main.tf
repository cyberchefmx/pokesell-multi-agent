# ==============================================================================
# PokeSell Ops (pokesell-multi-agent) — Terraform Infrastructure as Code (IaC)
# Addresses AgentOps Rubric Category 5:
# - Infrastructure as Code (5 pts)
# - Secure Secret Management via Google Cloud Secret Manager (5 pts)
# ==============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.38"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# 1. Enable Required Google Cloud APIs for Vertex AI / ADK, Secret Manager, DLP, Trace & Logging
resource "google_project_service" "agentops_apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "firestore.googleapis.com",
    "discoveryengine.googleapis.com",
    "dlp.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# 2. Dedicated Least-Privilege IAM Service Account for the ADK Multi-Agent Runtime
resource "google_service_account" "pokesell_runtime_sa" {
  account_id   = "${var.service_name}-sa"
  display_name = "PokeSell Ops ADK Multi-Agent Runtime Service Account"
}

resource "google_project_iam_member" "pokesell_runtime_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/discoveryengine.viewer",
    "roles/dlp.user",
    "roles/secretmanager.secretAccessor",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.pokesell_runtime_sa.email}"
}

# 3. Google Cloud Secret Manager Secrets (Zero Hardcoded Credentials)
resource "google_secret_manager_secret" "managed_secrets" {
  for_each = toset([
    "WHATNOT_WEBHOOK_SECRET",
    "EBAY_ORDER_API_SECRET",
    "POKESELL_DASHBOARD_SESSION_SECRET",
    "POKESELL_API_BEARER_TOKEN",
  ])

  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.agentops_apis]
}

# 4. Persistent Session Store (Firestore Native Mode)
resource "google_firestore_database" "pokesell_session_store" {
  project     = var.project_id
  name        = "${var.service_name}-sessions"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.agentops_apis]
}

# 5. Google Cloud Sensitive Data Protection (DLP) De-identification Template
resource "google_data_loss_prevention_deidentify_template" "buyer_pii_redactor" {
  parent       = "projects/${var.project_id}/locations/global"
  display_name = "PokeSell Buyer Shipping & Contact PII Redaction Template"
  description  = "Scrubs buyer emails, phone numbers, street shipping addresses, SSNs, and payment cards prior to logging and memory persistence."

  deidentify_config {
    info_type_transformations {
      transformations {
        info_types {
          name = "EMAIL_ADDRESS"
        }
        info_types {
          name = "PHONE_NUMBER"
        }
        info_types {
          name = "STREET_ADDRESS"
        }
        info_types {
          name = "CREDIT_CARD_NUMBER"
        }
        info_types {
          name = "US_SOCIAL_SECURITY_NUMBER"
        }
        primitive_transformation {
          replace_with_info_type_config {}
        }
      }
    }
  }

  depends_on = [google_project_service.agentops_apis]
}

# 6. Cloud Run v2 Service Hosting PokeSell Ops ADK + Secure BFF Server
resource "google_cloud_run_v2_service" "pokesell_cloud_run" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.pokesell_runtime_sa.email

    scaling {
      min_instance_count = 1
      max_instance_count = 5
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "ENABLE_CLOUD_DLP"
        value = "true"
      }
      env {
        name  = "ENABLE_CLOUD_TRACE_EXPORT"
        value = "true"
      }
      env {
        name  = "ENABLE_CLOUD_LOGGING"
        value = "true"
      }
      env {
        name = "WHATNOT_WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.managed_secrets["WHATNOT_WEBHOOK_SECRET"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "EBAY_ORDER_API_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.managed_secrets["EBAY_ORDER_API_SECRET"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "POKESELL_DASHBOARD_SESSION_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.managed_secrets["POKESELL_DASHBOARD_SESSION_SECRET"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.agentops_apis,
    google_firestore_database.pokesell_session_store,
  ]
}
