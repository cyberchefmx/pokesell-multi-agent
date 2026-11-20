output "cloud_run_service_uri" {
  description = "Deployed URL of the PokeSell Ops Cloud Run v2 service."
  value       = google_cloud_run_v2_service.pokesell_cloud_run.uri
}

output "runtime_service_account_email" {
  description = "Least-privilege IAM Service Account used by the ADK runtime."
  value       = google_service_account.pokesell_runtime_sa.email
}

output "dlp_deidentify_template_id" {
  description = "Google Cloud Sensitive Data Protection template resource ID."
  value       = google_data_loss_prevention_deidentify_template.buyer_pii_redactor.id
}
