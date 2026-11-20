#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use legendary_transport::{Backend, BackendError};
use serde_json::{json, Value};
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
async fn auth_status(backend: State<'_, Backend>) -> Result<Value, BackendError> {
    backend.request("auth.status", json!({})).await
}

#[tauri::command]
async fn auth_login(backend: State<'_, Backend>, code: String) -> Result<Value, BackendError> {
    backend.request("auth.login", json!({"code": code})).await
}

#[tauri::command]
async fn auth_logout(backend: State<'_, Backend>) -> Result<Value, BackendError> {
    backend.request("auth.logout", json!({})).await
}

#[tauri::command]
async fn installed_games(backend: State<'_, Backend>) -> Result<Value, BackendError> {
    backend.request("library.installed", json!({})).await
}

#[tauri::command]
async fn list_games(
    backend: State<'_, Backend>,
    platform: String,
    force_refresh: bool,
) -> Result<Value, BackendError> {
    backend
        .request(
            "library.list",
            json!({"platform": platform, "forceRefresh": force_refresh}),
        )
        .await
}

#[tauri::command]
async fn launch_game(
    backend: State<'_, Backend>,
    app_name: String,
    offline: bool,
    skip_version_check: bool,
) -> Result<Value, BackendError> {
    backend
        .request(
            "game.launch",
            json!({
                "appName": app_name, "offline": offline, "skipVersionCheck": skip_version_check,
            }),
        )
        .await
}

#[tauri::command]
async fn install_options(
    backend: State<'_, Backend>,
    app_name: String,
    platform: String,
) -> Result<Value, BackendError> {
    backend
        .request(
            "install.options",
            json!({"appName": app_name, "platform": platform}),
        )
        .await
}

#[tauri::command]
async fn prepare_install(
    backend: State<'_, Backend>,
    app_name: String,
    platform: String,
    install_path: String,
) -> Result<Value, BackendError> {
    backend
        .request(
            "install.prepare",
            json!({"appName": app_name, "platform": platform, "installPath": install_path}),
        )
        .await
}

#[tauri::command]
async fn prepare_import(
    backend: State<'_, Backend>,
    app_name: String,
    platform: String,
    install_path: String,
) -> Result<Value, BackendError> {
    backend
        .request(
            "import.prepare",
            json!({"appName": app_name, "platform": platform, "installPath": install_path}),
        )
        .await
}

#[tauri::command]
async fn install_status(backend: State<'_, Backend>) -> Result<Value, BackendError> {
    backend.request("install.status", json!({})).await
}

#[tauri::command]
async fn start_install(
    backend: State<'_, Backend>,
    task_id: String,
) -> Result<Value, BackendError> {
    backend
        .request("install.start", json!({"taskId": task_id}))
        .await
}

#[tauri::command]
async fn cancel_install(
    backend: State<'_, Backend>,
    task_id: String,
) -> Result<Value, BackendError> {
    backend
        .request("install.cancel", json!({"taskId": task_id}))
        .await
}

#[tauri::command]
async fn choose_install_directory(app: tauri::AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("选择游戏目录")
            .blocking_pick_folder()
            .map(|path| path.to_string())
    })
    .await
    .map_err(|_| "Could not open the directory picker.".to_string())
}

#[tauri::command]
async fn open_login() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| open::that("https://legendary.gl/epiclogin"))
        .await
        .map_err(|_| "Could not open the login page.".to_string())?
        .map_err(|_| "Could not open the login page.".to_string())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![
            auth_status,
            auth_login,
            auth_logout,
            installed_games,
            list_games,
            launch_game,
            open_login,
            install_options,
            prepare_install,
            prepare_import,
            install_status,
            start_install,
            cancel_install,
            choose_install_directory,
        ])
        .build(tauri::generate_context!())
        .expect("Could not start Legendary desktop");
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            app.state::<Backend>().stop();
        }
    });
}
