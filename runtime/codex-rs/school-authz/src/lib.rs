//! 教师↔班级鉴权原语(§6.3 school-authz)。
//!
//! Token 格式与 sc 后端 `backend/app/auth.py` 逐字节一致:`teacher_id.exp.sig`,
//! `sig = HMAC-SHA256(secret, "teacher_id.exp")` 的 hexdigest。复用同一密钥
//! `SC_AUTH_SECRET`——gateway 用 Python 签、本 crate 在壳侧验、后端已能验,
//! 单一密钥零新概念。
//!
//! 二进制 `school-authz-mcp`(见 `src/main.rs`)用 `decide_identity_action` 把
//! 决策表落成行为:`SC_MCP_TEACHER_ID` 绝不信任继承 env,一律由已验证 token 派生。

use hmac::{Hmac, Mac};
use sha2::Sha256;

const TOKEN_SEPARATOR: char = '.';

/// 一个已验证的教师身份(由签名 token 解析而来)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeacherIdentity {
    pub teacher_id: i64,
    pub expires_at: u64,
}

#[derive(Debug, PartialEq, Eq, thiserror::Error)]
pub enum TokenError {
    #[error("token 格式非法")]
    Malformed,
    #[error("token 签名无效")]
    InvalidSignature,
    #[error("token 已过期")]
    Expired,
}

#[derive(Debug, PartialEq, Eq, thiserror::Error)]
pub enum AuthzError {
    #[error("教师 {teacher_id} 无权访问班级 {class_id}")]
    NotAuthorized { teacher_id: i64, class_id: i64 },
}

/// shim 的身份决策:token 状态 → 对 `SC_MCP_TEACHER_ID` 的动作。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IdentityAction {
    /// `SC_AUTH_SECRET` 未配置:透传,不改 env(防御性;该模式不部署 shim)。
    Passthrough,
    /// 校验通过:注入/覆盖 `SC_MCP_TEACHER_ID`。
    SetTeacher { teacher_id: i64 },
    /// secret 已配但无 token:剥离 `SC_MCP_TEACHER_ID` → 匿名(安全模式由后端断言拒绝)。
    Anonymous,
    /// token 无效/过期:拒绝启动下游(fail closed)。
    FailClosed { reason: String },
}

/// 签发身份 token(gateway 用 Python 同款算法;本函数供 Rust 侧测试/将来服务方签发)。
pub fn sign_identity_token(secret: &str, teacher_id: i64, ttl_secs: u64) -> String {
    let exp = unix_now() + ttl_secs as i64;
    let body = format!("{teacher_id}{TOKEN_SEPARATOR}{exp}");
    format!("{body}{TOKEN_SEPARATOR}{}", hmac_hex(secret, &body))
}

/// 用当前时间校验 token,返回身份。
pub fn verify_identity_token(secret: &str, token: &str) -> Result<TeacherIdentity, TokenError> {
    verify_identity_token_at(secret, token, unix_now())
}

/// 用给定时间戳校验 token(语义对齐后端 `verify_token`:三段 split → 重算签名
/// → constant-time 比对 → 解析 → exp 检查)。
pub fn verify_identity_token_at(
    secret: &str,
    token: &str,
    now_secs: i64,
) -> Result<TeacherIdentity, TokenError> {
    let mut parts = token.split(TOKEN_SEPARATOR);
    let (teacher_id_raw, expires_raw, sig) = match (parts.next(), parts.next(), parts.next(), parts.next()) {
        (Some(a), Some(b), Some(c), None) => (a, b, c),
        _ => return Err(TokenError::Malformed),
    };
    let body = format!("{teacher_id_raw}{TOKEN_SEPARATOR}{expires_raw}");
    let expected = hmac_hex(secret, &body);
    if !constant_time_eq(expected.as_bytes(), sig.as_bytes()) {
        return Err(TokenError::InvalidSignature);
    }
    let teacher_id: i64 = teacher_id_raw.parse().map_err(|_| TokenError::Malformed)?;
    let expires_at: i64 = expires_raw.parse().map_err(|_| TokenError::Malformed)?;
    if expires_at < now_secs {
        return Err(TokenError::Expired);
    }
    Ok(TeacherIdentity {
        teacher_id,
        expires_at: expires_at as u64,
    })
}

/// 教师↔班级访问断言(§6.3 权限断言原语;逐调用权威裁决仍在 sc 后端查
/// `teacher_class` 授权表,本函数供壳侧按需复用)。
pub fn assert_class_access(
    identity: &TeacherIdentity,
    allowed_class_ids: &[i64],
    class_id: i64,
) -> Result<(), AuthzError> {
    if allowed_class_ids.contains(&class_id) {
        Ok(())
    } else {
        Err(AuthzError::NotAuthorized {
            teacher_id: identity.teacher_id,
            class_id,
        })
    }
}

/// shim 的身份决策表(核心安全属性)。
///
/// - secret 未配置 → 透传(该模式不部署 shim,纯防御)
/// - secret 已配 + token 有效 → 注入身份
/// - secret 已配 + 无 token → 剥离身份(匿名)
/// - secret 已配 + token 无效/过期 → fail closed
pub fn decide_identity_action(secret: Option<&str>, token: Option<&str>) -> IdentityAction {
    let Some(secret) = secret else {
        return IdentityAction::Passthrough;
    };
    let Some(token) = token else {
        return IdentityAction::Anonymous;
    };
    match verify_identity_token(secret, token) {
        Ok(identity) => IdentityAction::SetTeacher {
            teacher_id: identity.teacher_id,
        },
        Err(err) => IdentityAction::FailClosed {
            reason: err.to_string(),
        },
    }
}

fn hmac_hex(secret: &str, body: &str) -> String {
    let mut mac = <Hmac<Sha256>>::new_from_slice(secret.as_bytes())
        .expect("HMAC 接受任意长度密钥");
    mac.update(body.as_bytes());
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b) {
        diff |= x ^ y;
    }
    diff == 0
}

fn unix_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
