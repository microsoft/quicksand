//! Quicksand Guest Agent (quicksand-guest-agent)
//!
//! Minimal HTTP API running inside the guest VM to handle commands from the host.
//! Reads configuration (token, port) from kernel command line.

use axum::{
    extract::State,
    http::{header, StatusCode},
    response::{
        sse::{Event, Sse},
        IntoResponse,
    },
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use std::{
    convert::Infallible,
    fs,
    io::Write,
    net::SocketAddr,
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::time::timeout;
use tokio_stream::wrappers::ReceiverStream;

// ============================================================================
// Logging
// ============================================================================

fn log(msg: &str) {
    let formatted = format!("[quicksand-guest-agent] {}", msg);
    eprintln!("{}", formatted);
    // Also write to kernel console for visibility in boot output
    if let Ok(mut console) = fs::OpenOptions::new().write(true).open("/dev/console") {
        let _ = writeln!(console, "{}", formatted);
    }
}

// ============================================================================
// Configuration
// ============================================================================

fn get_cmdline_param(name: &str) -> Option<String> {
    let cmdline = fs::read_to_string("/proc/cmdline").ok()?;
    let prefix = format!("{}=", name);
    for part in cmdline.split_whitespace() {
        if let Some(value) = part.strip_prefix(&prefix) {
            return Some(value.to_string());
        }
    }
    None
}

fn get_param(name: &str) -> Option<String> {
    get_cmdline_param(name)
}

#[derive(Clone)]
struct AppState {
    token: Arc<String>,
    /// True while an exclusive command (sync, fstrim, etc.) is running.
    /// Other execute requests are rejected with 503 while this is set.
    exclusive_busy: Arc<AtomicBool>,
}

// ============================================================================
// Request/Response Types
// ============================================================================

#[derive(Deserialize)]
struct AuthRequest {
    token: String,
}

#[derive(Serialize)]
struct AuthResponse {
    authenticated: bool,
}

#[derive(Deserialize)]
struct ExecuteRequest {
    command: String,
    #[serde(default = "default_timeout")]
    timeout: f64,
    cwd: Option<String>,
    /// When true, reject all other execute requests while this one is running.
    #[serde(default)]
    exclusive: bool,
}

fn default_timeout() -> f64 {
    30.0
}

#[derive(Serialize)]
struct ExecuteResponse {
    stdout: String,
    stderr: String,
    exit_code: i32,
}

#[derive(Serialize)]
struct PingResponse {
    pong: bool,
    pid: u32,
}

#[derive(Serialize)]
struct ErrorResponse {
    detail: String,
}

// ============================================================================
// Authentication
// ============================================================================

fn extract_bearer_token(headers: &axum::http::HeaderMap) -> Option<&str> {
    headers
        .get(header::AUTHORIZATION)?
        .to_str()
        .ok()?
        .strip_prefix("Bearer ")
}

fn verify_token(headers: &axum::http::HeaderMap, expected: &str) -> Result<(), (StatusCode, Json<ErrorResponse>)> {
    match extract_bearer_token(headers) {
        Some(token) if token == expected => Ok(()),
        _ => Err((
            StatusCode::UNAUTHORIZED,
            Json(ErrorResponse {
                detail: "Invalid token".to_string(),
            }),
        )),
    }
}

// ============================================================================
// Handlers
// ============================================================================

async fn authenticate(
    State(state): State<AppState>,
    Json(req): Json<AuthRequest>,
) -> impl IntoResponse {
    if req.token == *state.token {
        (StatusCode::OK, Json(AuthResponse { authenticated: true }))
    } else {
        (StatusCode::UNAUTHORIZED, Json(AuthResponse { authenticated: false }))
    }
}

async fn execute(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(req): Json<ExecuteRequest>,
) -> impl IntoResponse {
    if let Err(e) = verify_token(&headers, &state.token) {
        return e.into_response();
    }

    // Reject if an exclusive command is already running.
    if state.exclusive_busy.load(Ordering::SeqCst) {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(ErrorResponse {
                detail: "Exclusive command in progress".to_string(),
            }),
        )
            .into_response();
    }

    // If this request is exclusive, atomically claim the lock.
    if req.exclusive {
        if state
            .exclusive_busy
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(ErrorResponse {
                    detail: "Exclusive command in progress".to_string(),
                }),
            )
                .into_response();
        }
    }

    let timeout_duration = Duration::from_secs_f64(req.timeout);

    let result = timeout(timeout_duration, async {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg(&req.command);
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());

        if let Some(cwd) = &req.cwd {
            cmd.current_dir(cwd);
        }

        cmd.output()
    })
    .await;

    // Release the exclusive lock if we held it.
    if req.exclusive {
        state.exclusive_busy.store(false, Ordering::SeqCst);
    }

    let response = match result {
        Ok(Ok(output)) => ExecuteResponse {
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
            exit_code: output.status.code().unwrap_or(-1),
        },
        Ok(Err(e)) => ExecuteResponse {
            stdout: String::new(),
            stderr: e.to_string(),
            exit_code: -1,
        },
        Err(_) => ExecuteResponse {
            stdout: String::new(),
            stderr: format!("Command timed out after {} seconds", req.timeout),
            exit_code: -1,
        },
    };

    (StatusCode::OK, Json(response)).into_response()
}

async fn execute_stream(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(req): Json<ExecuteRequest>,
) -> impl IntoResponse {
    if let Err(e) = verify_token(&headers, &state.token) {
        return e.into_response();
    }

    // Reject if an exclusive command is already running.
    if state.exclusive_busy.load(Ordering::SeqCst) {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(ErrorResponse {
                detail: "Exclusive command in progress".to_string(),
            }),
        )
            .into_response();
    }

    // If this request is exclusive, atomically claim the lock.
    if req.exclusive {
        if state
            .exclusive_busy
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(ErrorResponse {
                    detail: "Exclusive command in progress".to_string(),
                }),
            )
                .into_response();
        }
    }

    let timeout_duration = Duration::from_secs_f64(req.timeout);
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(64);
    let exclusive_busy = state.exclusive_busy.clone();
    let is_exclusive = req.exclusive;

    tokio::spawn(async move {
        let result = timeout(timeout_duration, async {
            let mut cmd = tokio::process::Command::new("/bin/sh");
            cmd.arg("-c").arg(&req.command);
            cmd.stdout(Stdio::piped());
            cmd.stderr(Stdio::piped());

            if let Some(cwd) = &req.cwd {
                cmd.current_dir(cwd);
            }

            let mut child = match cmd.spawn() {
                Ok(c) => c,
                Err(e) => {
                    let data = serde_json::json!({"stream": "stderr", "data": e.to_string()});
                    let _ = tx.send(Ok(Event::default().data(data.to_string()))).await;
                    let data = serde_json::json!({"stream": "exit", "exit_code": -1});
                    let _ = tx.send(Ok(Event::default().data(data.to_string()))).await;
                    return;
                }
            };

            let stdout = child.stdout.take().unwrap();
            let stderr = child.stderr.take().unwrap();
            let mut stdout_reader = BufReader::new(stdout).lines();
            let mut stderr_reader = BufReader::new(stderr).lines();

            let mut stdout_done = false;
            let mut stderr_done = false;

            while !stdout_done || !stderr_done {
                tokio::select! {
                    line = stdout_reader.next_line(), if !stdout_done => {
                        match line {
                            Ok(Some(text)) => {
                                let data = serde_json::json!({"stream": "stdout", "data": format!("{}\n", text)});
                                if tx.send(Ok(Event::default().data(data.to_string()))).await.is_err() {
                                    return;
                                }
                            }
                            _ => stdout_done = true,
                        }
                    }
                    line = stderr_reader.next_line(), if !stderr_done => {
                        match line {
                            Ok(Some(text)) => {
                                let data = serde_json::json!({"stream": "stderr", "data": format!("{}\n", text)});
                                if tx.send(Ok(Event::default().data(data.to_string()))).await.is_err() {
                                    return;
                                }
                            }
                            _ => stderr_done = true,
                        }
                    }
                }
            }

            let exit_code = match child.wait().await {
                Ok(status) => status.code().unwrap_or(-1),
                Err(_) => -1,
            };

            let data = serde_json::json!({"stream": "exit", "exit_code": exit_code});
            let _ = tx.send(Ok(Event::default().data(data.to_string()))).await;
        })
        .await;

        if result.is_err() {
            let data = serde_json::json!({"stream": "stderr", "data": format!("Command timed out after {} seconds\n", req.timeout)});
            let _ = tx.send(Ok(Event::default().data(data.to_string()))).await;
            let data = serde_json::json!({"stream": "exit", "exit_code": -1});
            let _ = tx.send(Ok(Event::default().data(data.to_string()))).await;
        }

        // Release exclusive lock if we held it.
        if is_exclusive {
            exclusive_busy.store(false, Ordering::SeqCst);
        }
    });

    let stream = ReceiverStream::new(rx);
    Sse::new(stream).into_response()
}

async fn ping(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
) -> impl IntoResponse {
    if let Err(e) = verify_token(&headers, &state.token) {
        return e.into_response();
    }

    (
        StatusCode::OK,
        Json(PingResponse {
            pong: true,
            pid: std::process::id(),
        }),
    )
        .into_response()
}

// ============================================================================
// Shared command execution (used by both HTTP and virtio-serial transports)
// ============================================================================

struct ExecResult {
    stdout: String,
    stderr: String,
    exit_code: i32,
}

async fn run_command(command: &str, timeout_secs: f64, cwd: Option<&str>) -> ExecResult {
    let timeout_duration = Duration::from_secs_f64(timeout_secs);

    let result = timeout(timeout_duration, async {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg(command);
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
        if let Some(dir) = cwd {
            cmd.current_dir(dir);
        }
        cmd.output()
    })
    .await;

    match result {
        Ok(Ok(output)) => ExecResult {
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
            exit_code: output.status.code().unwrap_or(-1),
        },
        Ok(Err(e)) => ExecResult {
            stdout: String::new(),
            stderr: e.to_string(),
            exit_code: -1,
        },
        Err(_) => ExecResult {
            stdout: String::new(),
            stderr: format!("Command timed out after {} seconds", timeout_secs),
            exit_code: -1,
        },
    }
}

// ============================================================================
// Virtio-serial transport (length-prefixed JSON frames)
// ============================================================================

const VIRTIO_SERIAL_PATH: &str = "/dev/virtio-ports/quicksand.agent.0";

/// Frame format: 4-byte big-endian length + JSON payload
async fn read_frame(reader: &mut (impl tokio::io::AsyncReadExt + Unpin)) -> std::io::Result<serde_json::Value> {
    let mut len_buf = [0u8; 4];
    reader.read_exact(&mut len_buf).await?;
    let len = u32::from_be_bytes(len_buf) as usize;
    if len > 64 * 1024 * 1024 {
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "Frame too large"));
    }
    let mut buf = vec![0u8; len];
    reader.read_exact(&mut buf).await?;
    serde_json::from_slice(&buf).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}

async fn write_frame(writer: &mut (impl tokio::io::AsyncWriteExt + Unpin), msg: &serde_json::Value) -> std::io::Result<()> {
    let payload = serde_json::to_vec(msg).unwrap();
    let len = (payload.len() as u32).to_be_bytes();
    writer.write_all(&len).await?;
    writer.write_all(&payload).await?;
    writer.flush().await?;
    Ok(())
}

async fn handle_virtio_serial(token: String, exclusive_busy: Arc<AtomicBool>) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt, BufStream};

    let file = match tokio::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(VIRTIO_SERIAL_PATH)
        .await
    {
        Ok(f) => f,
        Err(e) => {
            log(&format!("Failed to open {}: {}", VIRTIO_SERIAL_PATH, e));
            return;
        }
    };

    log(&format!("Virtio-serial channel open: {}", VIRTIO_SERIAL_PATH));

    let stream = BufStream::new(file);
    let (mut reader, mut writer) = tokio::io::split(stream);
    let mut authenticated = false;

    loop {
        let frame = match read_frame(&mut reader).await {
            Ok(f) => f,
            Err(e) => {
                log(&format!("Virtio-serial read error: {}", e));
                break;
            }
        };

        let id = frame.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
        let method = frame.get("method").and_then(|v| v.as_str()).unwrap_or("");
        let params = frame.get("params").cloned().unwrap_or(serde_json::json!({}));

        match method {
            "authenticate" => {
                let req_token = params.get("token").and_then(|v| v.as_str()).unwrap_or("");
                authenticated = req_token == token;
                let resp = serde_json::json!({"id": id, "result": {"authenticated": authenticated}});
                if let Err(e) = write_frame(&mut writer, &resp).await {
                    log(&format!("Write error: {}", e));
                    break;
                }
            }
            "ping" if authenticated => {
                let resp = serde_json::json!({"id": id, "result": {"pong": true, "pid": std::process::id()}});
                if let Err(e) = write_frame(&mut writer, &resp).await {
                    log(&format!("Write error: {}", e));
                    break;
                }
            }
            "execute" if authenticated => {
                let command = params.get("command").and_then(|v| v.as_str()).unwrap_or("");
                let timeout_secs = params.get("timeout").and_then(|v| v.as_f64()).unwrap_or(30.0);
                let cwd = params.get("cwd").and_then(|v| v.as_str());
                let is_exclusive = params.get("exclusive").and_then(|v| v.as_bool()).unwrap_or(false);

                if exclusive_busy.load(Ordering::SeqCst) {
                    let resp = serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}});
                    let _ = write_frame(&mut writer, &resp).await;
                    continue;
                }
                if is_exclusive {
                    if exclusive_busy.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst).is_err() {
                        let resp = serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}});
                        let _ = write_frame(&mut writer, &resp).await;
                        continue;
                    }
                }

                let result = run_command(command, timeout_secs, cwd).await;

                if is_exclusive {
                    exclusive_busy.store(false, Ordering::SeqCst);
                }

                let resp = serde_json::json!({
                    "id": id,
                    "result": {
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.exit_code
                    }
                });
                if let Err(e) = write_frame(&mut writer, &resp).await {
                    log(&format!("Write error: {}", e));
                    break;
                }
            }
            "execute_stream" if authenticated => {
                let command = params.get("command").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let timeout_secs = params.get("timeout").and_then(|v| v.as_f64()).unwrap_or(30.0);
                let cwd = params.get("cwd").and_then(|v| v.as_str()).map(|s| s.to_string());
                let is_exclusive = params.get("exclusive").and_then(|v| v.as_bool()).unwrap_or(false);

                if exclusive_busy.load(Ordering::SeqCst) {
                    let resp = serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}});
                    let _ = write_frame(&mut writer, &resp).await;
                    continue;
                }
                if is_exclusive {
                    if exclusive_busy.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst).is_err() {
                        let resp = serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}});
                        let _ = write_frame(&mut writer, &resp).await;
                        continue;
                    }
                }

                let timeout_duration = Duration::from_secs_f64(timeout_secs);

                let result = timeout(timeout_duration, async {
                    let mut cmd = tokio::process::Command::new("/bin/sh");
                    cmd.arg("-c").arg(&command);
                    cmd.stdout(Stdio::piped());
                    cmd.stderr(Stdio::piped());
                    if let Some(dir) = &cwd {
                        cmd.current_dir(dir);
                    }

                    let mut child = match cmd.spawn() {
                        Ok(c) => c,
                        Err(e) => {
                            let _ = write_frame(&mut writer, &serde_json::json!({"id": id, "stream": "stderr", "data": e.to_string()})).await;
                            let _ = write_frame(&mut writer, &serde_json::json!({"id": id, "stream": "exit", "exit_code": -1})).await;
                            return;
                        }
                    };

                    let stdout = child.stdout.take().unwrap();
                    let stderr = child.stderr.take().unwrap();
                    let mut stdout_reader = BufReader::new(stdout).lines();
                    let mut stderr_reader = BufReader::new(stderr).lines();

                    let mut stdout_done = false;
                    let mut stderr_done = false;

                    while !stdout_done || !stderr_done {
                        tokio::select! {
                            line = stdout_reader.next_line(), if !stdout_done => {
                                match line {
                                    Ok(Some(text)) => {
                                        let _ = write_frame(&mut writer, &serde_json::json!({"id": id, "stream": "stdout", "data": format!("{}\n", text)})).await;
                                    }
                                    _ => stdout_done = true,
                                }
                            }
                            line = stderr_reader.next_line(), if !stderr_done => {
                                match line {
                                    Ok(Some(text)) => {
                                        let _ = write_frame(&mut writer, &serde_json::json!({"id": id, "stream": "stderr", "data": format!("{}\n", text)})).await;
                                    }
                                    _ => stderr_done = true,
                                }
                            }
                        }
                    }

                    let exit_code = match child.wait().await {
                        Ok(status) => status.code().unwrap_or(-1),
                        Err(_) => -1,
                    };

                    let _ = write_frame(&mut writer, &serde_json::json!({"id": id, "stream": "exit", "exit_code": exit_code})).await;
                })
                .await;

                if result.is_err() {
                    let _ = write_frame(&mut writer, &serde_json::json!({"id": id, "stream": "stderr", "data": format!("Command timed out after {} seconds\n", timeout_secs)})).await;
                    let _ = write_frame(&mut writer, &serde_json::json!({"id": id, "stream": "exit", "exit_code": -1})).await;
                }

                if is_exclusive {
                    exclusive_busy.store(false, Ordering::SeqCst);
                }
            }
            _ if !authenticated => {
                let resp = serde_json::json!({"id": id, "error": {"message": "Not authenticated"}});
                let _ = write_frame(&mut writer, &resp).await;
            }
            _ => {
                let resp = serde_json::json!({"id": id, "error": {"message": format!("Unknown method: {}", method)}});
                let _ = write_frame(&mut writer, &resp).await;
            }
        }
    }
}

// ============================================================================
// Main
// ============================================================================

#[tokio::main]
async fn main() {
    log("Rust agent starting...");

    // Read config from kernel command line (injected by host via -append).
    let token = match get_param("quicksand_token") {
        Some(t) => t,
        None => {
            log("ERROR: No quicksand_token in /proc/cmdline");
            if let Ok(cmdline) = fs::read_to_string("/proc/cmdline") {
                log(&format!("cmdline: {}", cmdline.trim()));
            }
            std::process::exit(1);
        }
    };

    let exclusive_busy = Arc::new(AtomicBool::new(false));

    // Try virtio-serial first (no network dependency, faster boot).
    if std::path::Path::new(VIRTIO_SERIAL_PATH).exists() {
        log("Virtio-serial port detected, using serial transport");
        handle_virtio_serial(token, exclusive_busy).await;
        return;
    }

    // Fallback: HTTP server on TCP port
    let port: u16 = match get_param("quicksand_port") {
        Some(p) => match p.parse() {
            Ok(port) => port,
            Err(_) => {
                log(&format!("ERROR: Invalid quicksand_port: {}", p));
                std::process::exit(1);
            }
        },
        None => {
            log("ERROR: No quicksand_port in /proc/cmdline");
            std::process::exit(1);
        }
    };

    log(&format!("Token: {}... Port: {}", &token[..8.min(token.len())], port));

    let state = AppState {
        token: Arc::new(token),
        exclusive_busy,
    };

    let app = Router::new()
        .route("/authenticate", post(authenticate))
        .route("/execute", post(execute))
        .route("/execute_stream", post(execute_stream))
        .route("/ping", get(ping))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    log(&format!("Listening on {}", addr));

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
