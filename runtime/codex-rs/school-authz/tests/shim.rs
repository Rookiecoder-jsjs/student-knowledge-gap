//! `school-authz-mcp` shim 集成测试:直接 spawn 编译出的二进制。
//!
//! 依赖 `/bin/sh`(下游用 `sh -c 'echo teacher=...'` 观测 env),仅 unix。

#![cfg(unix)]

use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};

fn shim_bin() -> PathBuf {
    codex_utils_cargo_bin::cargo_bin("school-authz-mcp").expect("shim 二进制应可解析")
}

struct Output {
    stdout: String,
    code: i32,
}

/// spawn shim,喂入 stdin 后立即关闭(触发 shim 对下游做 EOF 收尾),收集其 stdout + 退出码。
#[allow(clippy::too_many_arguments)]
fn run(
    child_cmd: &[&str],
    secret: Option<&str>,
    token: Option<&str>,
    inherit_teacher_id: Option<&str>,
    stdin_input: &str,
) -> Output {
    let mut cmd = Command::new(shim_bin());
    cmd.arg("--").args(child_cmd);
    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    match secret {
        Some(v) => cmd.env("SC_AUTH_SECRET", v),
        None => cmd.env_remove("SC_AUTH_SECRET"),
    };
    match token {
        Some(v) => cmd.env("SC_SCHOOL_AUTH_TOKEN", v),
        None => cmd.env_remove("SC_SCHOOL_AUTH_TOKEN"),
    };
    // 继承的 SC_MCP_TEACHER_ID:显式设或清,避免宿主 env 污染测试结果
    match inherit_teacher_id {
        Some(v) => cmd.env("SC_MCP_TEACHER_ID", v),
        None => cmd.env_remove("SC_MCP_TEACHER_ID"),
    };

    let mut child = cmd.spawn().expect("spawn shim");
    child
        .stdin
        .take()
        .expect("piped stdin")
        .write_all(stdin_input.as_bytes())
        .expect("写入 shim stdin");
    drop(child.stdin.take()); // 关闭 stdin → shim 进入 EOF 收尾

    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("piped stdout")
        .read_to_string(&mut stdout)
        .expect("读取 shim stdout");
    let status = child.wait().expect("wait shim");
    Output {
        stdout,
        code: status.code().unwrap_or(-1),
    }
}

#[test]
fn bad_token_fails_closed_without_spawning_child() {
    let out = run(
        &["sh", "-c", "echo should-not-run"],
        Some("test-secret"),
        Some("garbage-token"),
        Some("999"),
        "",
    );
    assert_eq!(out.code, 1, "坏 token 应 fail closed(退出码 1)");
    assert!(out.stdout.is_empty(), "下游不应被启动: {}", out.stdout);
}

#[test]
fn valid_token_injects_teacher_id_overriding_inherited() {
    let token = codex_school_authz::sign_identity_token("test-secret", 5, 3600);
    let out = run(
        &["sh", "-c", "echo teacher=$SC_MCP_TEACHER_ID"],
        Some("test-secret"),
        Some(&token),
        Some("999"), // 继承的裸身份必须被覆盖
        "",
    );
    assert_eq!(out.code, 0);
    assert_eq!(out.stdout.trim(), "teacher=5");
}

#[test]
fn missing_token_with_secret_strips_teacher_id() {
    let out = run(
        &["sh", "-c", "echo teacher=$SC_MCP_TEACHER_ID"],
        Some("test-secret"),
        None,
        Some("999"), // 继承的裸身份必须被剥离 → 匿名
        "",
    );
    assert_eq!(out.code, 0);
    assert_eq!(out.stdout.trim(), "teacher=");
}

#[test]
fn no_secret_passthrough_inherited_teacher_id() {
    let out = run(
        &["sh", "-c", "echo teacher=$SC_MCP_TEACHER_ID"],
        None,
        None,
        Some("999"),
        "",
    );
    assert_eq!(out.code, 0);
    assert_eq!(out.stdout.trim(), "teacher=999");
}

#[test]
fn missing_separator_exits_usage() {
    // 无 `--` 分隔符 → 用法错误,退出码 2
    let mut cmd = Command::new(shim_bin());
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    let status = cmd.status().expect("spawn shim(无参)");
    assert_eq!(status.code(), Some(2));
}

#[test]
fn relays_stdin_to_child() {
    // 下游 `cat` 回显:验证 stdin 逐字节转发 + EOF 收尾
    let mut cmd = Command::new(shim_bin());
    cmd.arg("--").arg("sh").arg("-c").arg("cat");
    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    cmd.env_remove("SC_AUTH_SECRET").env_remove("SC_SCHOOL_AUTH_TOKEN").env_remove("SC_MCP_TEACHER_ID");

    let mut child = cmd.spawn().expect("spawn shim");
    let mut stdin = child.stdin.take().expect("piped stdin");
    stdin.write_all(b"hello-mcp\n").expect("写 stdin");
    stdin.write_all(b"second-line\n").expect("写 stdin");
    drop(stdin);

    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("piped stdout")
        .read_to_string(&mut stdout)
        .expect("读 stdout");
    let status = child.wait().expect("wait shim");
    assert_eq!(status.code(), Some(0));
    assert_eq!(stdout, "hello-mcp\nsecond-line\n");
}
