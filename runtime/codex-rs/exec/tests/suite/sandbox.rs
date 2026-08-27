#![cfg(unix)]
use codex_core::spawn::StdioPolicy;
use codex_protocol::models::PermissionProfile;
use codex_utils_absolute_path::AbsolutePathBuf;
use std::collections::HashMap;
use std::io;
use tokio::process::Child;

pub(super) async fn spawn_command_under_sandbox(
    command: Vec<String>,
    command_cwd: AbsolutePathBuf,
    permission_profile: &PermissionProfile,
    sandbox_cwd: &AbsolutePathBuf,
    stdio_policy: StdioPolicy,
    env: HashMap<String, String>,
) -> std::io::Result<Child> {
    use codex_core::exec::ExecCapturePolicy;
    use codex_core::exec::ExecParams;
    use codex_core::exec::build_exec_request;
    use codex_core::sandboxing::SandboxPermissions;
use std::process::Stdio;

    let exec_request = build_exec_request(
        ExecParams {
            command,
            cwd: command_cwd,
            expiration: 1000.into(),
            capture_policy: ExecCapturePolicy::ShellTool,
            env,
            network: None,
            network_environment_id: None,
            sandbox_permissions: SandboxPermissions::UseDefault,
            justification: None,
            arg0: None,
        },
        permission_profile,
        sandbox_cwd,
    )
    .map_err(|err| io::Error::other(err.to_string()))?;

    let (program, args) = exec_request
        .command
        .split_first()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "command args are empty"))?;

    let mut child = tokio::process::Command::new(program);
    if let Some(arg0) = exec_request.arg0.as_deref() {
        child.arg0(arg0);
    }
    child.args(args);
    // TODO(anp): Keep PathUri through the macOS sandbox process launch boundary.
    let native_cwd = exec_request
        .cwd
        .to_abs_path()
        .map_err(|err| io::Error::other(err.to_string()))?;
    child.current_dir(native_cwd);
    child.env_clear();
    child.envs(exec_request.env);

    match stdio_policy {
        StdioPolicy::RedirectForShellTool => {
            child.stdin(Stdio::null());
            child.stdout(Stdio::piped()).stderr(Stdio::piped());
        }
        StdioPolicy::Inherit => {
            child
                .stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit());
        }
    }

    child.kill_on_drop(true).spawn()
}