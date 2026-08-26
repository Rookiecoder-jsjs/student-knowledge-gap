use super::*;
use crate::plugins::PluginCapabilitySummary;
use pretty_assertions::assert_eq;

#[test]
fn explicit_plugin_instructions_use_manifest_namespace_for_skills() {
    let rendered = render_explicit_plugin_instructions(
        &PluginCapabilitySummary {
            config_name: "acme.tools@test".to_string(),
            display_name: "Acme Developer Tools".to_string(),
            plugin_namespace: Some("acme.tools".to_string()),
            has_skills: true,
            ..PluginCapabilitySummary::default()
        },
        &[],
    )
    .expect("skill capability should render");

    assert!(rendered.contains("Capabilities from the `Acme Developer Tools` plugin:"));
    assert!(rendered.contains("`acme.tools:`"));
    assert!(!rendered.contains("`Acme Developer Tools:`"));
}

#[test]
fn explicit_plugin_instructions_list_available_mcp_servers() {
    let rendered = render_explicit_plugin_instructions(
        &PluginCapabilitySummary {
            config_name: "sample@test".to_string(),
            display_name: "sample".to_string(),
            plugin_namespace: None,
            has_skills: true,
            ..PluginCapabilitySummary::default()
        },
        &["sample-docs".to_string()],
    )
    .expect("mcp capability should render");

    assert!(rendered.contains("`sample:`"));
    assert!(rendered.contains("- MCP servers from this plugin available in this session: `sample-docs`."));
}

#[test]
fn explicit_plugin_instructions_return_none_without_visible_capabilities() {
    let rendered = render_explicit_plugin_instructions(
        &PluginCapabilitySummary {
            config_name: "empty@test".to_string(),
            display_name: "empty".to_string(),
            plugin_namespace: None,
            has_skills: false,
            ..PluginCapabilitySummary::default()
        },
        &[],
    );
    assert_eq!(rendered, None);
}

#[test]
fn explicit_plugin_instructions_truncate_to_context_limit() {
    let rendered = render_explicit_plugin_instructions(
        &PluginCapabilitySummary {
            config_name: "large@test".to_string(),
            display_name: "large".to_string(),
            plugin_namespace: None,
            has_skills: true,
            ..PluginCapabilitySummary::default()
        },
        &(0..500).map(|i| format!("server-{i}")).collect::<Vec<_>>(),
    )
    .expect("large capability should render truncated");
    assert!(rendered.ends_with(TRUNCATED_PLUGIN_INSTRUCTIONS_SUFFIX));
    assert!(rendered.len() <= MAX_EXPLICIT_PLUGIN_INSTRUCTIONS_BYTES);
}
