use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fmt, io,
    process::Stdio,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::{Child, ChildStdin, Command},
    sync::{mpsc, oneshot, watch},
    time::{sleep_until, Instant},
};

pub const MAX_STDIN_CHUNK: usize = 65_536;
const MAX_ENCODED_CHUNK: usize = MAX_STDIN_CHUNK.div_ceil(3) * 4;
const OUTPUT_CHUNK: usize = 8192;

#[derive(Debug)]
pub enum RequestError {
    Invalid(String),
    Conflict(&'static str),
    ExclusiveBusy,
    Io(String),
}

impl fmt::Display for RequestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(message) | Self::Io(message) => f.write_str(message),
            Self::Conflict(message) => f.write_str(message),
            Self::ExclusiveBusy => f.write_str("Exclusive command in progress"),
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(tag = "stream", rename_all = "lowercase")]
pub enum OutputEvent {
    Ready,
    Stdout { data: String },
    Stderr { data: String },
    Exit { exit_code: i32 },
}

pub struct ExecutionRequest {
    pub stdin_id: String,
    pub command: String,
    pub shell: String,
    pub cwd: Option<String>,
    pub user: Option<String>,
    pub timeout: f64,
    pub exclusive: bool,
}

impl ExecutionRequest {
    fn build_command(&self) -> Result<Command, RequestError> {
        let user_pw = crate::resolve_user(&self.user).map_err(RequestError::Invalid)?;
        let mut command = Command::new(&self.shell);
        command
            .arg("-c")
            .arg(&self.command)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .kill_on_drop(true);
        crate::configure_user(command.as_std_mut(), self.cwd.as_deref(), user_pw.as_ref());
        Ok(command)
    }
}

#[derive(Deserialize)]
#[serde(untagged, deny_unknown_fields)]
enum StdinWireRequest {
    Data { stdin_id: String, data: String },
    Eof { stdin_id: String, eof: bool },
}

pub struct InputRequest {
    stdin_id: String,
    input: Input,
}

enum Input {
    Data(Vec<u8>),
    Eof,
}

fn validate_id(stdin_id: &str) -> Result<(), RequestError> {
    if stdin_id.len() != 32 || !stdin_id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RequestError::Invalid(
            "stdin_id must be a 32-character UUID hex string".into(),
        ));
    }
    Ok(())
}

impl InputRequest {
    pub fn parse(value: serde_json::Value) -> Result<Self, RequestError> {
        let request = serde_json::from_value(value).map_err(|error| {
            RequestError::Invalid(format!(
                "Expected stdin_id and either base64 data or eof: true: {error}"
            ))
        })?;
        let (stdin_id, input) = match request {
            StdinWireRequest::Data { stdin_id, data } => {
                if data.len() > MAX_ENCODED_CHUNK {
                    return Err(RequestError::Invalid(
                        "stdin data exceeds the maximum decoded chunk size of 65536 bytes".into(),
                    ));
                }
                let bytes = STANDARD.decode(data).map_err(|error| {
                    RequestError::Invalid(format!("Invalid base64 stdin data: {error}"))
                })?;
                if bytes.len() > MAX_STDIN_CHUNK {
                    return Err(RequestError::Invalid(
                        "stdin data exceeds the maximum decoded chunk size of 65536 bytes".into(),
                    ));
                }
                (stdin_id, Input::Data(bytes))
            }
            StdinWireRequest::Eof { stdin_id, eof } => {
                if !eof {
                    return Err(RequestError::Invalid("eof must be true".into()));
                }
                (stdin_id, Input::Eof)
            }
        };
        validate_id(&stdin_id)?;
        Ok(Self { stdin_id, input })
    }
}

pub fn parse_cancel(value: serde_json::Value) -> Result<String, RequestError> {
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct CancelRequest {
        stdin_id: String,
    }

    let request: CancelRequest = serde_json::from_value(value)
        .map_err(|error| RequestError::Invalid(format!("Invalid cancel request: {error}")))?;
    validate_id(&request.stdin_id)?;
    Ok(request.stdin_id)
}

struct InputWrite {
    input: Input,
    ack: oneshot::Sender<Result<bool, String>>,
}

pub struct InputAck {
    pending: Option<oneshot::Receiver<Result<bool, String>>>,
}

impl InputAck {
    pub async fn wait(self) -> Result<bool, RequestError> {
        match self.pending {
            Some(ack) => match ack.await {
                Ok(result) => result.map_err(RequestError::Io),
                // Dropping the writer closes its pipe and any outstanding ACKs.
                Err(_) => Ok(true),
            },
            None => Ok(true),
        }
    }
}

struct Control {
    input: mpsc::Sender<InputWrite>,
    input_closed: Arc<AtomicBool>,
    cancel: watch::Sender<bool>,
    done: watch::Receiver<bool>,
}

#[derive(Clone, Default)]
pub struct StdinExecutions {
    entries: Arc<Mutex<HashMap<String, Arc<Control>>>>,
}

impl StdinExecutions {
    pub fn start(
        &self,
        request: ExecutionRequest,
        exclusive_busy: Arc<AtomicBool>,
    ) -> Result<mpsc::Receiver<OutputEvent>, RequestError> {
        validate_id(&request.stdin_id)?;
        let duration = Duration::try_from_secs_f64(request.timeout)
            .map_err(|_| RequestError::Invalid("timeout must be finite and nonnegative".into()))?;
        let deadline = Instant::now()
            .checked_add(duration)
            .ok_or_else(|| RequestError::Invalid("timeout is too large".into()))?;
        // Resolve and configure the requested user before reserving an ID or
        // claiming exclusivity; an unknown user must never start a child.
        let command = request.build_command()?;

        let mut entries = self.entries.lock().expect("stdin registry poisoned");
        if entries.contains_key(&request.stdin_id) {
            return Err(RequestError::Conflict("stdin_id is already active"));
        }
        let exclusive = ExclusiveGuard::acquire(exclusive_busy, request.exclusive)?;
        let (input_tx, input_rx) = mpsc::channel(1);
        let (cancel_tx, cancel_rx) = watch::channel(false);
        let (done_tx, done_rx) = watch::channel(false);
        let control = Arc::new(Control {
            input: input_tx,
            input_closed: Arc::new(AtomicBool::new(false)),
            cancel: cancel_tx,
            done: done_rx,
        });
        // A following serial cancel frame must find the ID even before the
        // execution task runs or emits ready.
        entries.insert(request.stdin_id.clone(), Arc::clone(&control));
        let registration = Registration {
            executions: self.clone(),
            stdin_id: request.stdin_id.clone(),
            control,
            exclusive,
            done: done_tx,
        };
        drop(entries);

        let (output_tx, output_rx) = mpsc::channel(16);
        tokio::spawn(run(
            command,
            request.timeout,
            deadline,
            input_rx,
            cancel_rx,
            output_tx,
            registration,
        ));
        Ok(output_rx)
    }

    pub fn submit(&self, request: InputRequest) -> Result<InputAck, RequestError> {
        let control = self
            .entries
            .lock()
            .expect("stdin registry poisoned")
            .get(&request.stdin_id)
            .cloned();
        let Some(control) = control else {
            return Ok(InputAck { pending: None });
        };
        if control.input_closed.load(Ordering::SeqCst) || *control.cancel.borrow() {
            return Ok(InputAck { pending: None });
        }
        let (ack_tx, ack_rx) = oneshot::channel();
        // Never retain an unbounded number of chunks in pending HTTP/serial tasks.
        // There is one in-flight write and at most one queued request.
        match control.input.try_send(InputWrite {
            input: request.input,
            ack: ack_tx,
        }) {
            Ok(()) => Ok(InputAck {
                pending: Some(ack_rx),
            }),
            Err(mpsc::error::TrySendError::Closed(_)) => Ok(InputAck { pending: None }),
            Err(mpsc::error::TrySendError::Full(_)) => {
                Err(RequestError::Conflict("A stdin request is already pending"))
            }
        }
    }

    pub async fn cancel(&self, stdin_id: &str) -> bool {
        let control = self
            .entries
            .lock()
            .expect("stdin registry poisoned")
            .get(stdin_id)
            .cloned();
        let Some(control) = control else {
            return false;
        };
        // EOF/BrokenPipe can precede command completion; input_closed must not
        // disable cancellation of a command that is still processing.
        if *control.done.borrow() {
            return false;
        }
        let already_cancelled = control.cancel.send_replace(true);
        let mut done = control.done.clone();
        // The registration owns the sender, so closure also means it was dropped.
        let _ = done.wait_for(|done| *done).await;
        !already_cancelled
    }

    pub async fn cancel_all(&self) {
        let controls: Vec<_> = self
            .entries
            .lock()
            .expect("stdin registry poisoned")
            .values()
            .cloned()
            .collect();
        for control in &controls {
            control.cancel.send_replace(true);
        }
        for control in controls {
            let mut done = control.done.clone();
            let _ = done.wait_for(|done| *done).await;
        }
    }
}

struct ExclusiveGuard {
    busy: Arc<AtomicBool>,
}

impl ExclusiveGuard {
    fn acquire(busy: Arc<AtomicBool>, exclusive: bool) -> Result<Option<Self>, RequestError> {
        if busy.load(Ordering::SeqCst) {
            return Err(RequestError::ExclusiveBusy);
        }
        if exclusive {
            busy.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .map_err(|_| RequestError::ExclusiveBusy)?;
            Ok(Some(Self { busy }))
        } else {
            Ok(None)
        }
    }
}

impl Drop for ExclusiveGuard {
    fn drop(&mut self) {
        self.busy.store(false, Ordering::SeqCst);
    }
}

struct Registration {
    executions: StdinExecutions,
    stdin_id: String,
    control: Arc<Control>,
    exclusive: Option<ExclusiveGuard>,
    done: watch::Sender<bool>,
}

impl Drop for Registration {
    fn drop(&mut self) {
        self.control.input_closed.store(true, Ordering::SeqCst);
        let mut entries = self
            .executions
            .entries
            .lock()
            .expect("stdin registry poisoned");
        if entries
            .get(&self.stdin_id)
            .is_some_and(|entry| Arc::ptr_eq(entry, &self.control))
        {
            entries.remove(&self.stdin_id);
        }
        drop(entries);
        self.exclusive.take();
        self.done.send_replace(true);
    }
}

struct ProcessGroup {
    child: Child,
    pgid: i32,
    armed: bool,
}

impl ProcessGroup {
    fn new(child: Child) -> Self {
        Self {
            pgid: child.id().expect("new child has no PID") as i32,
            child,
            armed: true,
        }
    }

    fn kill_group(&mut self) -> io::Result<()> {
        if self.armed {
            // Each stdin execution is a new process group, never the agent's group.
            let result = unsafe { libc::kill(-self.pgid, libc::SIGKILL) };
            if result < 0 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() != Some(libc::ESRCH) {
                    return Err(error);
                }
            }
            self.armed = false;
        }
        Ok(())
    }

    async fn terminate_and_reap(&mut self) -> io::Result<()> {
        if let Err(group_error) = self.kill_group() {
            // Still reap the direct child if signalling the group failed.
            self.child.kill().await?;
            return Err(group_error);
        }
        self.child.wait().await?;
        Ok(())
    }
}

impl Drop for ProcessGroup {
    fn drop(&mut self) {
        if let Err(error) = self.kill_group() {
            crate::log(&format!("Failed to kill stdin process group: {error}"));
        }
        // Child::kill_on_drop is a second guard; normal paths explicitly reap it.
    }
}

enum Outcome {
    Exit(i32),
    Failed(String),
    Disconnected,
}

async fn run(
    mut command: Command,
    timeout_seconds: f64,
    deadline: Instant,
    input: mpsc::Receiver<InputWrite>,
    mut cancel: watch::Receiver<bool>,
    output: mpsc::Sender<OutputEvent>,
    registration: Registration,
) {
    let outcome = {
        // On startup failure, drop any queued input ACKs before terminal output.
        let input_requests = input;
        if output.is_closed() {
            Outcome::Disconnected
        } else if *cancel.borrow() {
            Outcome::Failed("Command cancelled".into())
        } else {
            match command.spawn() {
                Err(error) => Outcome::Failed(format!("Failed to start command: {error}")),
                Ok(child) => {
                    let mut process = ProcessGroup::new(child);
                    let input_closed = Arc::clone(&registration.control.input_closed);
                    let result = tokio::select! {
                        biased;
                        _ = cancel.wait_for(|cancelled| *cancelled) => {
                            Outcome::Failed("Command cancelled".into())
                        }
                        _ = output.closed() => Outcome::Disconnected,
                        _ = sleep_until(deadline) => {
                            Outcome::Failed(format!("Command timed out after {} seconds", timeout_seconds))
                        }
                        result = drive(&mut process, input_requests, input_closed, &output) => {
                            match result {
                                Ok(code) => Outcome::Exit(code),
                                Err(error) => Outcome::Failed(error),
                            }
                        }
                    };
                    match process.terminate_and_reap().await {
                        Ok(()) => result,
                        Err(error) => {
                            Outcome::Failed(format!("Failed to terminate command: {error}"))
                        }
                    }
                }
            }
        }
    };

    // Reaping, closing stdin and releasing exclusivity must not depend on a slow
    // output consumer accepting the final events.
    drop(registration);
    let exit_code = match outcome {
        Outcome::Disconnected => return,
        Outcome::Exit(code) => code,
        Outcome::Failed(message) => {
            if output
                .send(OutputEvent::Stderr { data: message })
                .await
                .is_err()
            {
                return;
            }
            -1
        }
    };
    // The receiver disappearing here is an ordinary transport disconnection.
    let _ = output.send(OutputEvent::Exit { exit_code }).await;
}

async fn drive(
    process: &mut ProcessGroup,
    input: mpsc::Receiver<InputWrite>,
    input_closed: Arc<AtomicBool>,
    output: &mpsc::Sender<OutputEvent>,
) -> Result<i32, String> {
    let stdin = process.child.stdin.take().expect("stdin was piped");
    let stdout = process.child.stdout.take().expect("stdout was piped");
    let stderr = process.child.stderr.take().expect("stderr was piped");
    let mut writer = Some(Box::pin(write_input(
        stdin,
        input,
        Arc::clone(&input_closed),
    )));
    let stdout = read_output(stdout, false, output);
    let stderr = read_output(stderr, true, output);
    tokio::pin!(stdout, stderr);
    output
        .send(OutputEvent::Ready)
        .await
        .map_err(|_| "Output transport closed".to_string())?;

    let mut exit_code = None;
    let mut stdout_done = false;
    let mut stderr_done = false;
    loop {
        if stdout_done && stderr_done {
            if let Some(code) = exit_code {
                return Ok(code);
            }
        }
        // These are independent futures: a blocked pipe write or output send
        // cannot keep the child wait (or the outer cancellation) from progressing.
        tokio::select! {
            result = process.child.wait(), if exit_code.is_none() => {
                input_closed.store(true, Ordering::SeqCst);
                writer.take();
                exit_code = Some(result.map_err(|error| format!("Failed to wait for command: {error}"))?
                    .code().unwrap_or(-1));
                // Background descendants must not hold the pipes open after the
                // command exits, or outlive an otherwise successful execution.
                process.kill_group().map_err(|error| format!("Failed to kill command descendants: {error}"))?;
            }
            result = async { writer.as_mut().expect("input writer exists").await }, if writer.is_some() => {
                writer.take();
                result?;
            }
            result = &mut stdout, if !stdout_done => {
                stdout_done = true;
                result?;
            }
            result = &mut stderr, if !stderr_done => {
                stderr_done = true;
                result?;
            }
        }
    }
}

struct InputClosed(Arc<AtomicBool>);

impl Drop for InputClosed {
    fn drop(&mut self) {
        self.0.store(true, Ordering::SeqCst);
    }
}

async fn write_input(
    mut stdin: ChildStdin,
    mut input: mpsc::Receiver<InputWrite>,
    closed: Arc<AtomicBool>,
) -> Result<(), String> {
    let _closed = InputClosed(closed);
    while let Some(request) = input.recv().await {
        match request.input {
            Input::Eof => {
                drop(stdin);
                // A lost ACK receiver means the host abandoned its control request.
                let _ = request.ack.send(Ok(true));
                return Ok(());
            }
            Input::Data(bytes) => match stdin.write_all(&bytes).await {
                Ok(()) => {
                    let _ = request.ack.send(Ok(false));
                }
                Err(error) if error.kind() == io::ErrorKind::BrokenPipe => {
                    let _ = request.ack.send(Ok(true));
                    return Ok(());
                }
                Err(error) => {
                    let message = format!("Failed to write stdin: {error}");
                    let _ = request.ack.send(Err(message.clone()));
                    return Err(message);
                }
            },
        }
    }
    Ok(())
}

#[derive(Default)]
struct Utf8Decoder {
    pending: Vec<u8>,
}

impl Utf8Decoder {
    fn decode(&mut self, bytes: &[u8], eof: bool) -> String {
        self.pending.extend_from_slice(bytes);
        let mut text = String::new();
        let mut consumed = 0;
        while consumed < self.pending.len() {
            match std::str::from_utf8(&self.pending[consumed..]) {
                Ok(valid) => {
                    text.push_str(valid);
                    consumed = self.pending.len();
                }
                Err(error) => {
                    let valid_end = consumed + error.valid_up_to();
                    text.push_str(
                        std::str::from_utf8(&self.pending[consumed..valid_end])
                            .expect("UTF-8 valid prefix"),
                    );
                    consumed = valid_end;
                    match error.error_len() {
                        Some(length) => {
                            text.push('\u{fffd}');
                            consumed += length;
                        }
                        None => {
                            if eof {
                                text.push('\u{fffd}');
                                consumed = self.pending.len();
                            }
                            break;
                        }
                    }
                }
            }
        }
        self.pending.drain(..consumed);
        text
    }
}

async fn read_output(
    mut reader: impl AsyncRead + Unpin,
    is_stderr: bool,
    output: &mpsc::Sender<OutputEvent>,
) -> Result<(), String> {
    let mut buffer = [0; OUTPUT_CHUNK];
    let mut decoder = Utf8Decoder::default();
    loop {
        let length = reader
            .read(&mut buffer)
            .await
            .map_err(|error| format!("Failed to read command output: {error}"))?;
        let data = decoder.decode(&buffer[..length], length == 0);
        if !data.is_empty() {
            let event = if is_stderr {
                OutputEvent::Stderr { data }
            } else {
                OutputEvent::Stdout { data }
            };
            output
                .send(event)
                .await
                .map_err(|_| "Output transport closed".to_string())?;
        }
        if length == 0 {
            return Ok(());
        }
    }
}

#[cfg(test)]
mod tests;
