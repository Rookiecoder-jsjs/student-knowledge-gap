use anyhow::Result;
use core_test_support::apps_test_server::SEARCH_CALENDAR_NAMESPACE;
use core_test_support::apps_test_server::AppsTestServer;
use codex_config::Constrained;
use codex_core::EnvironmentConfig;
use codex_core::TurnInputRequest;
use codex_core::config::Config;
use codex_extension_api::ExtensionFuture;
use codex_extension_api::ExtensionRegistryBuilder;
use codex_extension_api::McpServerContribution;
use codex_extension_api::McpServerContributionContext;
use codex_extension_api::McpServerContributor;
use codex_extension_api::ThreadLifecycleContributor;
use codex_extension_api::ThreadStartInput;
use codex_features::Feature;
use codex_history::RolloutItem;
use codex_history::RolloutLine;
use codex_mcp::CODEX_APPS_MCP_SERVER_NAME;
use codex_mcp::McpResourceClient;
use codex_protocol::capabilities::CapabilityRootLocation;
use codex_protocol::capabilities::SelectedCapabilityRoot;
use codex_protocol::models::PermissionProfile;
use codex_protocol::models::PermissionProfileSnapshot;
use codex_protocol::protocol::AskForApproval;
use codex_protocol::protocol::EventMsg;
use codex_protocol::protocol::Op;
use codex_protocol::protocol::SessionSource;
use codex_protocol::protocol::SubAgentSource;
use codex_protocol::request_user_input::RequestUserInputAnswer;
use codex_protocol::request_user_input::RequestUserInputResponse;
use codex_protocol::user_input::UserInput;
use core_test_support::context_snapshot;
use core_test_support::context_snapshot::ContextSnapshotOptions;
use core_test_support::context_snapshot::ContextSnapshotRenderMode;
use core_test_support::responses;
use core_test_support::responses::ResponsesRequest;
use core_test_support::responses::ev_assistant_message;
use core_test_support::responses::ev_completed;
use core_test_support::responses::ev_function_call;
use core_test_support::responses::ev_function_call_with_namespace;
use core_test_support::responses::ev_response_created;
use core_test_support::responses::mount_sse_once_match;
use core_test_support::responses::mount_sse_sequence;
use core_test_support::responses::namespace_child_tool;
use core_test_support::responses::sse;
use core_test_support::skip_if_no_network;
use core_test_support::wait_for_event;
use core_test_support::wait_for_mcp_server;
use core_test_support::apps_test_server::search_capable_apps_builder;
use pretty_assertions::assert_eq;
use rmcp::model::ReadResourceRequestParams;
use serde::Deserialize;
use serde_json::Value;
use serde_json::json;
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tempfile::TempDir;
use tokio::sync::Semaphore;
use wiremock::Mock;
use wiremock::Request;
use wiremock::ResponseTemplate;
use wiremock::matchers::body_partial_json;
use wiremock::matchers::method;
use wiremock::matchers::path_regex;

struct McpResourceClientCapture {
    client: Arc<Mutex<Option<McpResourceClient>>>,
}

struct CoalescingMcpContributor {
    block_next: AtomicBool,
    entered: Semaphore,
    release: Semaphore,
    observed_markers: Mutex<Vec<String>>,
}

struct AppsMcpServerContributor {
    id: &'static str,
    url: String,
    root_resolved: Option<Arc<Semaphore>>,
}

struct SessionSourceMcpContributor {
    observed_sources: Arc<Mutex<Vec<SessionSource>>>,
}

impl CoalescingMcpContributor {
    fn new() -> Self {
        Self {
            block_next: AtomicBool::new(false),
            entered: Semaphore::new(0),
            release: Semaphore::new(0),
            observed_markers: Mutex::new(Vec::new()),
        }
    }
}

impl McpServerContributor<Config> for CoalescingMcpContributor {
    fn id(&self) -> &'static str {
        "coalescing_mcp_refresh_test"
    }

    fn contribute<'a>(
        &'a self,
        context: McpServerContributionContext<'a, Config>,
    ) -> ExtensionFuture<'a, Vec<McpServerContribution>> {
        Box::pin(async move {
            let marker = context
                .config()
                .mcp_servers
                .get()
                .keys()
                .next()
                .cloned()
                .unwrap_or_else(|| "initial".to_string());
            self.observed_markers
                .lock()
                .expect("observed markers lock should not be poisoned")
                .push(marker.clone());
            if marker != "initial" {
                self.entered.add_permits(1);
            }
            if self.block_next.swap(false, Ordering::SeqCst) {
                self.release
                    .acquire()
                    .await
                    .expect("release semaphore should remain open")
                    .forget();
            }
            Vec::new()
        })
    }
}

impl McpServerContributor<Config> for AppsMcpServerContributor {
    fn id(&self) -> &'static str {
        self.id
    }

    fn contribute<'a>(
        &'a self,
        context: McpServerContributionContext<'a, Config>,
    ) -> ExtensionFuture<'a, Vec<McpServerContribution>> {
        Box::pin(async move {
            if context
                .ready_selected_capability_roots()
                .is_some_and(|roots| !roots.is_empty())
                && let Some(root_resolved) = &self.root_resolved
            {
                root_resolved.add_permits(1);
            }
            let config = Box::new(
                serde_json::from_value(json!({ "url": self.url }))
                    .expect("test Apps MCP server config should be valid"),
            );
            let contribution = if self.id == "hosted_plugin_runtime" {
                McpServerContribution::HostedApps { config }
            } else {
                McpServerContribution::Set {
                    name: CODEX_APPS_MCP_SERVER_NAME.to_string(),
                    config,
                }
            };
            vec![contribution]
        })
    }
}

impl McpServerContributor<Config> for SessionSourceMcpContributor {
    fn id(&self) -> &'static str {
        "session_source_mcp_test"
    }

    fn contribute<'a>(
        &'a self,
        context: McpServerContributionContext<'a, Config>,
    ) -> ExtensionFuture<'a, Vec<McpServerContribution>> {
        Box::pin(async move {
            self.observed_sources
                .lock()
                .expect("observed sources lock should not be poisoned")
                .push(
                    context
                        .session_source()
                        .expect("thread-scoped MCP resolution should identify its session source")
                        .clone(),
                );
            Vec::new()
        })
    }
}

fn format_labeled_requests_snapshot(
    scenario: &str,
    sections: &[(&str, &ResponsesRequest)],
) -> String {
    context_snapshot::format_labeled_requests_snapshot(
        scenario,
        sections,
        &ContextSnapshotOptions::default()
            .strip_capability_instructions()
            .render_mode(ContextSnapshotRenderMode::KindWithTextPrefix { max_chars: 96 }),
    )
}

fn enable_deferred_tool_world_state_without_agents(config: &mut Config) {
    config.agents_enabled = false;
    config
        .features
        .enable(Feature::DeferredToolWorldState)
        .expect("test config should allow feature update");
}

fn tools_state_sections(request: &ResponsesRequest) -> Vec<String> {
    request
        .message_input_texts("developer")
        .into_iter()
        .filter(|text| text.starts_with("<tools>"))
        .collect()
}

fn completed_response_sequence(count: usize) -> Vec<String> {
    (1..=count)
        .map(|index| {
            sse(vec![
                ev_response_created(&format!("resp-{index}")),
                ev_assistant_message(&format!("msg-{index}"), "done"),
                ev_completed(&format!("resp-{index}")),
            ])
        })
        .collect()
}

impl ThreadLifecycleContributor<Config> for McpResourceClientCapture {
    fn on_thread_start<'a>(
        &'a self,
        input: ThreadStartInput<'a, Config>,
    ) -> ExtensionFuture<'a, ()> {
        Box::pin(async move {
            let client = input
                .mcp_resource_client
                .as_ref()
                .expect("host should supply an MCP resource client");
            *self
                .client
                .lock()
                .expect("capture lock should not be poisoned") = Some(client.as_ref().clone());
        })
    }
}

fn config_with_mcp_marker(base: &Config, marker: &str) -> Config {
    let mut config = base.clone();
    let server = serde_json::from_value(json!({
        "url": "http://127.0.0.1:1/mcp",
        "enabled": false,
    }))
    .expect("test MCP server config");
    config
        .mcp_servers
        .set(HashMap::from([(marker.to_string(), server)]))
        .expect("test config should allow MCP servers");
    config
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn root_and_spawned_subagent_receive_distinct_mcp_session_sources() -> Result<()> {
    skip_if_no_network!(Ok(()));

    const PARENT_PROMPT: &str = "spawn an agent to verify its MCP session source";
    const CHILD_PROMPT: &str = "child: report that MCP configuration completed";
    const SPAWN_CALL_ID: &str = "mcp-session-source-spawn";

    let server = responses::start_mock_server().await;
    let spawn_args = serde_json::to_string(&json!({ "message": CHILD_PROMPT }))?;
    mount_sse_once_match(
        &server,
        |request: &Request| {
            std::str::from_utf8(&request.body).is_ok_and(|body| body.contains(PARENT_PROMPT))
                && !request.headers.contains_key("x-openai-subagent")
        },
        sse(vec![
            ev_response_created("resp-parent-spawn"),
            ev_function_call_with_namespace(
                SPAWN_CALL_ID,
                "multi_agent_v1",
                "spawn_agent",
                &spawn_args,
            ),
            ev_completed("resp-parent-spawn"),
        ]),
    )
    .await;
    let child_response = mount_sse_once_match(
        &server,
        |request: &Request| {
            std::str::from_utf8(&request.body).is_ok_and(|body| body.contains(CHILD_PROMPT))
                && request
                    .headers
                    .get("x-openai-subagent")
                    .and_then(|value| value.to_str().ok())
                    == Some("collab_spawn")
        },
        sse(vec![
            ev_response_created("resp-child"),
            ev_assistant_message("msg-child", "child done"),
            ev_completed("resp-child"),
        ]),
    )
    .await;
    mount_sse_once_match(
        &server,
        |request: &Request| {
            std::str::from_utf8(&request.body).is_ok_and(|body| body.contains(SPAWN_CALL_ID))
                && !request.headers.contains_key("x-openai-subagent")
        },
        sse(vec![
            ev_response_created("resp-parent-complete"),
            ev_assistant_message("msg-parent", "parent done"),
            ev_completed("resp-parent-complete"),
        ]),
    )
    .await;

    let observed_sources = Arc::new(Mutex::new(Vec::new()));
    let mut extensions = ExtensionRegistryBuilder::<Config>::new();
    extensions.mcp_server_contributor(Arc::new(SessionSourceMcpContributor {
        observed_sources: observed_sources.clone(),
    }));
    let test = core_test_support::test_codex::test_codex()
        .with_extensions(Arc::new(extensions.build()))
        .build(&server)
        .await?;

    test.submit_turn(PARENT_PROMPT).await?;
    tokio::time::timeout(Duration::from_secs(/*secs*/ 10), async {
        while child_response.requests().is_empty() {
            tokio::time::sleep(Duration::from_millis(/*millis*/ 10)).await;
        }
    })
    .await?;

    let observed_sources = observed_sources
        .lock()
        .expect("observed sources lock should not be poisoned");
    assert!(observed_sources.contains(&SessionSource::Exec));
    assert!(observed_sources.iter().any(|source| matches!(
        source,
        SessionSource::SubAgent(SubAgentSource::ThreadSpawn {
            parent_thread_id,
            ..
        }) if *parent_thread_id == test.session_configured.thread_id
    )));

    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn rapid_mcp_refreshes_coalesce_to_the_latest_config() -> Result<()> {
    let server = responses::start_mock_server().await;
    let response = responses::mount_sse_once(
        &server,
        sse(vec![
            ev_response_created("resp-1"),
            ev_assistant_message("msg-1", "done"),
            ev_completed("resp-1"),
        ]),
    )
    .await;
    let contributor = Arc::new(CoalescingMcpContributor::new());
    let mut extensions = ExtensionRegistryBuilder::<Config>::new();
    extensions.mcp_server_contributor(contributor.clone());
    let test = core_test_support::test_codex::test_codex()
        .with_extensions(Arc::new(extensions.build()))
        .build(&server)
        .await?;

    contributor.block_next.store(true, Ordering::SeqCst);
    test.codex
        .refresh_runtime_config(config_with_mcp_marker(&test.config, "config-a"))
        .await;
    tokio::time::timeout(Duration::from_secs(5), contributor.entered.acquire())
        .await
        .expect("configuration A should enter MCP projection")
        .expect("entered semaphore should remain open")
        .forget();

    test.codex
        .refresh_runtime_config(config_with_mcp_marker(&test.config, "config-b"))
        .await;
    test.codex
        .refresh_runtime_config(config_with_mcp_marker(&test.config, "config-c"))
        .await;
    contributor.release.add_permits(1);

    tokio::time::timeout(Duration::from_secs(5), contributor.entered.acquire())
        .await
        .expect("the coalesced refresh should project the latest configuration")
        .expect("entered semaphore should remain open")
        .forget();
    let observed = contributor
        .observed_markers
        .lock()
        .expect("observed markers lock should not be poisoned")
        .iter()
        .filter(|marker| marker.as_str() != "initial")
        .cloned()
        .collect::<Vec<_>>();
    assert_eq!(
        observed,
        vec!["config-a".to_string(), "config-c".to_string()]
    );

    test.submit_turn("bind the latest MCP state").await?;
    assert!(
        !contributor
            .observed_markers
            .lock()
            .expect("observed markers lock should not be poisoned")
            .iter()
            .any(|marker| marker == "config-b")
    );
    response.single_request();
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn out_of_band_resource_read_reconciles_the_published_mcp_runtime() -> Result<()> {
    let server = responses::start_mock_server().await;

    let captured_client = Arc::new(Mutex::new(None));
    let mut extensions = ExtensionRegistryBuilder::<Config>::new();
    extensions.thread_lifecycle_contributor(Arc::new(McpResourceClientCapture {
        client: Arc::clone(&captured_client),
    }));
    let test = core_test_support::test_codex::test_codex()
        .with_extensions(Arc::new(extensions.build()))
        .build(&server)
        .await?;
    let resource_client = captured_client
        .lock()
        .expect("capture lock should not be poisoned")
        .clone()
        .expect("thread start should capture the MCP resource client");
    assert!(!resource_client.has_server("refreshed").await);

    let mut refresh_config = test.config.clone();
    let user_config_path = refresh_config.codex_home.join("config.toml");
    let user_config: toml::Value = toml::from_str(&format!(
        r#"
[mcp_servers.refreshed]
url = "{}/mcp"
startup_timeout_sec = 0.1
"#,
        server.uri()
    ))?;
    let refreshed_servers = user_config
        .get("mcp_servers")
        .cloned()
        .map(HashMap::<String, codex_config::types::McpServerConfig>::deserialize)
        .transpose()?
        .expect("test config should define MCP servers");
    refresh_config
        .mcp_servers
        .set(refreshed_servers)
        .expect("test config should allow MCP servers");
    refresh_config.config_layer_stack = refresh_config
        .config_layer_stack
        .with_user_config(&user_config_path, user_config)?;
    test.codex.refresh_runtime_config(refresh_config).await;
    test.codex.submit(Op::RefreshMcpServers).await?;

    let _ = test
        .codex
        .read_mcp_resource(
            "refreshed",
            ReadResourceRequestParams::new("test://resource"),
        )
        .await;
    assert!(resource_client.has_server("refreshed").await);
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn elevated_apps_catalog_limit_requires_host_owned_registration() -> Result<()> {
    skip_if_no_network!(Ok(()));

    let codex_home = Arc::new(TempDir::new()?);
    for extension_id in [None, Some("hosted_plugin_runtime"), Some("test-extension")] {
        let server = responses::start_mock_server().await;
        let apps_server = AppsTestServer::mount_searchable(&server).await?;
        let tools = Arc::new(
            (0..2_603)
                .map(|index| {
                    json!({
                        "name": format!("calendar_catalog_tool_{index}"),
                        "description": format!("Read calendar catalog entry {index}."),
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": false,
                        },
                        "_meta": {
                            "connector_id": "calendar",
                            "connector_name": "Calendar",
                            "connector_description": "Plan events and manage your calendar.",
                        },
                    })
                })
                .collect::<Vec<_>>(),
        );
        Mock::given(method("POST"))
            .and(path_regex("^/api/codex/ps/mcp/?$"))
            .and(body_partial_json(json!({ "method": "tools/list" })))
            .respond_with(move |request: &Request| {
                let body: Value = serde_json::from_slice(&request.body)
                    .expect("Apps tools/list should be a valid JSON-RPC request");
                ResponseTemplate::new(200).set_body_json(json!({
                    "jsonrpc": "2.0",
                    "id": body.get("id").cloned().unwrap_or(Value::Null),
                    "result": {
                        "tools": tools.as_ref(),
                    },
                }))
            })
            .with_priority(1)
            .mount(&server)
            .await;

        let mut builder = search_capable_apps_builder(apps_server.chatgpt_base_url.clone())
            .with_home(Arc::clone(&codex_home));
        if let Some(id) = extension_id {
            let mut extensions = ExtensionRegistryBuilder::new();
            extensions.mcp_server_contributor(Arc::new(AppsMcpServerContributor {
                id,
                url: format!("{}/api/codex/ps/mcp", apps_server.chatgpt_base_url),
                root_resolved: None,
            }));
            builder = builder.with_extensions(Arc::new(extensions.build()));
        }
        let test = builder.build_with_auto_env(&server).await?;
        let startup = wait_for_mcp_server(&test.codex, CODEX_APPS_MCP_SERVER_NAME).await;

        if extension_id == Some("test-extension") {
            let error = startup.expect_err("an extension must retain the standard catalog limit");
            assert!(
                error.to_string().contains("catalog limit of 2048 items"),
                "an extension named codex_apps must not inherit the trusted Apps limit: {error}"
            );
            continue;
        }

        startup?;
        let response = responses::mount_sse_once(
            &server,
            sse(vec![
                ev_response_created("resp-1"),
                ev_assistant_message("msg-1", "done"),
                ev_completed("resp-1"),
            ]),
        )
        .await;
        test.submit_turn("inspect the large Apps tool catalog")
            .await?;
        let body = response.single_request().body_json();
        let description = body["tools"]
            .as_array()
            .and_then(|tools| {
                tools.iter().find_map(|tool| {
                    (tool.get("type").and_then(Value::as_str) == Some("tool_search"))
                        .then(|| tool.get("description").and_then(Value::as_str))
                        .flatten()
                })
            })
            .expect("large Apps catalogs should remain discoverable through tool_search");
        assert!(
            description.contains("Calendar"),
            "the accepted Apps catalog should remain model-discoverable: {description}"
        );
        test.codex.shutdown_and_wait().await?;
    }

    Ok(())
}


#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn deferred_tool_world_state_is_disabled_by_default() -> Result<()> {
    skip_if_no_network!(Ok(()));

    let server = responses::start_mock_server().await;
    let apps_server = AppsTestServer::mount_searchable(&server).await?;
    let response = responses::mount_sse_once(
        &server,
        sse(vec![
            ev_response_created("resp-1"),
            ev_assistant_message("msg-1", "done"),
            ev_completed("resp-1"),
        ]),
    )
    .await;

    let mut builder = search_capable_apps_builder(apps_server.chatgpt_base_url.clone());
    let test = builder.build(&server).await?;
    test.submit_turn("inspect deferred MCP tools").await?;

    let request = response.single_request();
    assert!(
        request
            .message_input_texts("developer")
            .into_iter()
            .all(|text| !text.contains("<tools>")),
        "deferred tool world state should not be injected unless its feature is enabled"
    );
    assert!(
        request.body_json()["tools"]
            .as_array()
            .is_some_and(|tools| {
                tools
                    .iter()
                    .any(|tool| tool.get("type").and_then(Value::as_str) == Some("tool_search"))
            }),
        "disabling tool world state must not disable deferred tool discovery"
    );

    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn initially_empty_deferred_tool_world_state_is_not_rendered_or_persisted() -> Result<()> {
    skip_if_no_network!(Ok(()));

    let server = responses::start_mock_server().await;
    let response = mount_sse_sequence(&server, completed_response_sequence(/*count*/ 1)).await;
    let mut builder = core_test_support::test_codex::test_codex()
        .with_config(enable_deferred_tool_world_state_without_agents);
    let test = builder.build_with_auto_env(&server).await?;
    test.submit_turn("inspect empty deferred tools").await?;

    let request = response.single_request();
    assert!(tools_state_sections(&request).is_empty());
    test.codex.ensure_rollout_materialized().await;
    test.codex.flush_rollout().await?;
    let rollout_path = test.codex.rollout_path().expect("rollout path");
    let world_states = tokio::fs::read_to_string(rollout_path)
        .await?
        .lines()
        .map(serde_json::from_str::<RolloutLine>)
        .collect::<serde_json::Result<Vec<_>>>()?
        .into_iter()
        .filter_map(|line| match line.item {
            RolloutItem::WorldState(item) => Some(item.state),
            _ => None,
        })
        .collect::<Vec<_>>();
    assert!(!world_states.is_empty());
    assert!(
        world_states
            .iter()
            .all(|state| state.get("tools").is_none())
    );

    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn deferred_tool_world_state_survives_resume_without_duplicate_updates() -> Result<()> {
    skip_if_no_network!(Ok(()));

    let server = responses::start_mock_server().await;
    let apps_server = AppsTestServer::mount_searchable(&server).await?;
    let response = mount_sse_sequence(&server, completed_response_sequence(/*count*/ 2)).await;
    let mut builder = search_capable_apps_builder(apps_server.chatgpt_base_url.clone())
        .with_config(enable_deferred_tool_world_state_without_agents);
    let initial = builder.build_with_auto_env(&server).await?;
    wait_for_mcp_server(&initial.codex, CODEX_APPS_MCP_SERVER_NAME).await?;
    initial
        .submit_turn("inspect deferred tools before resume")
        .await?;

    initial.codex.ensure_rollout_materialized().await;
    initial.codex.flush_rollout().await?;
    let rollout_path = initial
        .session_configured
        .rollout_path
        .clone()
        .expect("rollout path");
    let persisted_tools = tokio::fs::read_to_string(&rollout_path)
        .await?
        .lines()
        .map(serde_json::from_str::<RolloutLine>)
        .collect::<serde_json::Result<Vec<_>>>()?
        .into_iter()
        .filter_map(|line| match line.item {
            RolloutItem::WorldState(item) => item.state.get("tools").cloned(),
            _ => None,
        })
        .next_back()
        .expect("rollout should persist the deferred tools world state");
    assert_eq!(
        persisted_tools,
        json!({
            "mcp__codex_apps__calendar": "Plan events and manage your calendar."
        })
    );
    let mut resume_builder = search_capable_apps_builder(apps_server.chatgpt_base_url)
        .with_config(enable_deferred_tool_world_state_without_agents);
    let resumed = resume_builder.restart(&server, &initial).await?;
    drop(initial);
    wait_for_mcp_server(&resumed.codex, CODEX_APPS_MCP_SERVER_NAME).await?;
    resumed
        .submit_turn("inspect unchanged deferred tools after resume")
        .await?;

    let requests = response.requests();
    assert_eq!(requests.len(), 2);
    let tools_states = requests
        .iter()
        .map(tools_state_sections)
        .collect::<Vec<_>>();
    assert_eq!(tools_states[0], tools_states[1]);
    assert_eq!(tools_states[0].len(), 1);
    assert!(tools_states[0][0].contains(SEARCH_CALENDAR_NAMESPACE));
    insta::assert_snapshot!(
        "deferred_tools_resume_without_duplicate_update",
        format_labeled_requests_snapshot(
            "Persisted deferred tools remain unchanged after resuming the thread.",
            &[
                ("Before resume", &requests[0]),
                ("After resume", &requests[1]),
            ],
        )
    );

    Ok(())
}

