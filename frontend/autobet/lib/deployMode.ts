export type DeployCapabilities = {
  deploy_mode: "prod" | "dev"
  training_allowed: boolean
  backtest_allowed: boolean
  reset_model_allowed: boolean
  db_reset_allowed: boolean
  model_export_allowed: boolean
  model_upload_allowed: boolean
  model_activate_allowed: boolean
  model_create_allowed: boolean
  archive_export_allowed: boolean
  archive_import_allowed: boolean
}

export const DEFAULT_CAPABILITIES: DeployCapabilities = {
  deploy_mode: (process.env.NEXT_PUBLIC_DEPLOY_MODE === "dev" ? "dev" : "prod") as "prod" | "dev",
  training_allowed: process.env.NEXT_PUBLIC_DEPLOY_MODE === "dev",
  backtest_allowed: process.env.NEXT_PUBLIC_DEPLOY_MODE === "dev",
  reset_model_allowed: process.env.NEXT_PUBLIC_DEPLOY_MODE === "dev",
  db_reset_allowed: process.env.NEXT_PUBLIC_DEPLOY_MODE === "dev",
  model_export_allowed: process.env.NEXT_PUBLIC_DEPLOY_MODE === "dev",
  model_upload_allowed: true,
  model_activate_allowed: true,
  model_create_allowed: process.env.NEXT_PUBLIC_DEPLOY_MODE === "dev",
  archive_export_allowed: true,
  archive_import_allowed: true,
}

export function isDevDeploy(caps: DeployCapabilities): boolean {
  return caps.deploy_mode === "dev"
}
