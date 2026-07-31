use std::{
    collections::VecDeque,
    env,
    io::{BufRead, BufReader, Read},
    net::TcpListener,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::Duration,
};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};
use uuid::Uuid;

const MAX_LOG_LINES: usize = 200;

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
}

#[derive(Default)]
pub struct EngineRuntime {
    inner: Mutex<RuntimeInner>,
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

    pub(crate) fn stop(&self) -> Result<EngineConnection, String> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "Story Engine runtime lock is poisoned".to_owned())?;
        inner.generation = inner.generation.wrapping_add(1);
        if let Some(mut process) = inner.process.take() {
            let _ = process.kill();
            let _ = process.wait();
        }
        inner.connection.phase = EnginePhase::Stopped;
        inner.connection.session_token = None;
        inner.connection.last_error = None;
        Ok(inner.connection.clone())
    }
}

fn reserve_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("failed to reserve Sidecar port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("failed to inspect Sidecar port: {error}"))
}

fn session_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

fn sidecar_command(app: &AppHandle, port: u16, token: &str) -> Result<Command, String> {
    let mut command = if let Some(binary) = env::var_os("STORY_ENGINE_SIDECAR_BIN") {
        Command::new(binary)
    } else if cfg!(debug_assertions) {
        let project = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../story-engine");
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
    let port_argument = port.to_string();
    command
        .args(["serve", "--host", "127.0.0.1", "--port", &port_argument])
        .env("STORY_ENGINE_SESSION_TOKEN", token)
        .current_dir(data_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    Ok(command)
}

fn capture_logs<R: Read + Send + 'static>(
    reader: R,
    runtime: Arc<EngineRuntime>,
    generation: u64,
    token: String,
) {
    thread::spawn(move || {
        for line in BufReader::new(reader).lines().map_while(Result::ok) {
            let safe_line = line.replace(&token, "[redacted]");
            let Ok(mut inner) = runtime.inner.lock() else {
                return;
            };
            if inner.generation != generation {
                return;
            }
            inner.logs.push_back(safe_line);
            while inner.logs.len() > MAX_LOG_LINES {
                inner.logs.pop_front();
            }
        }
    });
}

fn emit_status(app: &AppHandle, connection: &EngineConnection) {
    let _ = app.emit("story-engine://status", EngineStatusEvent::from(connection));
}

fn set_crashed(app: &AppHandle, runtime: &EngineRuntime, generation: u64, message: String) {
    let Ok(mut inner) = runtime.inner.lock() else {
        return;
    };
    if inner.generation != generation {
        return;
    }
    inner.process = None;
    inner.connection.phase = EnginePhase::Crashed;
    inner.connection.session_token = None;
    inner.connection.last_error = Some(message);
    let connection = inner.connection.clone();
    drop(inner);
    emit_status(app, &connection);
}

fn start(
    app: AppHandle,
    runtime: Arc<EngineRuntime>,
    is_restart: bool,
) -> Result<EngineConnection, String> {
    let _ = runtime.stop()?;
    let port = reserve_port()?;
    let token = session_token();
    let base_url = format!("http://127.0.0.1:{port}");
    let websocket_url = format!("ws://127.0.0.1:{port}");
    let mut command = sidecar_command(&app, port, &token)?;
    let mut process = command
        .spawn()
        .map_err(|error| format!("failed to start Story Engine Sidecar: {error}"))?;
    let stdout = process.stdout.take();
    let stderr = process.stderr.take();

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
        (inner.generation, inner.connection.clone())
    };

    if let Some(stdout) = stdout {
        capture_logs(stdout, runtime.clone(), generation, token.clone());
    }
    if let Some(stderr) = stderr {
        capture_logs(stderr, runtime.clone(), generation, token);
    }
    emit_status(&app, &connection);

    tauri::async_runtime::spawn(monitor(app, runtime, generation, base_url));
    Ok(connection)
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
    for _ in 0..50 {
        tokio::time::sleep(Duration::from_millis(100)).await;
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
pub fn restart_story_engine(
    app: AppHandle,
    runtime: State<'_, Arc<EngineRuntime>>,
) -> Result<EngineConnection, String> {
    start(app, runtime.inner().clone(), true)
}

#[tauri::command]
pub fn stop_story_engine(
    app: AppHandle,
    runtime: State<'_, Arc<EngineRuntime>>,
) -> Result<EngineConnection, String> {
    let connection = runtime.stop()?;
    emit_status(&app, &connection);
    Ok(connection)
}

pub fn start_managed_sidecar(app: AppHandle, runtime: Arc<EngineRuntime>) {
    if let Err(error) = start(app.clone(), runtime.clone(), false) {
        if let Ok(mut inner) = runtime.inner.lock() {
            inner.connection.phase = EnginePhase::Crashed;
            inner.connection.session_token = None;
            inner.connection.last_error = Some(error);
            let connection = inner.connection.clone();
            drop(inner);
            emit_status(&app, &connection);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_session_tokens_are_strong_and_distinct() {
        let first = session_token();
        let second = session_token();

        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|character| character.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }

    #[test]
    fn reserved_port_is_loopback_bindable_after_release() {
        let port = reserve_port().expect("port should be available");
        let rebound = TcpListener::bind(("127.0.0.1", port));

        assert!(rebound.is_ok());
    }

    #[test]
    fn runtime_defaults_to_stopped_without_a_token() {
        let runtime = EngineRuntime::default();
        let connection = runtime.connection().expect("runtime state");

        assert_eq!(connection.phase, EnginePhase::Stopped);
        assert!(connection.session_token.is_none());
    }
}
