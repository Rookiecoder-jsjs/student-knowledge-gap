use crate::plugins::PluginCapabilitySummary;
use codex_utils_string::take_bytes_at_char_boundary;

const MAX_EXPLICIT_PLUGIN_INSTRUCTIONS_BYTES: usize = 4 * 1024;
const TRUNCATED_PLUGIN_INSTRUCTIONS_SUFFIX: &str =
    "\n- Additional plugin capabilities omitted to fit the context limit.";

/// Renders the explicit mention instructions for one plugin: its skill prefix
/// and the MCP servers it contributes that are usable this turn.
///
/// apps(connectors) 已裁剪：不再携带 app 列表。
pub(crate) fn render_explicit_plugin_instructions(
    plugin: &PluginCapabilitySummary,
    available_mcp_servers: &[String],
) -> Option<String> {
    let mut lines = vec![format!(
        "Capabilities from the `{}` plugin:",
        plugin.display_name
    )];

    if plugin.has_skills {
        let skill_namespace = plugin
            .plugin_namespace
            .as_deref()
            .unwrap_or(plugin.display_name.as_str());
        lines.push(format!(
            "- Skills from this plugin are prefixed with `{skill_namespace}:`."
        ));
    }

    if !available_mcp_servers.is_empty() {
        lines.push(format!(
            "- MCP servers from this plugin available in this session: {}.",
            available_mcp_servers
                .iter()
                .map(|server| format!("`{server}`"))
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }

    if lines.len() == 1 {
        return None;
    }

    lines.push("Use these plugin-associated capabilities to help solve the task.".to_string());

    Some(bound_explicit_plugin_instructions(lines.join("\n")))
}

fn bound_explicit_plugin_instructions(rendered: String) -> String {
    if rendered.len() <= MAX_EXPLICIT_PLUGIN_INSTRUCTIONS_BYTES {
        return rendered;
    }

    let max_prefix_bytes = MAX_EXPLICIT_PLUGIN_INSTRUCTIONS_BYTES
        .saturating_sub(TRUNCATED_PLUGIN_INSTRUCTIONS_SUFFIX.len());
    let prefix = take_bytes_at_char_boundary(&rendered, max_prefix_bytes);
    format!("{prefix}{TRUNCATED_PLUGIN_INSTRUCTIONS_SUFFIX}")
}

#[cfg(test)]
#[path = "render_tests.rs"]
mod tests;
