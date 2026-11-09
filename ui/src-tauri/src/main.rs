// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // `laya-app.exe --laya-send-ctrl-c <pid>` is the helper the app runs to
    // stop the engine and n8n cleanly on Windows; it exits without starting
    // the app (see process_util::send_ctrl_c).
    #[cfg(windows)]
    if let Some(code) = laya_app_lib::ctrl_c_helper_main() {
        std::process::exit(code);
    }
    laya_app_lib::run();
}
