mod runtime;

use std::sync::Arc;

use runtime::EngineRuntime;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(Arc::new(EngineRuntime::default()))
        .invoke_handler(tauri::generate_handler![
            runtime::engine_runtime_state,
            runtime::engine_runtime_logs,
            runtime::restart_story_engine,
            runtime::stop_story_engine,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let runtime = app.state::<Arc<EngineRuntime>>().inner().clone();
            runtime::start_managed_sidecar(handle, runtime);
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                let runtime = window.state::<Arc<EngineRuntime>>();
                let _ = runtime.stop();
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run Story Engine desktop application");
}
