//! `school-authz-mcp`:stdio MCP shim。
//!
//! 包装下游 sc MCP server(配置在 CODEX_HOME config.toml `[mcp_servers.sc]` 的
//! `command`/`args`)。用法:`school-authz-mcp -- <下游命令> [参数…]`。
//!
//! 安全契约:本进程是 `SC_MCP_TEACHER_ID` 的**唯一**写入方——它绝不信任继承 env,
//! 而是按 [`decide_identity_action`] 决策:token 有效则从 token 派生身份注入(覆盖任何
//! 继承值),token 缺失则剥离身份(匿名),token 无效则 fail closed 拒绝启动下游。
//! 逐调用教师↔班级断言仍由 sc 后端执行(DB 权威),见 `codex_school_authz` 文档。

use std::env;
use std::io::{Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};

use codex_school_authz::{decide_identity_action, IdentityAction};

const ENV_SECRET: &str = "SC_AUTH_SECRET";
const ENV_TOKEN: &str = "SC_SCHOOL_AUTH_TOKEN";
const ENV_TEACHER_ID: &str = "SC_MCP_TEACHER_ID";
const ARGV_SEPARATOR: &str = "--";
/// 下游在我们 stdin 关闭后仍不自行退出的宽限(超时才 kill,防孤儿)。
const CHILD_GRACE: std::time::Duration = std::time::Duration::from_secs(2);
const REAP_POLL: std::time::Duration = std::time::Duration::from_millis(20);

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let Some(separator) = args.iter().position(|a| a == ARGV_SEPARATOR) else {
        eprintln!("school-authz-mcp: 用法: school-authz-mcp -- <下游 MCP server 命令> [参数…]");
        std::process::exit(2);
    };
    if separator + 1 >= args.len() {
        eprintln!("school-authz-mcp: `--` 后缺少下游命令");
        std::process::exit(2);
    }

    let mut command = Command::new(&args[separator + 1]);
    command
        .args(&args[separator + 2..])
        .stdin(Stdio::piped())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    // 身份决策只作用于下游子进程 env,不改本进程 env(避免 edition 2024 的 unsafe set_var;
    // 也让「本进程是 SC_MCP_TEACHER_ID 唯一写入方」的边界收在 spawn 处)。
    match decide_identity_action(
        env::var(ENV_SECRET).ok().as_deref(),
        env::var(ENV_TOKEN).ok().as_deref(),
    ) {
        IdentityAction::FailClosed { reason } => {
            eprintln!("school-authz-mcp: 教师身份校验失败,拒绝启动下游: {reason}");
            std::process::exit(1);
        }
        IdentityAction::SetTeacher { teacher_id } => {
            command.env(ENV_TEACHER_ID, teacher_id.to_string());
        }
        IdentityAction::Anonymous => {
            command.env_remove(ENV_TEACHER_ID);
        }
        IdentityAction::Passthrough => {}
    }

    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(err) => {
            eprintln!("school-authz-mcp: 无法启动下游 `{}`: {err}", args[separator + 1]);
            std::process::exit(1);
        }
    };
    let child_stdin = child.stdin.take().expect("子进程已用 piped stdin spawn");
    let code = relay_and_reap(&mut child, child_stdin);
    std::process::exit(code);
}

/// 把本端 stdin 逐字节转发给下游;本端 EOF 后给下游优雅收尾宽限,超时再 kill。
/// 返回下游退出码(异常终止回落 1)。
fn relay_and_reap(child: &mut Child, mut child_stdin: ChildStdin) -> i32 {
    let mut stdin = std::io::stdin();
    let mut buf = [0u8; 8192];
    loop {
        match stdin.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                if child_stdin.write_all(&buf[..n]).is_err() {
                    break;
                }
                let _ = child_stdin.flush();
            }
        }
    }
    drop(child_stdin); // 本端会话结束 → 优雅 EOF 给下游

    let deadline = std::time::Instant::now() + CHILD_GRACE;
    loop {
        if let Some(status) = child.try_wait().expect("try_wait 不应失败") {
            return status.code().unwrap_or(1);
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            return child
                .wait()
                .ok()
                .and_then(|status| status.code())
                .unwrap_or(1);
        }
        std::thread::sleep(REAP_POLL);
    }
}
