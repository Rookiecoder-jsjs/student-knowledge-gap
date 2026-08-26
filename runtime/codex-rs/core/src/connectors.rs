use codex_config::types::ApprovalsReviewer;
use codex_mcp::CODEX_APPS_MCP_SERVER_NAME;

pub(crate) fn mcp_approvals_reviewer_from_layers(
    config_layer_stack: &codex_config::ConfigLayerStack,
    default_reviewer: ApprovalsReviewer,
    model: Option<&str>,
    server_name: &str,
    connector_id: Option<&str>,
) -> ApprovalsReviewer {
    let requirements = config_layer_stack.requirements();
    if model.is_some_and(|model| requirements.auto_review_required_for_model(model)) {
        return ApprovalsReviewer::AutoReview;
    }

    let app_reviewer = if server_name == CODEX_APPS_MCP_SERVER_NAME {
        apps_config_from_layer_stack(config_layer_stack).and_then(|apps_config| {
            connector_id
                .and_then(|connector_id| apps_config.apps.get(connector_id))
                .and_then(|app| app.approvals_reviewer)
                .or_else(|| {
                    apps_config
                        .default
                        .and_then(|defaults| defaults.approvals_reviewer)
                })
        })
    } else {
        None
    };

    if let Some(reviewer) = app_reviewer
        && requirements.approvals_reviewer.can_set(&reviewer).is_ok()
    {
        return reviewer;
    }

    default_reviewer
}

fn apps_config_from_layer_stack(
    config_layer_stack: &codex_config::ConfigLayerStack,
) -> Option<codex_config::types::AppsConfigToml> {
    use serde::Deserialize as _;

    config_layer_stack
        .effective_config()
        .as_table()
        .and_then(|table| table.get("apps"))
        .cloned()
        .and_then(|value| codex_config::types::AppsConfigToml::deserialize(value).ok())
}
