mod sidecar;

use std::sync::Mutex;
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

struct EngineProcess(Mutex<Option<std::process::Child>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // --- System tray ---
            let show = MenuItem::with_id(app, "show", "Show Laya", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;

            let _tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Laya")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // --- Spawn Python engine ---
            match sidecar::spawn_engine() {
                Ok(child) => {
                    app.manage(EngineProcess(Mutex::new(Some(child))));

                    // Poll for readiness in a background thread
                    std::thread::spawn(|| {
                        sidecar::wait_for_engine(Duration::from_secs(30));
                    });
                }
                Err(e) => {
                    log::error!("Failed to start engine: {}", e);
                    // Store None — the app can still run, health badge will show unhealthy
                    app.manage(EngineProcess(Mutex::new(None)));
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // Kill engine when the app exits
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.try_state::<EngineProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(ref mut child) = *guard {
                            log::info!("Killing engine process");
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
