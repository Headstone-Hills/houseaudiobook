//! Start, watch and stop the HillAudiobook engine (M9, D-054).
//!
//! The app bundles the engine (a frozen Python program) and runs it as a child
//! process: `hillaudio-engine serve --port 0 --exit-with-stdin`. The engine
//! picks a free loopback port and prints `HILLAUDIO_READY port=N url=...` once it
//! listens; we read that line (with a timeout) and open the window on the URL.
//! The engine's stdin stays connected to us: when this process ends, however it
//! ends, the pipe closes and the engine stops too.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

pub const READY: &str = "HILLAUDIO_READY";
pub const ENGINE_NAME: &str = "hillaudio-engine";

#[derive(Debug)]
pub enum LaunchError {
    NotFound(PathBuf),
    Spawn(std::io::Error),
    Exited(String),
    Timeout(Duration),
}

impl std::fmt::Display for LaunchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LaunchError::NotFound(p) => {
                write!(f, "the engine is missing from the app ({})", p.display())
            }
            LaunchError::Spawn(e) => write!(f, "couldn't start the engine: {e}"),
            LaunchError::Exited(err) => write!(f, "the engine stopped while starting: {err}"),
            LaunchError::Timeout(t) => {
                write!(f, "the engine didn't start within {} s", t.as_secs())
            }
        }
    }
}

impl std::error::Error for LaunchError {}

/// `HILLAUDIO_READY port=53712 url=http://127.0.0.1:53712/` -> (53712, url).
pub fn parse_ready(line: &str) -> Option<(u16, String)> {
    let rest = line.trim().strip_prefix(READY)?;
    let mut port = None;
    let mut url = None;
    for field in rest.split_whitespace() {
        if let Some(v) = field.strip_prefix("port=") {
            port = v.parse().ok();
        } else if let Some(v) = field.strip_prefix("url=") {
            url = Some(v.to_string());
        }
    }
    let port = port?;
    // Only ever a loopback URL: the window must not be pointed anywhere else.
    let url = url.unwrap_or_else(|| format!("http://127.0.0.1:{port}/"));
    if !(url.starts_with("http://127.0.0.1:") || url.starts_with("http://[::1]:")) {
        return None;
    }
    Some((port, url))
}

/// Where the bundled engine is, relative to the app's executable:
/// macOS `X.app/Contents/MacOS/X` -> `X.app/Contents/Resources/engine/`;
/// elsewhere `dir/X` -> `dir/engine/` (or `dir/resources/engine/`).
/// `$HILLAUDIO_ENGINE` overrides (development).
pub fn locate_engine(exe: &Path) -> PathBuf {
    if let Ok(p) = std::env::var("HILLAUDIO_ENGINE") {
        return PathBuf::from(p);
    }
    let name = if cfg!(windows) {
        format!("{ENGINE_NAME}.exe")
    } else {
        ENGINE_NAME.to_string()
    };
    let dir = exe.parent().unwrap_or(Path::new("."));
    let candidates = [
        dir.join("../Resources/engine").join(&name),
        dir.join("engine").join(&name),
        dir.join("resources/engine").join(&name),
    ];
    candidates
        .iter()
        .find(|p| p.is_file())
        .cloned()
        .unwrap_or_else(|| candidates[0].clone())
}

pub struct Engine {
    child: Child,
    stdin: Option<ChildStdin>,
    pub port: u16,
    pub url: String,
}

impl Engine {
    /// Start the engine and wait for its READY line.
    pub fn start(
        program: &Path,
        extra_args: &[&str],
        timeout: Duration,
    ) -> Result<Engine, LaunchError> {
        if !program.is_file() {
            return Err(LaunchError::NotFound(program.to_path_buf()));
        }
        let mut child = Command::new(program)
            .args(["serve", "--port", "0", "--exit-with-stdin"])
            .args(extra_args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(LaunchError::Spawn)?;
        let stdout = child.stdout.take().expect("piped stdout");
        let mut stderr = child.stderr.take().expect("piped stderr");
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                if let Some(found) = parse_ready(&line) {
                    let _ = tx.send(Some(found));
                    // Keep draining so the engine never blocks on a full pipe.
                    continue;
                }
            }
            let _ = tx.send(None);
        });
        let deadline = Instant::now() + timeout;
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            match rx.recv_timeout(left.min(Duration::from_millis(200))) {
                Ok(Some((port, url))) => {
                    // Drain stderr in the background too (warnings, tracebacks).
                    thread::spawn(move || {
                        let mut sink = Vec::new();
                        let _ = stderr.read_to_end(&mut sink);
                    });
                    let stdin = child.stdin.take();
                    return Ok(Engine {
                        child,
                        stdin,
                        port,
                        url,
                    });
                }
                Ok(None) => {
                    let _ = child.wait();
                    let mut err = String::new();
                    let _ = stderr.read_to_string(&mut err);
                    return Err(LaunchError::Exited(last_lines(&err, 5)));
                }
                Err(_) => {
                    if let Ok(Some(status)) = child.try_wait() {
                        let mut err = String::new();
                        let _ = stderr.read_to_string(&mut err);
                        return Err(LaunchError::Exited(format!(
                            "{status}: {}",
                            last_lines(&err, 5)
                        )));
                    }
                    if Instant::now() >= deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(LaunchError::Timeout(timeout));
                    }
                }
            }
        }
    }

    /// A plain HTTP GET to the engine (no extra crates): (status, body).
    pub fn get(&self, path: &str) -> std::io::Result<(u16, String)> {
        http_get(self.port, path)
    }

    pub fn is_running(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    /// Ask the engine to stop (close its stdin); kill it if it hasn't after `grace`.
    pub fn stop(&mut self, grace: Duration) {
        drop(self.stdin.take());
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if !self.is_running() {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        self.stop(Duration::from_secs(3));
    }
}

pub fn http_get(port: u16, path: &str) -> std::io::Result<(u16, String)> {
    let mut s = TcpStream::connect(("127.0.0.1", port))?;
    s.set_read_timeout(Some(Duration::from_secs(10)))?;
    write!(
        s,
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )?;
    let mut raw = String::new();
    s.read_to_string(&mut raw)?;
    let status = raw
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .unwrap_or(0);
    let body = raw
        .split_once("\r\n\r\n")
        .map(|(_, b)| b.to_string())
        .unwrap_or_default();
    Ok((status, body))
}

fn last_lines(s: &str, n: usize) -> String {
    let lines: Vec<&str> = s.lines().collect();
    lines[lines.len().saturating_sub(n)..].join(" | ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn parses_the_ready_line_and_only_loopback_urls() {
        assert_eq!(
            parse_ready("HILLAUDIO_READY port=53712 url=http://127.0.0.1:53712/\n"),
            Some((53712, "http://127.0.0.1:53712/".to_string()))
        );
        assert_eq!(
            parse_ready("HILLAUDIO_READY port=9"),
            Some((9, "http://127.0.0.1:9/".into()))
        );
        assert_eq!(
            parse_ready("HILLAUDIO_READY port=9 url=http://evil.example/"),
            None
        );
        assert_eq!(parse_ready("INFO starting"), None);
        assert_eq!(parse_ready("HILLAUDIO_READY port=x"), None);
    }

    #[test]
    fn locates_the_bundled_engine() {
        let tmp = std::env::temp_dir().join(format!("hl-locate-{}", std::process::id()));
        let app = tmp.join("X.app/Contents");
        fs::create_dir_all(app.join("MacOS")).unwrap();
        fs::create_dir_all(app.join("Resources/engine")).unwrap();
        fs::write(app.join("Resources/engine").join(ENGINE_NAME), "").unwrap();
        let found = locate_engine(&app.join("MacOS/X"));
        assert!(
            found.ends_with(format!("Resources/engine/{ENGINE_NAME}")),
            "{found:?}"
        );
        fs::remove_dir_all(&tmp).unwrap();
    }

    /// A stand-in engine: a shell script that behaves like `hillaudio serve`.
    #[cfg(unix)]
    fn fake_engine(body: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("hl-fake-{}-{}", std::process::id(), body.len()));
        fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
        p
    }

    #[cfg(unix)]
    #[test]
    fn starts_waits_for_ready_and_stops_with_stdin() {
        // Echo READY, then live until stdin closes (as --exit-with-stdin does).
        let p = fake_engine("echo starting; echo 'HILLAUDIO_READY port=4567 url=http://127.0.0.1:4567/'; cat >/dev/null");
        let mut e = Engine::start(&p, &[], Duration::from_secs(5)).unwrap();
        assert_eq!((e.port, e.url.as_str()), (4567, "http://127.0.0.1:4567/"));
        assert!(e.is_running());
        let t = Instant::now();
        e.stop(Duration::from_secs(5));
        assert!(
            !e.is_running() && t.elapsed() < Duration::from_secs(2),
            "stopped by closing stdin"
        );
        fs::remove_file(p).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn reports_a_crash_and_a_timeout() {
        let crash = fake_engine("echo 'Traceback: no module named x' >&2; exit 3");
        match Engine::start(&crash, &[], Duration::from_secs(5)) {
            Err(LaunchError::Exited(msg)) => assert!(msg.contains("no module named x"), "{msg}"),
            other => panic!("expected Exited, got {:?}", other.err()),
        }
        let slow = fake_engine("sleep 30");
        let t = Instant::now();
        assert!(matches!(
            Engine::start(&slow, &[], Duration::from_millis(500)),
            Err(LaunchError::Timeout(_))
        ));
        assert!(t.elapsed() < Duration::from_secs(3));
        assert!(matches!(
            Engine::start(Path::new("/no/such/engine"), &[], Duration::from_secs(1)),
            Err(LaunchError::NotFound(_))
        ));
        fs::remove_file(crash).unwrap();
        fs::remove_file(slow).unwrap();
    }
}
