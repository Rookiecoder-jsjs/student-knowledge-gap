//! `codex-school-authz` lib 集成测试:签名/校验/断言/决策表。

use codex_school_authz::{
    assert_class_access, decide_identity_action, sign_identity_token, verify_identity_token,
    verify_identity_token_at, AuthzError, IdentityAction, TeacherIdentity, TokenError,
};

const SECRET: &str = "test-secret";

#[test]
fn sign_and_verify_roundtrip() {
    let token = sign_identity_token(SECRET, 7, 3600);
    let identity = verify_identity_token(SECRET, &token).expect("有效 token 应通过");
    assert_eq!(identity.teacher_id, 7);
}

#[test]
fn signature_tamper_rejected() {
    let token = sign_identity_token(SECRET, 7, 3600);

    // 尾部追加 → sig 段不匹配
    assert_eq!(
        verify_identity_token(SECRET, &format!("{token}x")),
        Err(TokenError::InvalidSignature)
    );

    // 换 teacher_id 段 → 签名不匹配
    let mut parts: Vec<&str> = token.split('.').collect();
    parts[0] = "8";
    assert_eq!(
        verify_identity_token(SECRET, &parts.join(".")),
        Err(TokenError::InvalidSignature)
    );

    // 换 exp 段 → 签名不匹配
    let mut parts: Vec<&str> = token.split('.').collect();
    parts[1] = "9999999999";
    assert_eq!(
        verify_identity_token(SECRET, &parts.join(".")),
        Err(TokenError::InvalidSignature)
    );

    // 换密钥 → 签名不匹配
    assert_eq!(
        verify_identity_token("other-secret", &token),
        Err(TokenError::InvalidSignature)
    );
}

#[test]
fn expired_token_rejected() {
    // 用真实时钟签(exp = now + 3600),再以超过 exp 的未来时刻校验
    let token = sign_identity_token(SECRET, 7, 3600);
    let far_future = unix_now() + 7200;
    assert_eq!(
        verify_identity_token_at(SECRET, &token, far_future),
        Err(TokenError::Expired)
    );
}

#[test]
fn malformed_tokens_rejected() {
    assert_eq!(verify_identity_token_at(SECRET, "", 0), Err(TokenError::Malformed));
    assert_eq!(verify_identity_token_at(SECRET, "1.2", 0), Err(TokenError::Malformed));
    assert_eq!(
        verify_identity_token_at(SECRET, "1.2.3.extra", 0),
        Err(TokenError::Malformed)
    );
}

#[test]
fn assert_class_access_allows_and_denies() {
    let identity = TeacherIdentity {
        teacher_id: 3,
        expires_at: 9_999_999_999,
    };
    let allowed = vec![1i64, 3, 5];
    assert!(assert_class_access(&identity, &allowed, 3).is_ok());
    assert_eq!(
        assert_class_access(&identity, &allowed, 7),
        Err(AuthzError::NotAuthorized {
            teacher_id: 3,
            class_id: 7,
        })
    );
}

#[test]
fn decide_action_sets_teacher_from_valid_token() {
    let token = sign_identity_token(SECRET, 3, 3600);
    assert_eq!(
        decide_identity_action(Some(SECRET), Some(&token)),
        IdentityAction::SetTeacher { teacher_id: 3 }
    );
}

#[test]
fn decide_action_strips_identity_without_token() {
    assert_eq!(
        decide_identity_action(Some(SECRET), None),
        IdentityAction::Anonymous
    );
}

#[test]
fn decide_action_fails_closed_on_bad_token() {
    match decide_identity_action(Some(SECRET), Some("garbage")) {
        IdentityAction::FailClosed { .. } => {}
        other => panic!("预期 FailClosed,实际 {other:?}"),
    }
}

#[test]
fn decide_action_passthrough_without_secret() {
    assert_eq!(decide_identity_action(None, None), IdentityAction::Passthrough);
    assert_eq!(
        decide_identity_action(None, Some("ignored")),
        IdentityAction::Passthrough
    );
}

fn unix_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
