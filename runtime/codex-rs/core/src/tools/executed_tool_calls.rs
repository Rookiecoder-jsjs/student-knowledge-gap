use std::collections::HashMap;
use std::collections::HashSet;

use codex_protocol::models::ExecutedToolCall;
use codex_protocol::models::ResponseItem;
use codex_protocol::models::bound_executed_tool_calls_for_prompt_prioritizing_recent;
use codex_protocol::models::executed_tool_call_metadata_bytes;
use serde_json::Value as JsonValue;

use crate::tools::context::ToolCallSource;
use crate::tools::context::ToolPayload;
use crate::tools::router::ToolCall;

const MAX_EXECUTED_TOOL_CALL_ARGUMENT_BYTES: usize = 8 * 1024;
const MAX_EXECUTED_TOOL_CALL_FULL_ARGUMENT_BYTES_PER_OUTPUT: usize = 32 * 1024;
const MAX_PENDING_EXECUTED_TOOL_CALLS: usize = 256;

/// Best-effort, session-scoped attempted-tool metadata; cancellation and
/// compaction can leave pending calls unreported.
#[derive(Default)]
pub(crate) struct ExecutedToolCallRecorder {
    state: std::sync::Mutex<ExecutedToolCallRecorderState>,
}

#[derive(Default)]
struct ExecutedToolCallRecorderState {
    direct_calls: HashMap<String, ExecutedToolCall>,
    retained_calls: HashMap<(std::mem::Discriminant<ResponseItem>, String), Vec<ExecutedToolCall>>,
}

#[derive(Default)]
struct JsonByteCounter(usize);

impl std::io::Write for JsonByteCounter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0 = self.0.saturating_add(bytes.len());
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn serialized_json_bytes<T: serde::Serialize + ?Sized>(value: &T) -> usize {
    let mut counter = JsonByteCounter::default();
    if serde_json::to_writer(&mut counter, value).is_err() {
        return usize::MAX;
    }
    counter.0
}

impl ExecutedToolCallRecorder {
    pub(crate) fn record_tool_call(
        &self,
        call: &ToolCall,
        _source: &ToolCallSource,
    ) {
        let original_bytes = match &call.payload {
            ToolPayload::Function { arguments } => arguments.len(),
            ToolPayload::Custom { input } => serialized_json_bytes(input),
            ToolPayload::ToolSearch { arguments } => serialized_json_bytes(arguments),
        };
        let name = call.tool_name.clone().with_default_namespace().to_string();
        let recorded_call = if original_bytes > MAX_EXECUTED_TOOL_CALL_ARGUMENT_BYTES {
            ExecutedToolCall::truncated(name, original_bytes, MAX_EXECUTED_TOOL_CALL_ARGUMENT_BYTES)
        } else {
            let arguments = match &call.payload {
                ToolPayload::Function { arguments } => serde_json::from_str(arguments)
                    .unwrap_or_else(|_| JsonValue::String(arguments.clone())),
                ToolPayload::Custom { input } => JsonValue::String(input.clone()),
                ToolPayload::ToolSearch { arguments } => {
                    serde_json::to_value(arguments).unwrap_or_default()
                }
            };
            ExecutedToolCall::new(name, arguments)
        };
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.direct_calls.len() < MAX_PENDING_EXECUTED_TOOL_CALLS {
            state
                .direct_calls
                .entry(call.call_id.clone())
                .or_insert(recorded_call);
        } else if state.direct_calls.len() == MAX_PENDING_EXECUTED_TOOL_CALLS
            && !state.direct_calls.contains_key(&call.call_id)
        {
            state.direct_calls.insert(
                call.call_id.clone(),
                ExecutedToolCall::truncated(
                    recorded_call.name,
                    original_bytes,
                    /*max_bytes*/ 0,
                ),
            );
        }
    }

    pub(crate) fn attach_pending_to_prompt(
        &self,
        items: &mut [ResponseItem],
        retry_cache: &mut HashMap<
            (std::mem::Discriminant<ResponseItem>, String),
            Vec<ExecutedToolCall>,
        >,
    ) -> bool {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.direct_calls.is_empty()
            && state.retained_calls.is_empty()
            && retry_cache.is_empty()
        {
            return false;
        }

        let mut pending_retry_outputs = retry_cache.keys().cloned().collect::<HashSet<_>>();
        let mut pending_retained_outputs =
            state.retained_calls.keys().cloned().collect::<HashSet<_>>();
        let mut attached = false;
        let mut retained_bytes = 0_usize;
        for item in items.iter_mut().rev() {
            if state.direct_calls.is_empty()
                && pending_retry_outputs.is_empty()
                && pending_retained_outputs.is_empty()
            {
                break;
            }
            let call_id = match &*item {
                ResponseItem::FunctionCallOutput { call_id, .. }
                | ResponseItem::CustomToolCallOutput { call_id, .. }
                | ResponseItem::ToolSearchOutput {
                    call_id: Some(call_id),
                    ..
                } => call_id,
                _ => continue,
            };
            let key = (std::mem::discriminant(&*item), call_id.clone());
            let calls = if let Some(cached) = retry_cache.get(&key) {
                if !pending_retry_outputs.remove(&key) {
                    continue;
                }
                pending_retained_outputs.remove(&key);
                cached.clone()
            } else if let Some(retained) = state.retained_calls.get(&key) {
                if !pending_retained_outputs.remove(&key) {
                    continue;
                }
                retained.clone()
            } else {
                let calls = state
                    .direct_calls
                    .remove(call_id)
                    .into_iter()
                    .collect::<Vec<_>>();
                if calls.is_empty() {
                    continue;
                }
                retry_cache.insert(key.clone(), calls.clone());
                state.retained_calls.insert(key, calls.clone());
                calls
            };
            item.append_executed_tool_calls(calls);
            retained_bytes = retained_bytes.saturating_add(executed_tool_call_metadata_bytes(item));
            attached = true;
        }
        if !pending_retained_outputs.is_empty() {
            state
                .retained_calls
                .retain(|key, _| !pending_retained_outputs.contains(key));
        }
        if retained_bytes > MAX_EXECUTED_TOOL_CALL_FULL_ARGUMENT_BYTES_PER_OUTPUT {
            bound_executed_tool_calls_for_prompt_prioritizing_recent(items);
            state.retained_calls.clear();
            for item in items {
                let call_id = match &*item {
                    ResponseItem::FunctionCallOutput { call_id, .. }
                    | ResponseItem::CustomToolCallOutput { call_id, .. }
                    | ResponseItem::ToolSearchOutput {
                        call_id: Some(call_id),
                        ..
                    } => call_id,
                    _ => continue,
                };
                if let Some(calls) = item
                    .executed_tool_call_metadata()
                    .and_then(|metadata| metadata.executed_tool_calls.as_ref())
                    .filter(|calls| !calls.is_empty())
                {
                    state.retained_calls.insert(
                        (std::mem::discriminant(&*item), call_id.clone()),
                        calls.clone(),
                    );
                }
            }
        }

        attached
    }
}
