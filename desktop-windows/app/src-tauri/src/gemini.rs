//! Gemini API provider (paid, billed to Matthew's Google account).
//!
//! The API key lives in the OS credential store (Windows Credential Manager) and
//! never crosses into the web view. Every call is user-triggered from the UI after
//! a confirmation that names the model and warns about billing. There is no retry
//! and no fallback to another provider.

use serde::Serialize;
use serde_json::{json, Value};

const SERVICE: &str = "ie.boynearchives.escriptai.desktop";
const ACCOUNT: &str = "gemini-api-key";
const ENV_KEY: &str = "GEMINI_API_KEY";
const API_BASE: &str = "https://generativelanguage.googleapis.com/v1beta/models";

fn entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, ACCOUNT).map_err(|e| format!("Credential store unavailable: {e}"))
}

fn stored_key() -> Option<String> {
    entry().ok()?.get_password().ok().filter(|k| !k.trim().is_empty())
}

/// Credential store first; the environment variable is a developer fallback.
fn resolve_key() -> Option<(String, &'static str)> {
    if let Some(key) = stored_key() {
        return Some((key, "credential-store"));
    }
    std::env::var(ENV_KEY)
        .ok()
        .filter(|k| !k.trim().is_empty())
        .map(|k| (k, "environment"))
}

#[derive(Serialize)]
pub struct KeyStatus {
    configured: bool,
    source: Option<&'static str>,
}

#[tauri::command]
pub fn gemini_key_status() -> KeyStatus {
    let source = resolve_key().map(|(_, s)| s);
    KeyStatus { configured: source.is_some(), source }
}

#[tauri::command]
pub fn gemini_save_key(key: String) -> Result<(), String> {
    let key = key.trim();
    if key.is_empty() {
        return Err("The API key is empty.".into());
    }
    entry()?.set_password(key).map_err(|e| format!("Could not save the key: {e}"))
}

#[tauri::command]
pub fn gemini_delete_key() -> Result<(), String> {
    match entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("Could not remove the key: {e}")),
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReadResult {
    text: String,
    model: String,
    prompt_tokens: Option<u64>,
    output_tokens: Option<u64>,
    total_tokens: Option<u64>,
    finish_reason: Option<String>,
}

fn valid_model(model: &str) -> bool {
    !model.is_empty()
        && model.len() <= 80
        && model.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | '_'))
}

#[tauri::command]
pub async fn gemini_read_page(
    model: String,
    mime_type: String,
    image_base64: String,
    prompt: String,
) -> Result<ReadResult, String> {
    if !valid_model(&model) {
        return Err("Model name may contain only letters, digits, '-', '.' and '_'.".into());
    }
    if !matches!(mime_type.as_str(), "image/png" | "image/jpeg" | "image/webp") {
        return Err("Only PNG, JPEG and WebP pages can be sent.".into());
    }
    let (key, _) = resolve_key().ok_or("No Gemini API key is configured.")?;
    let body = json!({
        "contents": [{ "role": "user", "parts": [
            { "inline_data": { "mime_type": mime_type, "data": image_base64 } },
            { "text": prompt }
        ]}],
        "generationConfig": { "temperature": 0 }
    });
    let response = reqwest::Client::new()
        .post(format!("{API_BASE}/{model}:generateContent"))
        .header("x-goog-api-key", key)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("Gemini could not be reached: {}", e.without_url()))?;
    let status = response.status();
    let payload: Value = response
        .json()
        .await
        .map_err(|_| format!("Gemini returned an unreadable response (HTTP {status})."))?;
    if !status.is_success() {
        let message = payload["error"]["message"].as_str().unwrap_or("unknown error");
        return Err(format!("Gemini refused the request (HTTP {status}): {message}"));
    }
    parse_response(&payload, model)
}

fn parse_response(payload: &Value, model: String) -> Result<ReadResult, String> {
    let candidate = &payload["candidates"][0];
    let text: String = candidate["content"]["parts"]
        .as_array()
        .map(|parts| parts.iter().filter_map(|p| p["text"].as_str()).collect())
        .unwrap_or_default();
    let finish_reason = candidate["finishReason"].as_str().map(str::to_owned);
    if text.trim().is_empty() {
        let reason = finish_reason
            .as_deref()
            .or(payload["promptFeedback"]["blockReason"].as_str())
            .unwrap_or("no text returned");
        return Err(format!("Gemini returned no text ({reason})."));
    }
    let usage = &payload["usageMetadata"];
    Ok(ReadResult {
        text,
        model,
        prompt_tokens: usage["promptTokenCount"].as_u64(),
        output_tokens: usage["candidatesTokenCount"].as_u64(),
        total_tokens: usage["totalTokenCount"].as_u64(),
        finish_reason,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_text_and_usage() {
        let payload = json!({
            "candidates": [{ "content": { "parts": [{ "text": "Anno " }, { "text": "Domini" }] }, "finishReason": "STOP" }],
            "usageMetadata": { "promptTokenCount": 300, "candidatesTokenCount": 4, "totalTokenCount": 304 }
        });
        let result = parse_response(&payload, "m".into()).unwrap();
        assert_eq!(result.text, "Anno Domini");
        assert_eq!(result.total_tokens, Some(304));
    }

    #[test]
    fn reports_blocked_prompt() {
        let payload = json!({ "promptFeedback": { "blockReason": "SAFETY" } });
        assert!(parse_response(&payload, "m".into()).unwrap_err().contains("SAFETY"));
    }

    #[test]
    fn rejects_path_like_model_names() {
        assert!(valid_model("gemini-2.5-flash"));
        assert!(!valid_model("../evil"));
        assert!(!valid_model("a/b"));
    }
}
