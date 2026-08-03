pub mod core;

#[cfg(not(any(target_os = "android", target_os = "ios")))]
use core::story_engine_runtime::EngineRuntime;
use core::{
    app::commands::get_jan_data_folder_path,
    mcp::models::McpSettings,
    setup::{self, setup_mcp},
    state::AppState,
};
use jan_utils::generate_app_token;
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
};
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_store::StoreExt;
use tokio::sync::Mutex;

macro_rules! invoke_commands_with_extras {
    ($($extra:path),* $(,)?) => {
        tauri::generate_handler![
        // FS commands - Deperecate soon
        core::filesystem::commands::join_path,
        core::filesystem::commands::mkdir,
        core::filesystem::commands::exists_sync,
        core::filesystem::commands::readdir_sync,
        core::filesystem::commands::read_file_sync,
        core::filesystem::commands::rm,
        core::filesystem::commands::mv,
        core::filesystem::commands::file_stat,
        core::filesystem::commands::write_file_sync,
        core::filesystem::commands::write_yaml,
        core::filesystem::commands::read_yaml,
        core::filesystem::commands::decompress,
        core::filesystem::commands::open_dialog,
        core::filesystem::commands::save_dialog,
        // App configuration commands
        core::app::commands::get_app_configurations,
        core::app::commands::get_user_home_path,
        core::app::commands::update_app_configuration,
        core::app::commands::get_jan_data_folder_path,
        core::app::commands::get_configuration_file_path,
        core::app::commands::default_data_folder_path,
        core::app::commands::change_app_data_folder,
        core::app::commands::app_token,
        // Backend-owned settings store (webview zustand persistence)
        core::app::settings_store::settings_get,
        core::app::settings_store::settings_set,
        core::app::settings_store::settings_remove,
        core::server::provider_secrets::set_secret,
        core::server::provider_secrets::get_secret,
        // System commands
        core::system::commands::relaunch,
        core::system::commands::open_app_directory,
        core::system::commands::open_file_explorer,
        core::system::commands::factory_reset,
        core::system::commands::take_pending_webdata_reset,
        core::system::commands::read_logs,
        core::system::commands::is_library_available,
        // Remote provider commands
        core::server::remote_provider_commands::register_provider_config,
        core::server::remote_provider_commands::unregister_provider_config,
        core::server::remote_provider_commands::delete_provider_keys,
        core::server::remote_provider_commands::get_provider_config,
        core::server::remote_provider_commands::get_provider_keys,
        core::server::remote_provider_commands::list_provider_configs,
        // MCP commands
        core::mcp::commands::get_tools,
        core::mcp::commands::get_tools_for_servers,
        core::mcp::commands::get_server_summaries,
        core::mcp::commands::call_tool,
        core::mcp::commands::cancel_tool_call,
        core::mcp::commands::restart_mcp_servers,
        core::mcp::commands::get_connected_servers,
        core::mcp::commands::save_mcp_configs,
        core::mcp::commands::get_mcp_configs,
        core::mcp::commands::activate_mcp_server,
        core::mcp::commands::deactivate_mcp_server,
        core::mcp::commands::check_jan_browser_extension_connected,
        // Threads
        core::threads::commands::list_threads,
        core::threads::commands::create_thread,
        core::threads::commands::modify_thread,
        core::threads::commands::delete_thread,
        core::threads::commands::list_messages,
        core::threads::commands::create_message,
        core::threads::commands::modify_message,
        core::threads::commands::delete_message,
        core::threads::commands::get_thread_assistant,
        core::threads::commands::create_thread_assistant,
        core::threads::commands::modify_thread_assistant,
        // App lifecycle
        confirm_exit,
        // Theme
        core::setup::get_system_theme,
        core::setup::set_gtk_prefer_dark,
        core::setup::get_titlebar_layout,
        $(
            $extra,
        )*
    ]
    };
}

static SHUTTING_DOWN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

#[cfg(not(any(target_os = "android", target_os = "ios")))]
fn updater_release_configured(updater_config: Option<&serde_json::Value>) -> bool {
    let Some(config) = updater_config.and_then(serde_json::Value::as_object) else {
        return false;
    };

    let has_endpoints = config
        .get("endpoints")
        .and_then(serde_json::Value::as_array)
        .is_some_and(|endpoints| !endpoints.is_empty());
    let has_pubkey = config
        .get("pubkey")
        .and_then(serde_json::Value::as_str)
        .is_some_and(|pubkey| !pubkey.trim().is_empty());

    has_endpoints && has_pubkey
}

#[tauri::command]
async fn confirm_exit<R: tauri::Runtime>(_app_handle: tauri::AppHandle<R>) {
    SHUTTING_DOWN.store(true, std::sync::atomic::Ordering::SeqCst);
    tokio::spawn(async {
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        std::process::exit(0);
    });
}

#[cfg(not(target_os = "macos"))]
fn is_proxy_server_running<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> bool {
    use tauri::Manager;
    app.try_state::<AppState>()
        .and_then(|s| s.server_handle.try_lock().ok().map(|g| g.is_some()))
        .unwrap_or(false)
}

#[cfg_attr(
    all(mobile, any(target_os = "android", target_os = "ios")),
    tauri::mobile_entry_point
)]
pub fn run() {
    let mut builder = tauri::Builder::default();
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|_app, argv, _cwd| {
          println!("a new app instance was opened with {argv:?} and the deep link event was already triggered");
          // when defining deep link schemes at runtime, you must also check `argv` here
        }));
    }

    let mut app_builder = builder
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_websearch::init());

    #[cfg(feature = "deep-link")]
    {
        app_builder = app_builder.plugin(tauri_plugin_deep_link::init());
    }

    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    {
        app_builder = app_builder.manage(Arc::new(EngineRuntime::default()));
    }

    // Desktop: include updater commands
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    let app_builder = app_builder.invoke_handler(invoke_commands_with_extras![
        // Custom updater commands (desktop only)
        core::updater::commands::check_for_app_updates,
        core::updater::commands::is_update_available,
        // Product-owned Python Sidecar commands
        core::story_engine_runtime::engine_runtime_state,
        core::story_engine_runtime::engine_runtime_logs,
        core::story_engine_runtime::start_story_engine,
        core::story_engine_runtime::restart_story_engine,
        core::story_engine_runtime::stop_story_engine,
    ]);

    // Mobile: no updater commands
    #[cfg(any(target_os = "android", target_os = "ios"))]
    let app_builder = app_builder.invoke_handler(invoke_commands_with_extras![
        // Mobile-specific remote provider commands
        core::server::remote_provider_commands::abort_remote_stream,
    ]);

    let app = app_builder
        .manage(AppState {
            app_token: Some(generate_app_token()),
            mcp_servers: Arc::new(Mutex::new(HashMap::new())),
            mcp_active_servers: Arc::new(Mutex::new(HashMap::new())),
            server_handle: Arc::new(Mutex::new(None)),
            tool_call_cancellations: Arc::new(Mutex::new(HashMap::new())),
            mcp_settings: Arc::new(Mutex::new(McpSettings::default())),
            mcp_shutdown_in_progress: Arc::new(Mutex::new(false)),
            mcp_monitoring_tasks: Arc::new(Mutex::new(HashMap::new())),
            mcp_starting: Arc::new(Mutex::new(HashSet::new())),
            background_cleanup_handle: Arc::new(Mutex::new(None)),
            mcp_server_pids: Arc::new(Mutex::new(HashMap::new())),
            provider_configs: Arc::new(Mutex::new(HashMap::new())),
            mcp_reconnect_notify: Arc::new(tokio::sync::Notify::new()),
            mcp_last_known_tools: Arc::new(Mutex::new(HashMap::new())),
        })
        .setup(|app| {
            app.handle().plugin(
                tauri_plugin_log::Builder::default()
                    .level(log::LevelFilter::Debug)
                    .targets([
                        tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Stdout),
                        tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Webview),
                        tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Folder {
                            path: get_jan_data_folder_path(app.handle().clone()).join("logs"),
                            file_name: Some("app".to_string()),
                        }),
                    ])
                    .build(),
            )?;
            #[cfg(not(any(target_os = "ios", target_os = "android")))]
            if updater_release_configured(app.config().plugins.0.get("updater")) {
                app.handle()
                    .plugin(tauri_plugin_updater::Builder::new().build())?;
            } else {
                log::info!("Updater disabled: no signed release configuration");
            }

            // Start migration
            let mut store_path = get_jan_data_folder_path(app.handle().clone());
            store_path.push("store.json");
            let store = app
                .handle()
                .store(store_path)
                .expect("Store not initialized");
            let app_version = app.config().version.clone().unwrap_or_default();

            // Migrate MCP servers
            if let Err(e) = setup::migrate_mcp_servers(app.handle().clone(), store.clone()) {
                log::error!("Failed to migrate MCP servers: {e}");
            }

            // Store the new app version
            store.set("version", serde_json::json!(app_version));
            store.save().expect("Failed to save store");
            // Migration completed

            #[cfg(feature = "desktop")]
            if option_env!("ENABLE_SYSTEM_TRAY_ICON").unwrap_or("false") == "true" {
                log::info!("Enabling system tray icon");
                let _ = setup::setup_tray(app);
            }

            #[cfg(all(feature = "deep-link", any(windows, target_os = "linux")))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                app.deep_link().register_all()?;
            }

            // Initialize SQLite database for mobile platforms
            #[cfg(any(target_os = "android", target_os = "ios"))]
            {
                let app_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = crate::core::threads::db::init_database(&app_handle).await {
                        log::error!("Failed to initialize mobile database: {}", e);
                    }
                });
            }

            setup_mcp(app);
            setup::setup_theme_listener(app)?;
            #[cfg(not(any(target_os = "ios", target_os = "android")))]
            {
                let runtime = app.state::<Arc<EngineRuntime>>().inner().clone();
                core::story_engine_runtime::start_managed_sidecar(app.handle().clone(), runtime);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application");
    // Handle app lifecycle events
    app.run(|app, event| {
        use std::sync::atomic::Ordering;
        if let RunEvent::WindowEvent {
            event: tauri::WindowEvent::CloseRequested { api, .. },
            label,
            ..
        } = &event
        {
            if label == "main" && !SHUTTING_DOWN.load(Ordering::SeqCst) {
                // macOS: closing the window hides it; the app (and background
                // services) keep running. Quit happens via Cmd+Q / dock / tray,
                // which routes through RunEvent::ExitRequested.
                #[cfg(target_os = "macos")]
                {
                    api.prevent_close();
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.hide();
                    }
                    return;
                }
                // Windows/Linux: hide to tray only while the Local API Server is
                // running; otherwise fall through to the normal quit-on-close.
                #[cfg(not(target_os = "macos"))]
                if is_proxy_server_running(app) {
                    api.prevent_close();
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.hide();
                    }
                    return;
                }
            }
        }
        #[cfg(target_os = "macos")]
        if let RunEvent::Reopen { .. } = &event {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        if let RunEvent::Exit = event {
            let app_handle = app.clone();

            #[cfg(not(any(target_os = "ios", target_os = "android")))]
            {
                let runtime = app_handle.state::<Arc<EngineRuntime>>();
                let shutdown_result = tokio::task::block_in_place(|| {
                    tauri::async_runtime::block_on(runtime.shutdown())
                });
                if let Err(error) = shutdown_result {
                    log::warn!("Failed to stop Story Engine runtime: {error}");
                }
            }

            // Drain any debounced settings writes before the process dies.
            core::app::settings_store::flush_settings();

            #[cfg(not(any(target_os = "ios", target_os = "android")))]
            {
                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.emit("app-shutting-down", ());
                    let _ = window.hide();
                }
            }

            let state = app_handle.state::<AppState>();

            // Check if cleanup already ran
            let cleanup_already_running = tokio::task::block_in_place(|| {
                tauri::async_runtime::block_on(async {
                    let handle = state.background_cleanup_handle.lock().await;
                    handle.is_some()
                })
            });

            if cleanup_already_running {
                return;
            }

            // Run cleanup synchronously and WAIT for it to complete
            tokio::task::block_in_place(|| {
                tauri::async_runtime::block_on(async {
                    use crate::core::mcp::helpers::background_cleanup_mcp_servers;
                    let state = app_handle.state::<AppState>();

                    // Increase timeout to 10 seconds and log if it times out
                    let cleanup_future = background_cleanup_mcp_servers(&app_handle, &state);
                    match tokio::time::timeout(tokio::time::Duration::from_secs(10), cleanup_future)
                        .await
                    {
                        Ok(_) => log::info!("MCP cleanup completed successfully"),
                        Err(_) => log::warn!("MCP cleanup timed out after 10 seconds"),
                    }

                    log::info!("App cleanup completed");
                });
            });
        }
    });
}

#[cfg(all(test, not(any(target_os = "android", target_os = "ios"))))]
mod updater_configuration_tests {
    use super::updater_release_configured;

    #[test]
    fn updater_requires_endpoints_and_public_key() {
        let configured = serde_json::json!({
            "endpoints": ["https://updates.example.com/latest.json"],
            "pubkey": "signed-release-public-key"
        });

        assert!(updater_release_configured(Some(&configured)));
    }

    #[test]
    fn updater_stays_disabled_without_release_configuration() {
        for config in [
            None,
            Some(serde_json::json!({ "endpoints": [] })),
            Some(serde_json::json!({
                "endpoints": ["https://updates.example.com/latest.json"]
            })),
            Some(serde_json::json!({ "endpoints": [], "pubkey": "key" })),
        ] {
            assert!(!updater_release_configured(config.as_ref()));
        }
    }
}
