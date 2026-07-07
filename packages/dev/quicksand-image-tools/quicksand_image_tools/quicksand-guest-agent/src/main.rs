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

/// Async duplex wrapper over the virtio-serial char device fd.
///
/// The port permits only a single open handle, and a buffered `tokio::fs::File`
/// reconciles its position by seeking whenever reads and writes interleave —
/// which fails on this non-seekable char device with ESPIPE the moment a
/// command response is written while the read loop is waiting for the next
/// frame. Driving the raw fd through `AsyncFd` sidesteps both problems: it is a
/// single fd used for both directions, and it issues plain `read(2)`/`write(2)`
/// syscalls (never `lseek`). `AsyncFd` tracks read- and write-readiness
/// independently, so the read loop and the writer task can make progress
/// concurrently on the one fd.
struct SerialChannel {
    fd: tokio::io::unix::AsyncFd<std::os::fd::OwnedFd>,
}

impl SerialChannel {
    fn open(path: &str) -> std::io::Result<Self> {
        use std::os::fd::{FromRawFd, OwnedFd};

        let c_path = std::ffi::CString::new(path)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, e))?;
        // O_RDWR: one handle for both directions. O_NONBLOCK: required for
        // AsyncFd readiness-driven I/O.
        let raw = unsafe { libc::open(c_path.as_ptr(), libc::O_RDWR | libc::O_NONBLOCK) };
        if raw < 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: `raw` is a fresh, valid fd we just opened and now own.
        let owned = unsafe { OwnedFd::from_raw_fd(raw) };
        Ok(Self {
            fd: tokio::io::unix::AsyncFd::new(owned)?,
        })
    }

    /// Read exactly `buf.len()` bytes, waiting for readability as needed.
    async fn read_exact(&self, buf: &mut [u8]) -> std::io::Result<()> {
        use std::os::fd::AsRawFd;

        let mut filled = 0;
        while filled < buf.len() {
            let mut guard = self.fd.readable().await?;
            let raw = guard.get_ref().as_raw_fd();
            let dst = &mut buf[filled..];
            let ret = unsafe { libc::read(raw, dst.as_mut_ptr() as *mut libc::c_void, dst.len()) };
            if ret < 0 {
                let err = std::io::Error::last_os_error();
                if err.kind() == std::io::ErrorKind::WouldBlock {
                    guard.clear_ready();
                    continue;
                }
                return Err(err);
            }
            if ret == 0 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "virtio-serial channel closed",
                ));
            }
            filled += ret as usize;
        }
        Ok(())
    }

    /// Write the whole buffer, waiting for writability as needed.
    async fn write_all(&self, buf: &[u8]) -> std::io::Result<()> {
        use std::os::fd::AsRawFd;

        let mut sent = 0;
        while sent < buf.len() {
            let mut guard = self.fd.writable().await?;
            let raw = guard.get_ref().as_raw_fd();
            let src = &buf[sent..];
            let ret = unsafe { libc::write(raw, src.as_ptr() as *const libc::c_void, src.len()) };
            if ret < 0 {
                let err = std::io::Error::last_os_error();
                if err.kind() == std::io::ErrorKind::WouldBlock {
                    guard.clear_ready();
                    continue;
                }
                return Err(err);
            }
            sent += ret as usize;
        }
        Ok(())
    }
}

/// Read one length-prefixed JSON frame from the serial channel.
async fn read_frame_serial(chan: &SerialChannel) -> std::io::Result<serde_json::Value> {
    let mut len_buf = [0u8; 4];
    chan.read_exact(&mut len_buf).await?;
    let len = u32::from_be_bytes(len_buf) as usize;
    if len > 64 * 1024 * 1024 {
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "Frame too large"));
    }
    let mut buf = vec![0u8; len];
    chan.read_exact(&mut buf).await?;
    serde_json::from_slice(&buf).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}

/// Write one length-prefixed JSON frame to the serial channel.
async fn write_frame_serial(chan: &SerialChannel, msg: &serde_json::Value) -> std::io::Result<()> {
    let payload = serde_json::to_vec(msg).unwrap();
    let len = (payload.len() as u32).to_be_bytes();
    chan.write_all(&len).await?;
    chan.write_all(&payload).await?;
    Ok(())
}

/// Sender used by request handlers to hand output frames to the writer task.
///
/// Command handlers run on their own spawned tasks so a long-running command
/// never blocks the read loop (previously each command was awaited inline, so
/// one slow command — e.g. `systemctl stop` under WHPX — stalled the reader and
/// any queued request until it finished, which could wedge the whole session).
/// Handlers never touch the device directly; they only produce frames. A single
/// dedicated writer task owns the write handle and drains this channel, so the
/// frames for different request ids never interleave on the wire.
type FrameSender = tokio::sync::mpsc::UnboundedSender<serde_json::Value>;

fn send_frame(tx: &FrameSender, msg: serde_json::Value) {
    // The only way this fails is if the writer task has exited (receiver
    // dropped), which means the session is already tearing down.
    let _ = tx.send(msg);
}

async fn handle_virtio_serial(token: String, exclusive_busy: Arc<AtomicBool>) {
    // One non-blocking fd drives both directions through `AsyncFd` (see
    // `SerialChannel`): the port allows only a single open handle, and a
    // buffered `tokio::fs::File` seeks on read/write interleave (ESPIPE on this
    // non-seekable char device).
    let chan = match SerialChannel::open(VIRTIO_SERIAL_PATH) {
        Ok(c) => Arc::new(c),
        Err(e) => {
            log(&format!("Failed to open {}: {}", VIRTIO_SERIAL_PATH, e));
            return;
        }
    };

    log(&format!("Virtio-serial channel open: {}", VIRTIO_SERIAL_PATH));

    // Dedicated writer task: request handlers (including spawned command tasks)
    // send frames through this channel; the writer drains it and serialises the
    // frames onto the wire in arrival order, so responses for different request
    // ids never interleave. The reader loop below shares the same fd via
    // `AsyncFd`, which tracks read/write readiness independently.
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<serde_json::Value>();
    let writer_chan = Arc::clone(&chan);
    let writer_task = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if let Err(e) = write_frame_serial(&writer_chan, &msg).await {
                log(&format!("Write error: {}", e));
                break;
            }
        }
    });

    let mut authenticated = false;

    loop {
        let frame = match read_frame_serial(&chan).await {
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
                send_frame(&tx, serde_json::json!({"id": id, "result": {"authenticated": authenticated}}));
            }
            "ping" if authenticated => {
                send_frame(&tx, serde_json::json!({"id": id, "result": {"pong": true, "pid": std::process::id()}}));
            }
            "execute" if authenticated => {
                let command = params.get("command").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let timeout_secs = params.get("timeout").and_then(|v| v.as_f64()).unwrap_or(30.0);
                let cwd = params.get("cwd").and_then(|v| v.as_str()).map(|s| s.to_string());
                let is_exclusive = params.get("exclusive").and_then(|v| v.as_bool()).unwrap_or(false);

                if exclusive_busy.load(Ordering::SeqCst) {
                    send_frame(&tx, serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}}));
                    continue;
                }
                if is_exclusive
                    && exclusive_busy
                        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                        .is_err()
                {
                    send_frame(&tx, serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}}));
                    continue;
                }

                let tx = tx.clone();
                let exclusive_busy = Arc::clone(&exclusive_busy);
                tokio::spawn(async move {
                    let result = run_command(&command, timeout_secs, cwd.as_deref()).await;

                    if is_exclusive {
                        exclusive_busy.store(false, Ordering::SeqCst);
                    }

                    send_frame(&tx, serde_json::json!({
                        "id": id,
                        "result": {
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                            "exit_code": result.exit_code
                        }
                    }));
                });
            }
            "execute_stream" if authenticated => {
                let command = params.get("command").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let timeout_secs = params.get("timeout").and_then(|v| v.as_f64()).unwrap_or(30.0);
                let cwd = params.get("cwd").and_then(|v| v.as_str()).map(|s| s.to_string());
                let is_exclusive = params.get("exclusive").and_then(|v| v.as_bool()).unwrap_or(false);

                if exclusive_busy.load(Ordering::SeqCst) {
                    send_frame(&tx, serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}}));
                    continue;
                }
                if is_exclusive
                    && exclusive_busy
                        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                        .is_err()
                {
                    send_frame(&tx, serde_json::json!({"id": id, "error": {"message": "Exclusive command in progress"}}));
                    continue;
                }

                let tx = tx.clone();
                let exclusive_busy = Arc::clone(&exclusive_busy);
                tokio::spawn(async move {
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
                                send_frame(&tx, serde_json::json!({"id": id, "stream": "stderr", "data": e.to_string()}));
                                send_frame(&tx, serde_json::json!({"id": id, "stream": "exit", "exit_code": -1}));
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
                                            send_frame(&tx, serde_json::json!({"id": id, "stream": "stdout", "data": format!("{}\n", text)}));
                                        }
                                        _ => stdout_done = true,
                                    }
                                }
                                line = stderr_reader.next_line(), if !stderr_done => {
                                    match line {
                                        Ok(Some(text)) => {
                                            send_frame(&tx, serde_json::json!({"id": id, "stream": "stderr", "data": format!("{}\n", text)}));
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

                        send_frame(&tx, serde_json::json!({"id": id, "stream": "exit", "exit_code": exit_code}));
                    })
                    .await;

                    if result.is_err() {
                        send_frame(&tx, serde_json::json!({"id": id, "stream": "stderr", "data": format!("Command timed out after {} seconds\n", timeout_secs)}));
                        send_frame(&tx, serde_json::json!({"id": id, "stream": "exit", "exit_code": -1}));
                    }

                    if is_exclusive {
                        exclusive_busy.store(false, Ordering::SeqCst);
                    }
                });
            }
            _ if !authenticated => {
                send_frame(&tx, serde_json::json!({"id": id, "error": {"message": "Not authenticated"}}));
            }
            _ => {
                send_frame(&tx, serde_json::json!({"id": id, "error": {"message": format!("Unknown method: {}", method)}}));
            }
        }
    }

    // Read loop ended (peer closed or read error). Drop the last sender so the
    // writer task's channel closes and it exits, then join it.
    drop(tx);
    let _ = writer_task.await;
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
