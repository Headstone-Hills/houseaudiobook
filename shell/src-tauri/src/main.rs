//! HillAudiobook for macOS (M9, D-054): a window around the local engine.
//!
//! On launch the app shows a small "starting" page, starts the bundled engine
//! (`Resources/engine/hillaudio-engine serve --port 0 --exit-with-stdin`), waits
//! for it to listen, and points the window at its loopback URL; the UI is the
//! same web app as in a browser. On quit the engine is stopped; if the app is
//! killed, the engine notices its stdin closing and stops by itself.
//!
//! `--smoke-test` starts the bundled engine, checks `/api/health` and the UI
//! page, prints the result and exits: what CI runs on the built `.app`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
use std::time::Duration;

use hillaudio_launcher::{locate_engine, Engine};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const START_TIMEOUT: Duration = Duration::from_secs(90);

struct EngineState(Mutex<Option<Engine>>);

fn smoke_test() -> i32 {
    let exe = std::env::current_exe().expect("current exe");
    let path = locate_engine(&exe);
    println!("engine: {}", path.display());
    let mut engine = match Engine::start(&path, &[], START_TIMEOUT) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("SMOKE FAIL: {e}");
            return 1;
        }
    };
    let health = engine.get("/api/health");
    let page = engine.get("/");
    engine.stop(Duration::from_secs(5));
    match (health, page) {
        (Ok((200, h)), Ok((200, p))) if h.contains("\"ok\"") && p.contains("<div id=\"root\"") => {
            println!("SMOKE OK: {} {}", engine.url, h.trim());
            0
        }
        (h, p) => {
            eprintln!(
                "SMOKE FAIL: health={h:?} page={:?}",
                p.map(|(s, b)| (s, b.len()))
            );
            1
        }
    }
}

fn show_error(window: &tauri::WebviewWindow, msg: &str) {
    let js = format!(
        "document.getElementById('msg').textContent = {};",
        serde_json_string(&format!("The engine couldn't start: {msg}"))
    );
    let _ = window.eval(&js);
}

/// A JavaScript string literal (JSON-escaped) without pulling in serde.
fn serde_json_string(s: &str) -> String {
    let mut out = String::from("\"");
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn main() {
    if std::env::args().any(|a| a == "--smoke-test") {
        std::process::exit(smoke_test());
    }
    let app = tauri::Builder::default()
        .manage(EngineState(Mutex::new(None)))
        .setup(|app| {
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("HillAudiobook")
                    .inner_size(1400.0, 900.0)
                    .min_inner_size(900.0, 600.0)
                    .build()?;
            let handle = app.handle().clone();
            // Start the engine off the main thread so the window shows at once.
            std::thread::spawn(move || {
                let exe = std::env::current_exe().unwrap_or_default();
                match Engine::start(&locate_engine(&exe), &[], START_TIMEOUT) {
                    Ok(engine) => {
                        let url = engine.url.clone();
                        if let Some(state) = handle.try_state::<EngineState>() {
                            *state.0.lock().unwrap() = Some(engine);
                        }
                        match url.parse() {
                            Ok(u) => {
                                let _ = window.navigate(u);
                            }
                            Err(e) => show_error(&window, &e.to_string()),
                        }
                    }
                    Err(e) => show_error(&window, &e.to_string()),
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building HillAudiobook");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<EngineState>() {
                if let Some(mut engine) = state.0.lock().unwrap().take() {
                    engine.stop(Duration::from_secs(5));
                }
            }
        }
    });
}
