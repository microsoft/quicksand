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
    ffi::CString,
    fs,
    io::Write,
    net::SocketAddr,
    os::unix::fs::{MetadataExt, PermissionsExt},
    os::unix::process::CommandExt,
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
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
    /// Optional OS user to run the command as. When set, the command is
    /// executed with that user's uid/gid/groups and HOME, defaulting cwd to
    /// the user's home directory.
    #[serde(default)]
    user: Option<String>,
}

fn default_timeout() -> f64 {
    30.0
}

#[derive(Deserialize)]
struct UserRequest {
    name: String,
    /// On delete, also remove the user's home directory.
    #[serde(default)]
    remove_home: bool,
}

#[derive(Serialize)]
struct UserCreatedResponse {
    uid: u32,
    gid: u32,
    home: String,
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
// Multi-user support
// ============================================================================
//
// Users are managed by editing the standard POSIX account files directly
// (/etc/passwd, /etc/group, /etc/shadow). This keeps the behaviour identical
// across every distro (Alpine, Ubuntu, ...) because those formats are
// standardised and every minimal guest defaults to the `files` nsswitch
// backend — no dependency on distro-specific `adduser`/`useradd` tools.

/// uid/gid range for quicksand-managed users.
const UID_MIN: u32 = 1000;
const UID_MAX: u32 = 60000;

/// Serializes account-file mutations so concurrent create/delete requests
/// (the agent multiplexes requests) can't produce a torn /etc/passwd write.
static USER_MGMT_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone)]
struct PwEntry {
    uid: u32,
    gid: u32,
    home: String,
}

/// Look up an existing user in /etc/passwd. Returns None if absent.
fn lookup_user(name: &str) -> Option<PwEntry> {
    let content = fs::read_to_string("/etc/passwd").ok()?;
    for line in content.lines() {
        let f: Vec<&str> = line.split(':').collect();
        if f.len() >= 7 && f[0] == name {
            return Some(PwEntry {
                uid: f[2].parse().ok()?,
                gid: f[3].parse().ok()?,
                home: f[5].to_string(),
            });
        }
    }
    None
}

/// Validate a username to prevent injection into the colon/newline-delimited
/// account files. POSIX-portable subset: starts with [a-z_], then [a-z0-9_-].
fn valid_username(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 32
        && name.bytes().enumerate().all(|(i, b)| match b {
            b'a'..=b'z' | b'_' => true,
            b'0'..=b'9' | b'-' if i > 0 => true,
            _ => false,
        })
}

/// Highest id in [UID_MIN, UID_MAX) across passwd+group, plus one.
fn next_free_id() -> u32 {
    let mut max = UID_MIN - 1;
    for (path, field) in [("/etc/passwd", 2usize), ("/etc/group", 2usize)] {
        if let Ok(content) = fs::read_to_string(path) {
            for line in content.lines() {
                let f: Vec<&str> = line.split(':').collect();
                if f.len() > field {
                    if let Ok(id) = f[field].parse::<u32>() {
                        if (UID_MIN..UID_MAX).contains(&id) && id > max {
                            max = id;
                        }
                    }
                }
            }
        }
    }
    max + 1
}

fn append_line(path: &str, line: &str) -> std::io::Result<()> {
    let mut f = fs::OpenOptions::new().append(true).create(true).open(path)?;
    f.write_all(line.as_bytes())?;
    f.write_all(b"\n")
}

fn remove_lines_for_user(path: &str, name: &str) -> std::io::Result<()> {
    let content = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return Ok(()), // file may not exist (e.g. shadow); nothing to do
    };
    let prefix = format!("{}:", name);
    let mut out: String = content
        .lines()
        .filter(|l| !l.starts_with(&prefix))
        .collect::<Vec<_>>()
        .join("\n");
    if !out.is_empty() {
        out.push('\n');
    }
    fs::write(path, out)
}

/// SIGKILL every process owned by `uid` (each /proc/<pid> dir is owned by the
/// process's real uid).
fn kill_user_processes(uid: u32) {
    if let Ok(entries) = fs::read_dir("/proc") {
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let pid = match fname.to_string_lossy().parse::<i32>() {
                Ok(p) => p,
                Err(_) => continue,
            };
            if let Ok(meta) = fs::metadata(format!("/proc/{}", pid)) {
                if meta.uid() == uid {
                    unsafe {
                        libc::kill(pid, libc::SIGKILL);
                    }
                }
            }
        }
    }
}

/// Create a new user account (distro-agnostic, native file manipulation).
fn create_user(name: &str) -> Result<PwEntry, String> {
    let _guard = USER_MGMT_LOCK.lock().unwrap();

    if !valid_username(name) {
        return Err(format!("Invalid username: {}", name));
    }
    if lookup_user(name).is_some() {
        return Err(format!("User already exists: {}", name));
    }

    let id = next_free_id();
    if id >= UID_MAX {
        return Err("No free uid available".to_string());
    }
    let home = format!("/home/{}", name);

    // group: name:x:gid:
    append_line("/etc/group", &format!("{}:x:{}:", name, id))
        .map_err(|e| format!("write /etc/group: {}", e))?;
    // passwd: name:x:uid:gid:gecos:home:shell  (gecos empty; /bin/sh is universal)
    append_line(
        "/etc/passwd",
        &format!("{}:x:{}:{}::{}:/bin/sh", name, id, id, home),
    )
    .map_err(|e| format!("write /etc/passwd: {}", e))?;
    // shadow: locked password (`!`); we never password-auth, only setuid.
    let _ = append_line("/etc/shadow", &format!("{}:!::0:99999:7:::", name));

    fs::create_dir_all(&home).map_err(|e| format!("create {}: {}", home, e))?;
    let c_home = CString::new(home.as_str()).map_err(|e| e.to_string())?;
    unsafe {
        if libc::chown(c_home.as_ptr(), id as _, id as _) != 0 {
            return Err(format!("chown {}: {}", home, std::io::Error::last_os_error()));
        }
    }
    fs::set_permissions(&home, fs::Permissions::from_mode(0o700))
        .map_err(|e| format!("chmod {}: {}", home, e))?;

    Ok(PwEntry { uid: id, gid: id, home })
}

/// Delete a user: kill its processes, strip account-file entries, optionally
/// remove its home directory.
fn delete_user(name: &str, remove_home: bool) -> Result<(), String> {
    let _guard = USER_MGMT_LOCK.lock().unwrap();

    if !valid_username(name) {
        return Err(format!("Invalid username: {}", name));
    }
    let pw = lookup_user(name).ok_or_else(|| format!("No such user: {}", name))?;

    kill_user_processes(pw.uid);

    remove_lines_for_user("/etc/passwd", name).map_err(|e| format!("edit /etc/passwd: {}", e))?;
    remove_lines_for_user("/etc/group", name).map_err(|e| format!("edit /etc/group: {}", e))?;
    let _ = remove_lines_for_user("/etc/shadow", name);

    if remove_home {
        let _ = fs::remove_dir_all(&pw.home);
    }
    Ok(())
}

/// Resolve an optional username into a `PwEntry`, returning an error string if
/// the user is requested but doesn't exist.
fn resolve_user(user: &Option<String>) -> Result<Option<(String, PwEntry)>, String> {
    match user {
        None => Ok(None),
        Some(u) => match lookup_user(u) {
            Some(pw) => Ok(Some((u.clone(), pw))),
            None => Err(format!("No such user: {}", u)),
        },
    }
}

/// Configure a Command (std or tokio) to run as `name`/`pw`: set HOME/USER and
/// register a pre_exec hook that, in order, joins the user's supplementary
/// groups then drops gid and uid. Everything happens while still root in the
/// forked child, before exec — ordering is explicit so the privilege drop is
/// correct regardless of std internals.
macro_rules! configure_user {
    ($cmd:expr, $pw:expr, $name:expr) => {{
        let pw_ref: &PwEntry = $pw;
        let nm: &str = $name;
        let uid = pw_ref.uid;
        let gid = pw_ref.gid;
        let name_c = CString::new(nm).expect("validated username");
        $cmd.env("HOME", &pw_ref.home).env("USER", nm).env("LOGNAME", nm);
        unsafe {
            $cmd.pre_exec(move || {
                // `as _` adapts to the target's libc types (e.g. initgroups'
                // basegroup is gid_t on Linux but c_int on macOS).
                if libc::initgroups(name_c.as_ptr(), gid as _) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                if libc::setgid(gid as _) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                if libc::setuid(uid as _) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }};
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

    // Resolve the target user (if any) before touching the exclusive lock so a
    // bad username can't leave the lock claimed.
    let user_pw = match resolve_user(&req.user) {
        Ok(v) => v,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(ErrorResponse { detail: e })).into_response(),
    };

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
        } else if let Some((_, pw)) = &user_pw {
            cmd.current_dir(&pw.home);
        }
        if let Some((name, pw)) = &user_pw {
            configure_user!(cmd, pw, name.as_str());
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

    // Resolve the target user (if any) before touching the exclusive lock.
    let user_pw = match resolve_user(&req.user) {
        Ok(v) => v,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(ErrorResponse { detail: e })).into_response(),
    };

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
            } else if let Some((_, pw)) = &user_pw {
                cmd.current_dir(&pw.home);
            }
            if let Some((name, pw)) = &user_pw {
                configure_user!(cmd, pw, name.as_str());
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

async fn create_user_handler(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(req): Json<UserRequest>,
) -> impl IntoResponse {
    if let Err(e) = verify_token(&headers, &state.token) {
        return e.into_response();
    }
    match create_user(&req.name) {
        Ok(pw) => (
            StatusCode::OK,
            Json(UserCreatedResponse {
                uid: pw.uid,
                gid: pw.gid,
                home: pw.home,
            }),
        )
            .into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(ErrorResponse { detail: e })).into_response(),
    }
}

async fn delete_user_handler(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(req): Json<UserRequest>,
) -> impl IntoResponse {
    if let Err(e) = verify_token(&headers, &state.token) {
        return e.into_response();
    }
    match delete_user(&req.name, req.remove_home) {
        Ok(()) => (StatusCode::OK, Json(serde_json::json!({"removed": true}))).into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(ErrorResponse { detail: e })).into_response(),
    }
}

// ============================================================================
// Shared command execution (used by both HTTP and virtio-serial transports)
// ============================================================================

struct ExecResult {
    stdout: String,
    stderr: String,
    exit_code: i32,
}

async fn run_command(
    command: &str,
    timeout_secs: f64,
    cwd: Option<&str>,
    user: Option<&str>,
) -> ExecResult {
    let user_pw = match resolve_user(&user.map(|s| s.to_string())) {
        Ok(v) => v,
        Err(e) => {
            return ExecResult {
                stdout: String::new(),
                stderr: e,
                exit_code: -1,
            }
        }
    };

    let timeout_duration = Duration::from_secs_f64(timeout_secs);

    let result = timeout(timeout_duration, async {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg(command);
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
        if let Some(dir) = cwd {
            cmd.current_dir(dir);
        } else if let Some((_, pw)) = &user_pw {
            cmd.current_dir(&pw.home);
        }
        if let Some((name, pw)) = &user_pw {
            configure_user!(cmd, pw, name.as_str());
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
                let user = params.get("user").and_then(|v| v.as_str()).map(|s| s.to_string());
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
                    let result =
                        run_command(&command, timeout_secs, cwd.as_deref(), user.as_deref()).await;

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
                let user = params.get("user").and_then(|v| v.as_str()).map(|s| s.to_string());
                let is_exclusive = params.get("exclusive").and_then(|v| v.as_bool()).unwrap_or(false);

                // Resolve the target user before claiming the exclusive lock.
                let user_pw = match resolve_user(&user) {
                    Ok(v) => v,
                    Err(e) => {
                        send_frame(&tx, serde_json::json!({"id": id, "stream": "stderr", "data": format!("{}\n", e)}));
                        send_frame(&tx, serde_json::json!({"id": id, "stream": "exit", "exit_code": -1}));
                        continue;
                    }
                };

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
                        } else if let Some((_, pw)) = &user_pw {
                            cmd.current_dir(&pw.home);
                        }
                        if let Some((name, pw)) = &user_pw {
                            configure_user!(cmd, pw, name.as_str());
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
            "create_user" if authenticated => {
                let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let resp = match create_user(name) {
                    Ok(pw) => serde_json::json!({"id": id, "result": {"uid": pw.uid, "gid": pw.gid, "home": pw.home}}),
                    Err(e) => serde_json::json!({"id": id, "error": {"message": e}}),
                };
                send_frame(&tx, resp);
            }
            "delete_user" if authenticated => {
                let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let remove_home = params.get("remove_home").and_then(|v| v.as_bool()).unwrap_or(false);
                let resp = match delete_user(name, remove_home) {
                    Ok(()) => serde_json::json!({"id": id, "result": {"removed": true}}),
                    Err(e) => serde_json::json!({"id": id, "error": {"message": e}}),
                };
                send_frame(&tx, resp);
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
        .route("/create_user", post(create_user_handler))
        .route("/delete_user", post(delete_user_handler))
        .route("/ping", get(ping))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    log(&format!("Listening on {}", addr));

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
