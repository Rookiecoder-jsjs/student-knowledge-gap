use super::SandboxCommand;
use super::SandboxManager;
use super::SandboxTransformRequest;
use super::SandboxType;
use super::SandboxablePreference;
use super::get_platform_sandbox;
use super::with_managed_mitm_ca_readable_root;
use codex_protocol::models::AdditionalPermissionProfile;
use codex_protocol::models::FileSystemPermissions;
use codex_protocol::models::NetworkPermissions;
use codex_protocol::models::PermissionProfile;
use codex_protocol::permissions::FileSystemAccessMode;
use codex_protocol::permissions::FileSystemPath;
use codex_protocol::permissions::FileSystemSandboxEntry;
use codex_protocol::permissions::FileSystemSandboxPolicy;
use codex_protocol::permissions::FileSystemSpecialPath;
use codex_protocol::permissions::NetworkSandboxPolicy;
use codex_utils_absolute_path::AbsolutePathBuf;
use codex_utils_path_uri::PathUri;
use dunce::canonicalize;
use pretty_assertions::assert_eq;
use std::collections::HashMap;
use tempfile::TempDir;

#[test]
fn danger_full_access_defaults_to_no_sandbox_without_network_requirements() {
    let manager = SandboxManager::new();
    let sandbox = manager.select_initial(
        &PermissionProfile::Disabled,
        SandboxablePreference::Auto,
        /*has_managed_network_requirements*/ false,
    );
    assert_eq!(sandbox, SandboxType::None);
}

#[test]
fn danger_full_access_uses_platform_sandbox_with_network_requirements() {
    let manager = SandboxManager::new();
    let expected = get_platform_sandbox().unwrap_or(SandboxType::None);
    let sandbox = manager.select_initial(
        &PermissionProfile::Disabled,
        SandboxablePreference::Auto,
        /*has_managed_network_requirements*/ true,
    );
    assert_eq!(sandbox, expected);
}

#[test]
fn restricted_file_system_uses_platform_sandbox_without_managed_network() {
    let manager = SandboxManager::new();
    let expected = get_platform_sandbox().unwrap_or(SandboxType::None);
    let permissions = PermissionProfile::from_runtime_permissions(
        &FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        }]),
        NetworkSandboxPolicy::Enabled,
    );
    let sandbox = manager.select_initial(
        &permissions,
        SandboxablePreference::Auto,
        /*has_managed_network_requirements*/ false,
    );
    assert_eq!(sandbox, expected);
}

#[test]
fn unsandboxed_transform_preserves_foreign_cwd_and_unrestricted_file_system_policy() {
    let manager = SandboxManager::new();
    let cwd_uri = if cfg!(windows) {
        PathUri::parse("file:///workspace/remote").expect("POSIX path URI")
    } else {
        PathUri::parse("file:///C:/workspace/remote").expect("Windows path URI")
    };
    let permissions = PermissionProfile::from_runtime_permissions(
        &FileSystemSandboxPolicy::unrestricted(),
        NetworkSandboxPolicy::Restricted,
    );
    let exec_request = manager
        .transform(SandboxTransformRequest {
            command: SandboxCommand {
                program: "true".into(),
                args: Vec::new(),
                cwd: cwd_uri.clone(),
                env: HashMap::new(),
                managed_network: None,
                additional_permissions: None,
            },
            permissions: &permissions,
            sandbox: SandboxType::None,
            enforce_managed_network: false,
            environment_id: None,
            network: None,
            sandbox_policy_cwd: &cwd_uri,
            })
        .expect("transform");

    assert_eq!(exec_request.cwd, cwd_uri);
    assert_eq!(exec_request.sandbox_policy_cwd, cwd_uri);
    assert_eq!(
        exec_request.permission_profile.file_system_sandbox_policy(),
        FileSystemSandboxPolicy::unrestricted()
    );
    assert_eq!(
        exec_request.permission_profile.network_sandbox_policy(),
        NetworkSandboxPolicy::Restricted
    );
}

#[cfg(target_os = "macos")]
#[test]
fn symlinked_workspace_reports_seatbelt_preparation_error() {
    use std::os::unix::fs::symlink;

    let manager = SandboxManager::new();
    let temp_dir = TempDir::new().expect("create temp dir");
    let target = temp_dir.path().join("target");
    let workspace = temp_dir.path().join("workspace");
    std::fs::create_dir(&target).expect("create target");
    symlink(&target, &workspace).expect("create symlinked workspace");
    let workspace = AbsolutePathBuf::from_absolute_path(workspace).expect("absolute workspace");
    let workspace_uri = PathUri::from_abs_path(&workspace);
    let permissions = PermissionProfile::from_runtime_permissions(
        &FileSystemSandboxPolicy::workspace_write(
            &[],
            /*exclude_tmpdir_env_var*/ true,
            /*exclude_slash_tmp*/ true,
        ),
        NetworkSandboxPolicy::Restricted,
    );

    let error = manager
        .transform(SandboxTransformRequest {
            command: SandboxCommand {
                program: "true".into(),
                args: Vec::new(),
                cwd: workspace_uri.clone(),
                env: HashMap::new(),
                managed_network: None,
                additional_permissions: None,
            },
            permissions: &permissions,
            sandbox: SandboxType::MacosSeatbelt,
            enforce_managed_network: false,
            environment_id: None,
            network: None,
            sandbox_policy_cwd: &workspace_uri,
            })
        .expect_err("symlinked workspace should be rejected");

    assert!(matches!(
        &error,
        super::SandboxTransformError::SeatbeltPreparation(message)
            if message.contains("symlinked writable roots are not supported")
    ));
    assert!(
        !error.to_string().contains("network proxy"),
        "filesystem error should not be attributed to network proxy: {error}"
    );
}

#[test]
fn transform_additional_permissions_enable_network_for_external_sandbox() {
    let manager = SandboxManager::new();
    let cwd = AbsolutePathBuf::current_dir().expect("current dir");
    let cwd_uri = PathUri::from_abs_path(&cwd);
    let permissions = PermissionProfile::External {
        network: NetworkSandboxPolicy::Restricted,
    };
    let temp_dir = TempDir::new().expect("create temp dir");
    let path = AbsolutePathBuf::from_absolute_path(
        canonicalize(temp_dir.path()).expect("canonicalize temp dir"),
    )
    .expect("absolute temp dir");
    let exec_request = manager
        .transform(SandboxTransformRequest {
            command: SandboxCommand {
                program: "true".into(),
                args: Vec::new(),
                cwd: cwd_uri.clone(),
                env: HashMap::new(),
                managed_network: None,
                additional_permissions: Some(AdditionalPermissionProfile {
                    network: Some(NetworkPermissions {
                        enabled: Some(true),
                    }),
                    file_system: Some(FileSystemPermissions::from_read_write_roots(
                        Some(vec![path]),
                        Some(Vec::new()),
                    )),
                }),
            },
            permissions: &permissions,
            sandbox: SandboxType::None,
            enforce_managed_network: false,
            environment_id: None,
            network: None,
            sandbox_policy_cwd: &cwd_uri,
            })
        .expect("transform");

    assert_eq!(
        exec_request.permission_profile,
        PermissionProfile::External {
            network: NetworkSandboxPolicy::Enabled,
        }
    );
    assert_eq!(
        exec_request.permission_profile.network_sandbox_policy(),
        NetworkSandboxPolicy::Enabled
    );
}

#[test]
fn transform_additional_permissions_preserves_denied_entries() {
    let manager = SandboxManager::new();
    let cwd = AbsolutePathBuf::current_dir().expect("current dir");
    let cwd_uri = PathUri::from_abs_path(&cwd);
    let temp_dir = TempDir::new().expect("create temp dir");
    let workspace_root = AbsolutePathBuf::from_absolute_path(
        canonicalize(temp_dir.path()).expect("canonicalize temp dir"),
    )
    .expect("absolute temp dir");
    let allowed_path = workspace_root.join("allowed");
    let denied_path = workspace_root.join("denied");
    let file_system_policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: denied_path.clone().into(),
            access: FileSystemAccessMode::Deny,
            missing_path_behavior: None,
        },
    ]);
    let permissions = PermissionProfile::from_runtime_permissions(
        &file_system_policy,
        NetworkSandboxPolicy::Restricted,
    );
    let exec_request = manager
        .transform(SandboxTransformRequest {
            command: SandboxCommand {
                program: "true".into(),
                args: Vec::new(),
                cwd: cwd_uri.clone(),
                env: HashMap::new(),
                managed_network: None,
                additional_permissions: Some(AdditionalPermissionProfile {
                    file_system: Some(FileSystemPermissions::from_read_write_roots(
                        /*read*/ None,
                        Some(vec![allowed_path.clone()]),
                    )),
                    ..Default::default()
                }),
            },
            permissions: &permissions,
            sandbox: SandboxType::None,
            enforce_managed_network: false,
            environment_id: None,
            network: None,
            sandbox_policy_cwd: &cwd_uri,
            })
        .expect("transform");

    assert_eq!(
        exec_request.permission_profile.file_system_sandbox_policy(),
        FileSystemSandboxPolicy::restricted(vec![
            FileSystemSandboxEntry {
                path: FileSystemPath::Special {
                    value: FileSystemSpecialPath::Root,
                },
                access: FileSystemAccessMode::Read,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: denied_path.into(),
                access: FileSystemAccessMode::Deny,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: allowed_path.into(),
                access: FileSystemAccessMode::Write,
                missing_path_behavior: None,
            },
        ])
    );
    assert_eq!(
        exec_request.permission_profile.network_sandbox_policy(),
        NetworkSandboxPolicy::Restricted
    );
}

#[test]
fn managed_mitm_ca_bundle_becomes_readable_for_restricted_sandbox() {
    let cwd = TempDir::new().expect("create cwd");
    let cwd =
        AbsolutePathBuf::from_absolute_path(canonicalize(cwd.path()).expect("canonicalize cwd"))
            .expect("absolute cwd");
    let managed_bundle_dir = TempDir::new().expect("create managed bundle dir");
    let managed_bundle_path =
        AbsolutePathBuf::from_absolute_path(managed_bundle_dir.path().join("ca-bundle.pem"))
            .expect("absolute managed bundle path");
    let permission_profile = PermissionProfile::from_runtime_permissions(
        &FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
            path: cwd.clone().into(),
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        }]),
        NetworkSandboxPolicy::Restricted,
    );

    let permission_profile = with_managed_mitm_ca_readable_root(
        permission_profile,
        Some(&managed_bundle_path),
        cwd.as_path(),
    );
    let (file_system_sandbox_policy, _) = permission_profile.to_runtime_permissions();

    assert_eq!(
        file_system_sandbox_policy,
        FileSystemSandboxPolicy::restricted(vec![
            FileSystemSandboxEntry {
                path: cwd.into(),
                access: FileSystemAccessMode::Read,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: managed_bundle_path.into(),
                access: FileSystemAccessMode::Read,
                missing_path_behavior: None,
            },
        ])
    );
}
