mod denial;
mod manager;
pub mod policy_transforms;
#[cfg(target_os = "macos")]
pub mod seatbelt;
mod spawn;
mod violation;
mod windows;

pub use codex_windows_sandbox::WindowsSandboxProxySettingsMode;
pub use denial::is_likely_executor_managed_sandbox_denied;
pub use denial::is_likely_sandbox_denied;
pub use manager::SandboxCommand;
pub use manager::SandboxDirectSpawnTransformRequest;
pub use manager::SandboxExecRequest;
pub use manager::SandboxManager;
pub use manager::SandboxTransformError;
pub use manager::SandboxTransformRequest;
pub use manager::SandboxType;
pub use manager::SandboxablePreference;
pub use manager::compatibility_sandbox_policy_for_permission_profile;
pub use manager::get_platform_sandbox;
pub use manager::with_managed_mitm_ca_readable_root;
pub use spawn::SpawnRequest;
pub use spawn::WindowsSandboxSpawnRequest;
pub use spawn::spawn_process;
pub use violation::FileSystemSandboxViolation;
pub use violation::FileSystemSandboxViolationReason;
pub use violation::NetworkSandboxViolation;
pub use violation::SandboxViolationBackend;
pub use violation::SandboxViolationEvent;
pub use violation::record_filesystem_sandbox_violation;
pub use violation::record_network_sandbox_violation;
pub use violation::record_sandbox_violation;
pub use windows::WindowsSandboxFilesystemOverrides;
pub use windows::permission_profile_supports_windows_restricted_token_sandbox;
pub use windows::resolve_windows_elevated_filesystem_overrides;
pub use windows::resolve_windows_restricted_token_filesystem_overrides;
pub use windows::unsupported_windows_restricted_token_sandbox_reason;
pub use windows::windows_sandbox_uses_elevated_backend;

use codex_protocol::error::CodexErr;

impl From<SandboxTransformError> for CodexErr {
    fn from(err: SandboxTransformError) -> Self {
        match err {
            error @ SandboxTransformError::InvalidCommandCwd { .. }
            | error @ SandboxTransformError::InvalidSandboxPolicyCwd { .. } => {
                CodexErr::InvalidRequest(error.to_string())
            }
            SandboxTransformError::EnvironmentNetworkProxy(message) => {
                CodexErr::UnsupportedOperation(message)
            }
            #[cfg(target_os = "macos")]
            SandboxTransformError::SeatbeltPreparation(message) => {
                CodexErr::UnsupportedOperation(message)
            }
            #[cfg(not(target_os = "macos"))]
            SandboxTransformError::SeatbeltUnavailable => CodexErr::UnsupportedOperation(
                "seatbelt sandbox is only available on macOS".to_string(),
            ),
            #[cfg(target_os = "windows")]
            SandboxTransformError::WindowsSandboxPreparation(message) => {
                CodexErr::UnsupportedOperation(message)
            }
        }
    }
}
