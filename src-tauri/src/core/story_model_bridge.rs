use std::sync::{Arc, Mutex};

use tauri::{AppHandle, Manager};
use tokio::sync::Mutex as AsyncMutex;
use uuid::Uuid;

use crate::core::{
    server::proxy,
    state::{AppState, ServerHandle},
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModelBridgeConnection {
    pub base_url: String,
    pub api_key: String,
}

pub struct ModelBridge {
    connection: Mutex<Option<ModelBridgeConnection>>,
    server_handle: Arc<AsyncMutex<Option<ServerHandle>>>,
    startup_lock: AsyncMutex<()>,
}

impl Default for ModelBridge {
    fn default() -> Self {
        Self {
            connection: Mutex::new(None),
            server_handle: Arc::new(AsyncMutex::new(None)),
            startup_lock: AsyncMutex::new(()),
        }
    }
}

impl ModelBridge {
    fn connection(&self) -> Result<Option<ModelBridgeConnection>, String> {
        self.connection
            .lock()
            .map(|connection| connection.clone())
            .map_err(|_| "model bridge connection lock is poisoned".to_owned())
    }

    pub async fn ensure_started(&self, app: &AppHandle) -> Result<ModelBridgeConnection, String> {
        if let Some(connection) = self.connection()? {
            return Ok(connection);
        }

        let _startup = self.startup_lock.lock().await;
        if let Some(connection) = self.connection()? {
            return Ok(connection);
        }

        let app_state = app.state::<AppState>();
        let api_key = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
        let port = proxy::start_server(
            self.server_handle.clone(),
            "127.0.0.1".to_owned(),
            0,
            api_key.clone(),
            app_state.provider_configs.clone(),
        )
        .await
        .map_err(|error| format!("failed to start the private model bridge: {error}"))?;

        let connection = ModelBridgeConnection {
            base_url: format!("http://127.0.0.1:{port}/v1"),
            api_key,
        };
        self.connection
            .lock()
            .map_err(|_| "model bridge connection lock is poisoned".to_owned())?
            .replace(connection.clone());
        Ok(connection)
    }

    pub async fn shutdown(&self) -> Result<(), String> {
        proxy::stop_server(self.server_handle.clone())
            .await
            .map_err(|error| format!("failed to stop the private model bridge: {error}"))?;
        self.connection
            .lock()
            .map_err(|_| "model bridge connection lock is poisoned".to_owned())?
            .take();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bridge_starts_without_an_exposed_connection() {
        let bridge = ModelBridge::default();
        assert_eq!(bridge.connection().expect("read connection"), None);
    }
}
