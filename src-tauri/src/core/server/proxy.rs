//! Private, remote-only OpenAI-compatible bridge used by Story Engine.

use std::{collections::HashMap, convert::Infallible, net::SocketAddr, sync::Arc};

use futures_util::StreamExt;
use http_body_util::{combinators::BoxBody, BodyExt, Full, StreamBody};
use hyper::{
    body::{Bytes, Frame, Incoming},
    server::conn::http1,
    service::service_fn,
    Method, Request, Response, StatusCode,
};
use hyper_util::rt::TokioIo;
use reqwest::Client;
use serde_json::{json, Value};
use tokio::{net::TcpListener, sync::Mutex};

use crate::core::{
    server::converters::{converter_for, SseAccumulator, StreamState},
    state::{ProviderConfig, ServerHandle},
};

type ResponseBody = BoxBody<Bytes, Infallible>;

fn full(value: impl Into<Bytes>) -> ResponseBody {
    Full::new(value.into()).boxed()
}

fn response(status: StatusCode, content_type: &str, body: ResponseBody) -> Response<ResponseBody> {
    Response::builder()
        .status(status)
        .header(hyper::header::CONTENT_TYPE, content_type)
        .header(hyper::header::CACHE_CONTROL, "no-store")
        .body(body)
        .expect("static response metadata is valid")
}

fn json_error(status: StatusCode, message: impl Into<String>) -> Response<ResponseBody> {
    response(
        status,
        "application/json",
        full(json!({ "error": { "message": message.into() } }).to_string()),
    )
}

fn authorized(request: &Request<Incoming>, api_key: &str) -> bool {
    request
        .headers()
        .get(hyper::header::AUTHORIZATION)
        .and_then(|header| header.to_str().ok())
        .and_then(|header| header.strip_prefix("Bearer "))
        .is_some_and(|token| token == api_key)
}

fn resolve_provider(
    model_ref: &str,
    configs: &HashMap<String, ProviderConfig>,
) -> Option<(ProviderConfig, String)> {
    if let Some((provider, model)) = model_ref.split_once('/') {
        if let Some(config) = configs.get(provider) {
            return Some((config.clone(), model.to_owned()));
        }
    }
    configs
        .values()
        .find(|config| config.models.iter().any(|model| model == model_ref))
        .cloned()
        .map(|config| (config, model_ref.to_owned()))
}

async fn models(
    provider_configs: &Arc<Mutex<HashMap<String, ProviderConfig>>>,
) -> Response<ResponseBody> {
    let configs = provider_configs.lock().await;
    let data = configs
        .values()
        .flat_map(|provider| {
            provider.models.iter().map(|model| {
                json!({
                    "id": format!("{}/{}", provider.provider, model),
                    "object": "model",
                    "created": 0,
                    "owned_by": provider.provider,
                })
            })
        })
        .collect::<Vec<_>>();
    response(
        StatusCode::OK,
        "application/json",
        full(json!({ "object": "list", "data": data }).to_string()),
    )
}

async fn forward_completion(
    request: Request<Incoming>,
    provider_configs: Arc<Mutex<HashMap<String, ProviderConfig>>>,
) -> Response<ResponseBody> {
    let bytes = match request.into_body().collect().await {
        Ok(body) => body.to_bytes(),
        Err(error) => {
            return json_error(
                StatusCode::BAD_REQUEST,
                format!("failed to read request body: {error}"),
            )
        }
    };
    let mut body: Value = match serde_json::from_slice(&bytes) {
        Ok(Value::Object(value)) => Value::Object(value),
        Ok(_) => return json_error(StatusCode::BAD_REQUEST, "request body must be an object"),
        Err(error) => return json_error(StatusCode::BAD_REQUEST, format!("invalid JSON: {error}")),
    };
    let Some(model_ref) = body.get("model").and_then(Value::as_str).map(str::to_owned) else {
        return json_error(StatusCode::BAD_REQUEST, "request body must contain model");
    };

    let resolved = {
        let configs = provider_configs.lock().await;
        resolve_provider(&model_ref, &configs)
    };
    let Some((provider, upstream_model)) = resolved else {
        return json_error(
            StatusCode::NOT_FOUND,
            format!("cloud model '{model_ref}' is not configured in Jan"),
        );
    };
    body["model"] = Value::String(upstream_model);

    let Some(base_url) = provider.base_url.as_deref() else {
        return json_error(
            StatusCode::BAD_GATEWAY,
            format!("provider '{}' has no base URL", provider.provider),
        );
    };
    let converter = converter_for(provider.api_type.as_deref());
    let path = converter
        .as_ref()
        .map(|item| item.upstream_path(&body))
        .unwrap_or_else(|| "/chat/completions".to_owned());
    let url = format!("{}{}", base_url.trim_end_matches('/'), path);
    let payload = converter
        .as_ref()
        .map(|item| item.convert_request(&body))
        .unwrap_or_else(|| body.clone());
    let streaming = body.get("stream").and_then(Value::as_bool).unwrap_or(false);
    let client = Client::new();
    let keys = provider.bearer_key_chain();
    let attempts = if keys.is_empty() {
        vec![None]
    } else {
        keys.iter().map(Some).collect::<Vec<_>>()
    };
    let mut upstream = None;

    for (index, key) in attempts.iter().enumerate() {
        let mut builder = client.post(&url).json(&payload);
        if let Some(key) = key {
            if let Some(item) = converter.as_ref() {
                let (name, value) = item.auth_header(key);
                builder = builder.header(name, value);
            } else {
                builder = builder.bearer_auth(key);
            }
        }
        if let Some(item) = converter.as_ref() {
            for (name, value) in item.extra_headers() {
                builder = builder.header(name, value);
            }
        }
        for header in &provider.custom_headers {
            builder = builder.header(&header.header, &header.value);
        }
        match builder.send().await {
            Ok(result)
                if matches!(result.status().as_u16(), 401 | 403 | 429)
                    && index + 1 < attempts.len() => {}
            Ok(result) => {
                upstream = Some(result);
                break;
            }
            Err(error) => {
                return json_error(
                    StatusCode::BAD_GATEWAY,
                    format!("cloud provider request failed: {error}"),
                )
            }
        }
    }

    let Some(upstream) = upstream else {
        return json_error(StatusCode::BAD_GATEWAY, "cloud provider request failed");
    };
    let status =
        StatusCode::from_u16(upstream.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
    if !upstream.status().is_success() {
        let content = upstream.bytes().await.unwrap_or_default();
        let detail = content.slice(..content.len().min(65_536));
        return response(status, "application/json", full(detail));
    }

    if !streaming {
        let content = match upstream.bytes().await {
            Ok(content) => content,
            Err(error) => {
                return json_error(
                    StatusCode::BAD_GATEWAY,
                    format!("failed to read provider response: {error}"),
                )
            }
        };
        if let Some(converter) = converter {
            let parsed: Value = match serde_json::from_slice(&content) {
                Ok(parsed) => parsed,
                Err(error) => {
                    return json_error(
                        StatusCode::BAD_GATEWAY,
                        format!("provider returned invalid JSON: {error}"),
                    )
                }
            };
            return response(
                status,
                "application/json",
                full(converter.convert_response(&parsed).to_string()),
            );
        }
        return response(status, "application/json", full(content));
    }

    let (sender, receiver) = tokio::sync::mpsc::channel(32);
    tokio::spawn(async move {
        let mut stream = upstream.bytes_stream();
        if let Some(converter) = converter {
            let mut accumulator = SseAccumulator::new();
            let mut state = StreamState::default();
            while let Some(chunk) = stream.next().await {
                let Ok(chunk) = chunk else { break };
                for event in accumulator.push(&String::from_utf8_lossy(&chunk)) {
                    for data in converter.convert_stream_event(&event, &mut state) {
                        if sender
                            .send(Ok(Frame::data(Bytes::from(format!("data: {data}\n\n")))))
                            .await
                            .is_err()
                        {
                            return;
                        }
                    }
                }
            }
            if let Some(event) = accumulator.finish() {
                for data in converter.convert_stream_event(&event, &mut state) {
                    let _ = sender
                        .send(Ok(Frame::data(Bytes::from(format!("data: {data}\n\n")))))
                        .await;
                }
            }
        } else {
            while let Some(chunk) = stream.next().await {
                let Ok(chunk) = chunk else { break };
                if sender.send(Ok(Frame::data(chunk))).await.is_err() {
                    return;
                }
            }
        }
    });
    let stream = tokio_stream::wrappers::ReceiverStream::new(receiver);
    let body = BodyExt::boxed(StreamBody::new(stream));
    response(status, "text/event-stream", body)
}

async fn handle(
    request: Request<Incoming>,
    api_key: String,
    provider_configs: Arc<Mutex<HashMap<String, ProviderConfig>>>,
) -> Result<Response<ResponseBody>, Infallible> {
    if !authorized(&request, &api_key) {
        return Ok(json_error(StatusCode::UNAUTHORIZED, "invalid bridge token"));
    }
    let response = match (request.method(), request.uri().path()) {
        (&Method::GET, "/v1/models") => models(&provider_configs).await,
        (&Method::POST, "/v1/chat/completions") => {
            forward_completion(request, provider_configs).await
        }
        _ => json_error(StatusCode::NOT_FOUND, "route not found"),
    };
    Ok(response)
}

pub async fn start_server(
    server_handle: Arc<Mutex<Option<ServerHandle>>>,
    host: String,
    port: u16,
    api_key: String,
    provider_configs: Arc<Mutex<HashMap<String, ProviderConfig>>>,
) -> Result<u16, Box<dyn std::error::Error + Send + Sync>> {
    let listener = TcpListener::bind(format!("{host}:{port}")).await?;
    let actual_port = listener.local_addr()?.port();
    let handle = tokio::spawn(async move {
        loop {
            let (stream, _peer): (_, SocketAddr) = listener.accept().await?;
            let io = TokioIo::new(stream);
            let token = api_key.clone();
            let configs = provider_configs.clone();
            tokio::spawn(async move {
                if let Err(error) = http1::Builder::new()
                    .serve_connection(
                        io,
                        service_fn(move |request| handle(request, token.clone(), configs.clone())),
                    )
                    .await
                {
                    log::debug!("model bridge connection closed: {error}");
                }
            });
        }
        #[allow(unreachable_code)]
        Ok(())
    });
    server_handle.lock().await.replace(handle);
    Ok(actual_port)
}

pub async fn stop_server(
    server_handle: Arc<Mutex<Option<ServerHandle>>>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if let Some(handle) = server_handle.lock().await.take() {
        handle.abort();
    }
    Ok(())
}

pub async fn is_server_running(server_handle: Arc<Mutex<Option<ServerHandle>>>) -> bool {
    server_handle
        .lock()
        .await
        .as_ref()
        .is_some_and(|handle| !handle.is_finished())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn provider(name: &str, models: &[&str]) -> ProviderConfig {
        ProviderConfig {
            provider: name.to_owned(),
            base_url: Some("https://example.invalid/v1".to_owned()),
            models: models.iter().map(|item| (*item).to_owned()).collect(),
            ..ProviderConfig::default()
        }
    }

    #[test]
    fn resolves_explicit_provider_model_reference() {
        let configs = HashMap::from([("cloud".to_owned(), provider("cloud", &["story-model"]))]);
        let (resolved, model) = resolve_provider("cloud/story-model", &configs).unwrap();
        assert_eq!(resolved.provider, "cloud");
        assert_eq!(model, "story-model");
    }

    #[test]
    fn rejects_unknown_model_reference() {
        let configs = HashMap::from([("cloud".to_owned(), provider("cloud", &["story-model"]))]);
        assert!(resolve_provider("missing", &configs).is_none());
    }
}
