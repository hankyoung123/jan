use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager, Runtime, State};

use crate::core::app::commands::{
    default_data_folder_path, get_configuration_file_path, get_jan_data_folder_path,
    update_app_configuration,
};
use crate::core::app::constants::{
    JAN_DATA_DIRS_COMMON, JAN_DATA_DIRS_CONVERSATIONS, JAN_DATA_FILES_CONFIGS,
    JAN_DATA_FILES_SETTINGS,
};
use crate::core::app::models::AppConfiguration;
use crate::core::mcp::helpers::{stop_mcp_servers_with_context, ShutdownContext};
use crate::core::state::AppState;

fn is_safe_to_delete(path: &std::path::Path) -> bool {
    let count = path.components().count();
    count >= 3
}

fn remove_dir(data_folder: &std::path::Path, name: &str) {
    let path = data_folder.join(name);
    if path.is_dir() {
        log::info!("Removing directory: {}", path.display());
        if let Err(e) = fs::remove_dir_all(&path) {
            log::warn!("Failed to remove {}: {e}", path.display());
        }
    }
}

fn remove_file(data_folder: &std::path::Path, name: &str) {
    let path = data_folder.join(name);
    if path.is_file() {
        log::info!("Removing file: {}", path.display());
        if let Err(e) = fs::remove_file(&path) {
            log::warn!("Failed to remove {}: {e}", path.display());
        }
    }
}

/// Delete conversations and user data (threads, assistants).
fn delete_conversations(data_folder: &std::path::Path) {
    log::info!("Deleting conversations (threads, assistants)");
    for dir in JAN_DATA_DIRS_CONVERSATIONS {
        remove_dir(data_folder, dir);
    }
}

/// Delete provider-adjacent configuration files such as MCP settings.
fn delete_provider_configs(data_folder: &std::path::Path) {
    log::info!("Deleting provider and MCP configurations");
    for file in JAN_DATA_FILES_CONFIGS {
        remove_file(data_folder, file);
    }
}

/// Delete extensions, logs, caches — always cleaned during any reset.
fn delete_common_data(data_folder: &std::path::Path) {
    log::info!("Deleting common data (extensions, logs, caches)");
    for dir in JAN_DATA_DIRS_COMMON {
        remove_dir(data_folder, dir);
    }
}

/// Delete cross-category settings (store.json) — only during a full wipe
/// when the user is not keeping any data.
fn delete_settings(data_folder: &std::path::Path) {
    log::info!("Deleting cross-category settings (store.json)");
    for file in JAN_DATA_FILES_SETTINGS {
        remove_file(data_folder, file);
    }
}

/// Clear the WebKit/WRY webview profile (localStorage, cookies, IndexedDB,
/// updater state) stored in the bundle-id app-data dir (e.g. `jan.ai.app/`).
/// Distinct from the product-name data folder; only removed on explicit opt-in.
fn clear_webview_profile<R: Runtime>(
    app_handle: &tauri::AppHandle<R>,
    data_folder: &std::path::Path,
) {
    let webview_dir = match app_handle.path().app_data_dir() {
        Ok(dir) => dir,
        Err(e) => {
            log::warn!("Cannot resolve webview profile dir: {e}");
            return;
        }
    };

    // Never touch the dir if it is the user's data folder or contains it.
    if webview_dir == data_folder || data_folder.starts_with(&webview_dir) {
        log::warn!(
            "Skipping webview clear: data folder lives inside {}",
            webview_dir.display()
        );
        return;
    }

    if !webview_dir.is_dir() || !is_safe_to_delete(&webview_dir) {
        return;
    }

    log::info!("Clearing webview profile: {}", webview_dir.display());
    if let Err(e) = fs::remove_dir_all(&webview_dir) {
        log::warn!(
            "Failed to clear webview profile {}: {e}",
            webview_dir.display()
        );
    }
}

const WEBDATA_RESET_SENTINEL: &str = ".pending-webdata-reset";

/// Flags describing what a pending reset should prune from webview localStorage.
/// Consumed by the frontend on next startup before stores hydrate.
#[derive(serde::Serialize, serde::Deserialize, Clone, Copy)]
#[serde(rename_all = "camelCase")]
pub struct WebdataResetFlags {
    pub keep_app_data: bool,
    pub keep_provider_configs: bool,
    pub clear_web_data: bool,
}

/// Sentinel lives in the app config dir (alongside settings.json), which
/// survives the data-folder wipe.
fn webdata_reset_sentinel_path<R: Runtime>(app_handle: &tauri::AppHandle<R>) -> Option<PathBuf> {
    get_configuration_file_path(app_handle.clone())
        .parent()
        .map(|dir| dir.join(WEBDATA_RESET_SENTINEL))
}

fn write_webdata_reset_sentinel<R: Runtime>(
    app_handle: &tauri::AppHandle<R>,
    keep_app_data: bool,
    keep_provider_configs: bool,
    clear_web_data: bool,
) {
    let Some(path) = webdata_reset_sentinel_path(app_handle) else {
        return;
    };
    let flags = WebdataResetFlags {
        keep_app_data,
        keep_provider_configs,
        clear_web_data,
    };
    match serde_json::to_string(&flags) {
        Ok(json) => {
            if let Err(e) = fs::write(&path, json) {
                log::warn!("Failed to write webdata reset sentinel: {e}");
            }
        }
        Err(e) => log::warn!("Failed to serialize webdata reset flags: {e}"),
    }
}

/// Read and remove the pending-reset sentinel. Returns the flags if one existed
/// so the frontend can prune the matching localStorage keys before hydration.
#[tauri::command]
pub fn take_pending_webdata_reset<R: Runtime>(
    app_handle: tauri::AppHandle<R>,
) -> Option<WebdataResetFlags> {
    let path = webdata_reset_sentinel_path(&app_handle)?;
    if !path.is_file() {
        return None;
    }
    let flags = fs::read_to_string(&path)
        .ok()
        .and_then(|content| serde_json::from_str::<WebdataResetFlags>(&content).ok());
    if let Err(e) = fs::remove_file(&path) {
        log::warn!("Failed to remove webdata reset sentinel: {e}");
    }
    flags
}

#[tauri::command]
pub fn factory_reset<R: Runtime>(
    app_handle: tauri::AppHandle<R>,
    state: State<'_, AppState>,
    keep_app_data: Option<bool>,
    keep_provider_configs: Option<bool>,
    clear_web_data: Option<bool>,
) {
    let keep_app_data = keep_app_data.unwrap_or(false);
    let keep_provider_configs = keep_provider_configs.unwrap_or(false);
    let clear_web_data = clear_web_data.unwrap_or(false);

    #[cfg(not(any(target_os = "ios", target_os = "android")))]
    {
        let windows = app_handle.webview_windows();
        for (label, window) in windows.iter() {
            window.close().unwrap_or_else(|_| {
                log::warn!("Failed to close window: {label:?}");
            });
        }
    }
    let data_folder = get_jan_data_folder_path(app_handle.clone());
    log::info!(
        "Factory reset (keep_app_data={}, keep_provider_configs={}, clear_web_data={}), data folder: {:?}",
        keep_app_data,
        keep_provider_configs,
        clear_web_data,
        data_folder
    );

    tauri::async_runtime::block_on(async {
        let _ =
            stop_mcp_servers_with_context(&app_handle, &state, ShutdownContext::FactoryReset).await;

        {
            let mut active_servers = state.mcp_active_servers.lock().await;
            active_servers.clear();
        }

        use crate::core::mcp::lockfile::cleanup_own_locks;
        if let Err(e) = cleanup_own_locks(&app_handle) {
            log::warn!("Failed to cleanup lock files: {}", e);
        }

        if data_folder.exists() {
            if !is_safe_to_delete(&data_folder) {
                log::error!(
                    "Refusing factory reset: path is too close to filesystem root: {}",
                    data_folder.display()
                );
                return;
            }

            // Always clean common data (extensions, logs, caches)
            delete_common_data(&data_folder);

            // Delete conversations (threads, assistants) unless user chose to keep it
            if !keep_app_data {
                delete_conversations(&data_folder);
            }

            // Delete provider and MCP configs unless user chose to keep them.
            if !keep_provider_configs {
                delete_provider_configs(&data_folder);
            }

            // store.json spans all categories; only wipe it when nothing is kept
            if !keep_app_data && !keep_provider_configs {
                delete_settings(&data_folder);
            }
        }

        // Reset app configuration to defaults unless user chose to keep configs
        if !keep_provider_configs {
            let default_config = AppConfiguration {
                data_folder: default_data_folder_path(app_handle.clone()),
            };
            let _ = update_app_configuration(app_handle.clone(), default_config);
        }

        // Persisted UI state (model-provider, setup flag) lives in webview
        // localStorage, not the data folder. Renderer-side removal races the
        // restart flush, and the on-disk localStorage location is
        // platform-specific (esp. macOS WKWebView), so instead drop a sentinel
        // that the frontend consumes on next startup to clear localStorage via
        // the webview API before stores hydrate. File-level profile deletion
        // (Linux cookies/cache) stays as a best-effort supplement.
        let full_wipe = !keep_app_data && !keep_provider_configs;
        if !keep_app_data || !keep_provider_configs || clear_web_data {
            write_webdata_reset_sentinel(
                &app_handle,
                keep_app_data,
                keep_provider_configs,
                clear_web_data,
            );
        }
        if clear_web_data || full_wipe {
            clear_webview_profile(&app_handle, &data_folder);
        }

        app_handle.restart();
    });
}

#[tauri::command]
pub fn relaunch<R: Runtime>(app: AppHandle<R>) {
    app.restart()
}

#[tauri::command]
pub fn open_app_directory<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    let app_path = get_jan_data_folder_path(app.clone());
    let program = if cfg!(target_os = "windows") {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    std::process::Command::new(program)
        .arg(app_path)
        .status()
        .map_err(|e| format!("Failed to open app directory: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn open_file_explorer(path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    let (program, arg): (&str, std::ffi::OsString) = if cfg!(target_os = "windows") {
        // Normalize extended-length paths (\\?\...) for explorer compatibility.
        let mut path_str = path.to_string_lossy().into_owned();
        if let Some(stripped) = path_str.strip_prefix(r"\\?\UNC\") {
            path_str = format!(r"\\{}", stripped);
        } else if let Some(stripped) = path_str.strip_prefix(r"\\?\") {
            path_str = stripped.to_string();
        }
        ("explorer", path_str.into())
    } else if cfg!(target_os = "macos") {
        ("open", path.into())
    } else {
        ("xdg-open", path.into())
    };
    std::process::Command::new(program)
        .arg(arg)
        .status()
        .map_err(|e| format!("Failed to open file explorer: {e}"))?;
    Ok(())
}

#[tauri::command]
pub async fn read_logs<R: Runtime>(app: AppHandle<R>) -> Result<String, String> {
    let log_path = get_jan_data_folder_path(app).join("logs").join("app.log");
    if log_path.exists() {
        let content = fs::read_to_string(log_path).map_err(|e| e.to_string())?;
        Ok(content)
    } else {
        Err("Log file not found".to_string())
    }
}

// check if a system library is available
#[tauri::command]
pub fn is_library_available(library: &str) -> bool {
    match unsafe { libloading::Library::new(library) } {
        Ok(_) => true,
        Err(e) => {
            log::info!("Library {library} is not available: {e}");
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::app::constants::*;
    use std::fs;
    use tempfile::tempdir;

    fn create_all_data(dir: &std::path::Path) {
        for subdir in JAN_DATA_SUBDIRS {
            fs::create_dir_all(dir.join(subdir)).unwrap();
            fs::write(dir.join(subdir).join("dummy.txt"), "data").unwrap();
        }
        for file in JAN_DATA_FILES {
            fs::write(dir.join(file), "data").unwrap();
        }
    }

    fn exists_any(dir: &std::path::Path, names: &[&str]) -> bool {
        names.iter().any(|n| dir.join(n).exists())
    }

    fn exists_all(dir: &std::path::Path, names: &[&str]) -> bool {
        names.iter().all(|n| dir.join(n).exists())
    }

    #[test]
    fn test_delete_conversations_only_removes_conversation_dirs() {
        let tmp = tempdir().unwrap();
        let d = tmp.path();
        create_all_data(d);

        delete_conversations(d);

        assert!(!exists_any(d, JAN_DATA_DIRS_CONVERSATIONS));
        assert!(exists_all(d, JAN_DATA_DIRS_COMMON));
        assert!(d.join("settings.json").exists());
        assert!(d.join("mcp_config.json").exists());
    }

    #[test]
    fn test_delete_provider_configs_only_removes_config_files() {
        let tmp = tempdir().unwrap();
        let d = tmp.path();
        create_all_data(d);

        delete_provider_configs(d);

        assert!(!exists_any(d, JAN_DATA_FILES_CONFIGS));
        assert!(exists_all(d, JAN_DATA_DIRS_CONVERSATIONS));
        assert!(exists_all(d, JAN_DATA_DIRS_COMMON));
        assert!(d.join("settings.json").exists());
    }

    #[test]
    fn test_delete_common_data_only_removes_common_dirs() {
        let tmp = tempdir().unwrap();
        let d = tmp.path();
        create_all_data(d);

        delete_common_data(d);

        assert!(!exists_any(d, JAN_DATA_DIRS_COMMON));
        assert!(exists_all(d, JAN_DATA_DIRS_CONVERSATIONS));
        assert!(d.join("settings.json").exists());
        assert!(d.join("mcp_config.json").exists());
    }

    #[test]
    fn test_delete_settings_only_removes_settings_json() {
        let tmp = tempdir().unwrap();
        let d = tmp.path();
        create_all_data(d);

        delete_settings(d);

        assert!(!d.join("settings.json").exists());
        assert!(exists_all(d, JAN_DATA_DIRS_CONVERSATIONS));
        assert!(exists_all(d, JAN_DATA_DIRS_COMMON));
        assert!(d.join("mcp_config.json").exists());
    }

    #[test]
    fn test_settings_json_survives_when_keeping_any_category() {
        // Simulate: keep_app_data=true, keep_provider_configs=false
        let tmp = tempdir().unwrap();
        let d = tmp.path();
        create_all_data(d);

        delete_common_data(d);
        delete_provider_configs(d);
        // settings.json should NOT be deleted because keep_app_data=true
        assert!(d.join("settings.json").exists());

        // Simulate: keep_app_data=false, keep_provider_configs=true
        let tmp2 = tempdir().unwrap();
        let d2 = tmp2.path();
        create_all_data(d2);

        delete_common_data(d2);
        delete_conversations(d2);
        // settings.json should NOT be deleted because keep_provider_configs=true
        assert!(d2.join("settings.json").exists());
    }

    #[test]
    fn test_full_wipe_deletes_settings_json() {
        let tmp = tempdir().unwrap();
        let d = tmp.path();
        create_all_data(d);

        delete_common_data(d);
        delete_conversations(d);
        delete_provider_configs(d);
        delete_settings(d);

        assert!(!d.join("settings.json").exists());
        assert!(!exists_any(d, JAN_DATA_SUBDIRS));
        assert!(!exists_any(d, JAN_DATA_FILES));
    }

    #[test]
    fn test_delete_on_nonexistent_dirs_does_not_panic() {
        let tmp = tempdir().unwrap();
        let d = tmp.path();
        // Nothing created — should not panic
        delete_conversations(d);
        delete_provider_configs(d);
        delete_common_data(d);
        delete_settings(d);
    }

    #[test]
    fn test_is_safe_to_delete() {
        assert!(!is_safe_to_delete(std::path::Path::new("/")));
        assert!(!is_safe_to_delete(std::path::Path::new("/home")));
        assert!(is_safe_to_delete(std::path::Path::new("/home/user/jan")));
        assert!(is_safe_to_delete(std::path::Path::new(
            "/home/user/.local/share/jan"
        )));
    }
}
