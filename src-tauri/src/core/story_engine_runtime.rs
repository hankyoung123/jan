use std::{
    collections::VecDeque,
    env,
    ffi::OsString,
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

use log::Level;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};
use uuid::Uuid;

use crate::core::story_model_bridge::{ModelBridge, ModelBridgeConnection};

const MAX_LOG_LINES: usize = 200;
const SIDECAR_LOG_TARGET: &str = "story_engine::sidecar";
const HEALTH_INTERVAL: Duration = Duration::from_millis(100);
const DEFAULT_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EnginePhase {
    Starting,
    Ready,
    #[default]
    Stopped,
    Crashed,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct EngineConnection {
    pub phase: EnginePhase,
    pub base_url: Option<String>,
    pub websocket_url: Option<String>,
    pub session_token: Option<String>,
    pub restart_count: u32,
    pub last_error: Option<String>,
}

/// Status notifications deliberately omit the bearer token. The webview may
/// obtain it only through the explicit `engine_runtime_state` command.
#[derive(Clone, Debug, Serialize)]
struct EngineStatusEvent {
    phase: EnginePhase,
    base_url: Option<String>,
    websocket_url: Option<String>,
    restart_count: u32,
    last_error: Option<String>,
}

impl From<&EngineConnection> for EngineStatusEvent {
    fn from(connection: &EngineConnection) -> Self {
        Self {
            phase: connection.phase.clone(),
            base_url: connection.base_url.clone(),
            websocket_url: connection.websocket_url.clone(),
            restart_count: connection.restart_count,
            last_error: connection.last_error.clone(),
        }
    }
}

#[derive(Default)]
struct RuntimeInner {
    connection: EngineConnection,
    process: Option<Child>,
    generation: u64,
    logs: VecDeque<String>,
    log_secrets: Vec<String>,
}

pub struct EngineRuntime {
    inner: Mutex<RuntimeInner>,
    model_bridge: Arc<ModelBridge>,
}

impl Default for EngineRuntime {
    fn default() -> Self {
        Self {
            inner: Mutex::new(RuntimeInner::default()),
            model_bridge: Arc::new(ModelBridge::default()),
        }
    }
}

impl EngineRuntime {
    fn connection(&self) -> Result<EngineConnection, String> {
        self.inner
            .lock()
            .map(|inner| inner.connection.clone())
            .map_err(|_| "Story Engine runtime lock is poisoned".to_owned())
    }

    fn logs(&self) -> Result<Vec<String>, String> {
        self.inner
            .lock()
            .map(|inner| inner.logs.iter().cloned().collect())
            .map_err(|_| "Story Engine runtime lock is poisoned".to_owned())
    }

    pub fn stop(&self) -> Result<EngineConnection, String> {
        let (process, connection) = {
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| "Story Engine runtime lock is poisoned".to_owned())?;
            inner.generation = inner.generation.wrapping_add(1);
            let process = inner.process.take();
            inner.log_secrets.clear();
            inner.connection.phase = EnginePhase::Stopped;
            inner.connection.session_token = None;
            inner.connection.last_error = None;
            (process, inner.connection.clone())
        };
        terminate_process(process);
        Ok(connection)
    }

    pub async fn shutdown(&self) -> Result<EngineConnection, String> {
        let connection = self.stop()?;
        self.model_bridge.shutdown().await?;
        Ok(connection)
    }
}

fn terminate_process(process: Option<Child>) {
    if let Some(mut process) = process {
        let _ = process.kill();
        let _ = process.wait();
    }
}

fn startup_timeout_from(value: Option<OsString>) -> Duration {
    value
        .and_then(|value| value.to_string_lossy().parse::<u64>().ok())
        .filter(|seconds| (5..=120).contains(seconds))
        .map(Duration::from_secs)
        .unwrap_or(DEFAULT_STARTUP_TIMEOUT)
}

fn startup_timeout() -> Duration {
    startup_timeout_from(env::var_os("STORY_ENGINE_STARTUP_TIMEOUT_SECONDS"))
}

fn session_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

fn development_project_path(manifest_dir: &Path) -> PathBuf {
    manifest_dir
        .parent()
        .unwrap_or(manifest_dir)
        .join("apps/story-engine")
}

fn configure_model_bridge(command: &mut Command, bridge: &ModelBridgeConnection) {
    command
        .env("STORY_ENGINE_MODEL_BASE_URL", &bridge.base_url)
        .env("STORY_ENGINE_MODEL_API_KEY", &bridge.api_key);
}

fn redact_secrets(line: &str, secrets: &[String]) -> String {
    secrets
        .iter()
        .filter(|secret| !secret.is_empty())
        .fold(line.to_owned(), |safe_line, secret| {
            safe_line.replace(secret, "[redacted]")
        })
}

fn record_runtime_log(runtime: &EngineRuntime, generation: u64, line: &str, level: Level) {
    let safe_line = {
        let Ok(mut inner) = runtime.inner.lock() else {
            return;
        };
        if inner.generation != generation {
            return;
        }
        let safe_line = redact_secrets(line, &inner.log_secrets);
        inner.logs.push_back(safe_line.clone());
        while inner.logs.len() > MAX_LOG_LINES {
            inner.logs.pop_front();
        }
        safe_line
    };

    // Jan's app logger persists this target to app.log and exposes it through
    // the existing desktop log viewer. Only the redacted value reaches it.
    log::log!(target: SIDECAR_LOG_TARGET, level, "{safe_line}");
}

fn record_current_runtime_log(runtime: &EngineRuntime, line: &str, level: Level) {
    let generation = match runtime.inner.lock() {
        Ok(inner) => inner.generation,
        Err(_) => return,
    };
    record_runtime_log(runtime, generation, line, level);
}

fn sidecar_command(
    app: &AppHandle,
    port_file: &Path,
    token: &str,
    model_bridge: &ModelBridgeConnection,
) -> Result<Command, String> {
    let mut command = if let Some(binary) = env::var_os("STORY_ENGINE_SIDECAR_BIN") {
        Command::new(binary)
    } else if cfg!(debug_assertions) {
        let project = development_project_path(Path::new(env!("CARGO_MANIFEST_DIR")));
        let mut command =
            Command::new(env::var_os("STORY_ENGINE_UV_BIN").unwrap_or_else(|| "uv".into()));
        command.args(["run", "--project"]);
        command.arg(project);
        command.arg("story-engine");
        command
    } else {
        let binary_name = if cfg!(windows) {
            "story-engine.exe"
        } else {
            "story-engine"
        };
        let binary = app
            .path()
            .resource_dir()
            .map_err(|error| format!("failed to resolve app resources: {error}"))?
            .join("story-engine")
            .join(binary_name);
        Command::new(binary)
    };

    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data directory: {error}"))?;
    std::fs::create_dir_all(&data_dir)
        .map_err(|error| format!("failed to create app data directory: {error}"))?;
    command
        .args(["serve", "--host", "127.0.0.1", "--port", "0", "--port-file"])
        .arg(port_file)
        .env("STORY_ENGINE_SESSION_TOKEN", token)
        .current_dir(data_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_model_bridge(&mut command, model_bridge);
    Ok(command)
}

async fn wait_for_announced_port(
    process: &mut Child,
    port_file: &Path,
    timeout: Duration,
) -> Result<u16, String> {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if let Ok(value) = std::fs::read_to_string(port_file) {
            let parsed = value.trim().parse::<u16>().ok().filter(|port| *port > 0);
            let _ = std::fs::remove_file(port_file);
            return parsed.ok_or_else(|| "Sidecar announced an invalid port".to_owned());
        }
        if let Some(status) = process
            .try_wait()
            .map_err(|error| format!("failed to inspect Sidecar process: {error}"))?
        {
            return Err(format!(
                "Story Engine exited before announcing its port: {status}"
            ));
        }
        tokio::time::sleep(HEALTH_INTERVAL).await;
    }
    Err("Story Engine port announcement timed out".to_owned())
}

fn capture_logs<R: Read + Send + 'static>(
    reader: R,
    runtime: Arc<EngineRuntime>,
    generation: u64,
    level: Level,
) {
    thread::spawn(move || {
        for line in BufReader::new(reader).lines().map_while(Result::ok) {
            record_runtime_log(&runtime, generation, &line, level);
        }
    });
}

fn emit_status(app: &AppHandle, connection: &EngineConnection) {
    let _ = app.emit("story-engine://status", EngineStatusEvent::from(connection));
}

fn set_crashed(app: &AppHandle, runtime: &EngineRuntime, generation: u64, message: String) {
    let crash_log = format!("Story Engine crashed: {message}");
    let (process, connection) = {
        let Ok(mut inner) = runtime.inner.lock() else {
            return;
        };
        if inner.generation != generation {
            return;
        }
        let process = inner.process.take();
        inner.connection.phase = EnginePhase::Crashed;
        inner.connection.session_token = None;
        inner.connection.last_error = Some(message);
        (process, inner.connection.clone())
    };
    record_runtime_log(runtime, generation, &crash_log, Level::Error);
    terminate_process(process);
    emit_status(app, &connection);
}

async fn start(
    app: AppHandle,
    runtime: Arc<EngineRuntime>,
    is_restart: bool,
) -> Result<EngineConnection, String> {
    let _ = runtime.stop()?;
    let model_bridge = runtime.model_bridge.ensure_started(&app).await?;
    let token = session_token();
    let port_file = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("failed to resolve app data directory: {error}"))?
        .join(format!(
            ".story-engine-sidecar-{}.port",
            Uuid::new_v4().simple()
        ));
    let mut command = sidecar_command(&app, &port_file, &token, &model_bridge)?;
    let mut process = command
        .spawn()
        .map_err(|error| format!("failed to start Story Engine Sidecar: {error}"))?;
    let port = match wait_for_announced_port(&mut process, &port_file, startup_timeout()).await {
        Ok(port) => port,
        Err(error) => {
            terminate_process(Some(process));
            let _ = std::fs::remove_file(&port_file);
            return Err(error);
        }
    };
    let base_url = format!("http://127.0.0.1:{port}");
    let websocket_url = format!("ws://127.0.0.1:{port}/ws/events");
    let stdout = process.stdout.take();
    let stderr = process.stderr.take();
    let log_secrets = vec![token.clone(), model_bridge.api_key.clone()];

    let (generation, connection) = {
        let mut inner = runtime
            .inner
            .lock()
            .map_err(|_| "Story Engine runtime lock is poisoned".to_owned())?;
        inner.generation = inner.generation.wrapping_add(1);
        if is_restart {
            inner.connection.restart_count = inner.connection.restart_count.saturating_add(1);
        }
        inner.connection.phase = EnginePhase::Starting;
        inner.connection.base_url = Some(base_url.clone());
        inner.connection.websocket_url = Some(websocket_url);
        inner.connection.session_token = Some(token.clone());
        inner.connection.last_error = None;
        inner.process = Some(process);
        inner.log_secrets = log_secrets;
        (inner.generation, inner.connection.clone())
    };

    record_runtime_log(
        &runtime,
        generation,
        "Story Engine Sidecar process started",
        Level::Info,
    );
    if let Some(stdout) = stdout {
        capture_logs(stdout, runtime.clone(), generation, Level::Info);
    }
    if let Some(stderr) = stderr {
        capture_logs(stderr, runtime.clone(), generation, Level::Error);
    }
    emit_status(&app, &connection);

    tauri::async_runtime::spawn(monitor(app, runtime, generation, base_url));
    Ok(connection)
}

async fn start_with_bridge_cleanup(
    app: AppHandle,
    runtime: Arc<EngineRuntime>,
    is_restart: bool,
) -> Result<EngineConnection, String> {
    match start(app, runtime.clone(), is_restart).await {
        Ok(connection) => Ok(connection),
        Err(start_error) => {
            let final_error = match runtime.model_bridge.shutdown().await {
                Ok(()) => start_error,
                Err(shutdown_error) => {
                    format!("{start_error}; failed to clean up model bridge: {shutdown_error}")
                }
            };
            record_current_runtime_log(
                &runtime,
                &format!("Story Engine failed to start: {final_error}"),
                Level::Error,
            );
            Err(final_error)
        }
    }
}

async fn monitor(app: AppHandle, runtime: Arc<EngineRuntime>, generation: u64, base_url: String) {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_millis(500))
        .build()
    {
        Ok(client) => client,
        Err(error) => {
            set_crashed(
                &app,
                &runtime,
                generation,
                format!("failed to create health client: {error}"),
            );
            return;
        }
    };

    let mut ready = false;
    let health_started = Instant::now();
    while health_started.elapsed() < startup_timeout() {
        tokio::time::sleep(HEALTH_INTERVAL).await;
        let exited = {
            let Ok(mut inner) = runtime.inner.lock() else {
                return;
            };
            if inner.generation != generation {
                return;
            }
            inner
                .process
                .as_mut()
                .and_then(|process| process.try_wait().ok().flatten())
        };
        if let Some(status) = exited {
            set_crashed(
                &app,
                &runtime,
                generation,
                format!("Story Engine exited during startup: {status}"),
            );
            return;
        }
        if client
            .get(format!("{base_url}/health"))
            .send()
            .await
            .is_ok_and(|response| response.status().is_success())
        {
            ready = true;
            break;
        }
    }

    if !ready {
        set_crashed(
            &app,
            &runtime,
            generation,
            "Story Engine health check timed out".to_owned(),
        );
        return;
    }

    let connection = {
        let Ok(mut inner) = runtime.inner.lock() else {
            return;
        };
        if inner.generation != generation {
            return;
        }
        inner.connection.phase = EnginePhase::Ready;
        inner.connection.clone()
    };
    record_runtime_log(
        &runtime,
        generation,
        "Story Engine Sidecar is ready",
        Level::Info,
    );
    emit_status(&app, &connection);

    loop {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let status = {
            let Ok(mut inner) = runtime.inner.lock() else {
                return;
            };
            if inner.generation != generation {
                return;
            }
            inner
                .process
                .as_mut()
                .and_then(|process| process.try_wait().ok().flatten())
        };
        if let Some(status) = status {
            set_crashed(
                &app,
                &runtime,
                generation,
                format!("Story Engine exited unexpectedly: {status}"),
            );
            return;
        }
    }
}

#[tauri::command]
pub fn engine_runtime_state(
    runtime: State<'_, Arc<EngineRuntime>>,
) -> Result<EngineConnection, String> {
    runtime.connection()
}

#[tauri::command]
pub fn engine_runtime_logs(runtime: State<'_, Arc<EngineRuntime>>) -> Result<Vec<String>, String> {
    runtime.logs()
}

#[tauri::command]
pub async fn start_story_engine(
    app: AppHandle,
    runtime: State<'_, Arc<EngineRuntime>>,
) -> Result<EngineConnection, String> {
    let connection = runtime.connection()?;
    if matches!(connection.phase, EnginePhase::Starting | EnginePhase::Ready) {
        return Ok(connection);
    }
    start_with_bridge_cleanup(app, runtime.inner().clone(), false).await
}

#[tauri::command]
pub async fn restart_story_engine(
    app: AppHandle,
    runtime: State<'_, Arc<EngineRuntime>>,
) -> Result<EngineConnection, String> {
    start_with_bridge_cleanup(app, runtime.inner().clone(), true).await
}

#[tauri::command]
pub async fn stop_story_engine(
    app: AppHandle,
    runtime: State<'_, Arc<EngineRuntime>>,
) -> Result<EngineConnection, String> {
    let connection = runtime.shutdown().await?;
    emit_status(&app, &connection);
    Ok(connection)
}

pub fn start_managed_sidecar(app: AppHandle, runtime: Arc<EngineRuntime>) {
    tauri::async_runtime::spawn(async move {
        if let Err(error) = start_with_bridge_cleanup(app.clone(), runtime.clone(), false).await {
            let connection = {
                let Ok(mut inner) = runtime.inner.lock() else {
                    return;
                };
                inner.connection.phase = EnginePhase::Crashed;
                inner.connection.session_token = None;
                inner.connection.last_error = Some(error);
                inner.connection.clone()
            };
            emit_status(&app, &connection);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Once;

    struct CapturingLogger;

    static CAPTURED_LOGS: Mutex<Vec<(String, Level, String)>> = Mutex::new(Vec::new());
    static LOGGER_INIT: Once = Once::new();

    impl log::Log for CapturingLogger {
        fn enabled(&self, _metadata: &log::Metadata<'_>) -> bool {
            true
        }

        fn log(&self, record: &log::Record<'_>) {
            if record.target() == SIDECAR_LOG_TARGET {
                CAPTURED_LOGS.lock().expect("captured logger").push((
                    record.target().to_owned(),
                    record.level(),
                    record.args().to_string(),
                ));
            }
        }

        fn flush(&self) {}
    }

    fn install_capturing_logger() {
        LOGGER_INIT.call_once(|| {
            log::set_logger(&CapturingLogger).expect("test logger");
            log::set_max_level(log::LevelFilter::Trace);
        });
    }

    #[test]
    fn development_sidecar_uses_the_repository_story_engine() {
        assert_eq!(
            development_project_path(Path::new("/workspace/src-tauri")),
            PathBuf::from("/workspace/apps/story-engine")
        );
    }

    #[test]
    fn model_bridge_credentials_are_passed_only_through_the_environment() {
        let connection = ModelBridgeConnection {
            base_url: "http://127.0.0.1:49152/v1".to_owned(),
            api_key: "private-bridge-token".to_owned(),
        };
        let mut command = Command::new("story-engine");

        configure_model_bridge(&mut command, &connection);

        let environment = command
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_string_lossy().into_owned(),
                    value.map(|item| item.to_string_lossy().into_owned()),
                )
            })
            .collect::<std::collections::HashMap<_, _>>();
        assert_eq!(
            environment.get("STORY_ENGINE_MODEL_BASE_URL"),
            Some(&Some(connection.base_url))
        );
        assert_eq!(
            environment.get("STORY_ENGINE_MODEL_API_KEY"),
            Some(&Some(connection.api_key))
        );
        assert!(command.get_args().next().is_none());
    }

    #[test]
    fn captured_logs_redact_session_and_model_bridge_tokens() {
        install_capturing_logger();
        let runtime = EngineRuntime::default();
        {
            let mut inner = runtime.inner.lock().expect("runtime state");
            inner.generation = 7;
            inner.log_secrets = vec![
                "session-secret".to_owned(),
                "private-bridge-token".to_owned(),
            ];
        }

        record_runtime_log(
            &runtime,
            7,
            "session-secret called private-bridge-token; harmless context remains",
            log::Level::Error,
        );

        assert_eq!(
            runtime.logs().expect("captured logs"),
            vec!["[redacted] called [redacted]; harmless context remains"]
        );
        assert!(CAPTURED_LOGS.lock().expect("captured logger").iter().any(
            |(target, level, message)| {
                target == SIDECAR_LOG_TARGET
                    && *level == Level::Error
                    && message == "[redacted] called [redacted]; harmless context remains"
            }
        ));
    }

    #[test]
    fn stale_sidecar_output_cannot_enter_the_runtime_or_persistent_logger() {
        install_capturing_logger();
        let runtime = EngineRuntime::default();
        runtime.inner.lock().expect("runtime state").generation = 9;
        let stale_message = "unique output from a stopped Sidecar";

        record_runtime_log(&runtime, 8, stale_message, log::Level::Info);

        assert!(runtime.logs().expect("captured logs").is_empty());
        assert!(!CAPTURED_LOGS
            .lock()
            .expect("captured logger")
            .iter()
            .any(|(_, _, message)| message == stale_message));
    }

    #[test]
    fn status_events_never_serialize_the_session_token() {
        let connection = EngineConnection {
            phase: EnginePhase::Ready,
            base_url: Some("http://127.0.0.1:41000".to_owned()),
            websocket_url: Some("ws://127.0.0.1:41000/ws/events".to_owned()),
            session_token: Some("runtime-secret".to_owned()),
            restart_count: 1,
            last_error: None,
        };

        let payload = serde_json::to_value(EngineStatusEvent::from(&connection))
            .expect("status event serializes");

        assert_eq!(payload["phase"], "ready");
        assert!(payload.get("session_token").is_none());
        assert!(!payload.to_string().contains("runtime-secret"));
    }

    #[test]
    fn generated_session_tokens_are_strong_and_distinct() {
        let first = session_token();
        let second = session_token();

        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|character| character.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }

    #[test]
    fn startup_timeout_is_bounded_and_configurable() {
        assert_eq!(startup_timeout_from(None), Duration::from_secs(30));
        assert_eq!(
            startup_timeout_from(Some(OsString::from("20"))),
            Duration::from_secs(20)
        );
        assert_eq!(
            startup_timeout_from(Some(OsString::from("1"))),
            Duration::from_secs(30)
        );
        assert_eq!(
            startup_timeout_from(Some(OsString::from("invalid"))),
            Duration::from_secs(30)
        );
    }

    #[test]
    fn runtime_defaults_to_stopped_without_a_token() {
        let runtime = EngineRuntime::default();
        let connection = runtime.connection().expect("runtime state");

        assert_eq!(connection.phase, EnginePhase::Stopped);
        assert!(connection.session_token.is_none());
    }
}
