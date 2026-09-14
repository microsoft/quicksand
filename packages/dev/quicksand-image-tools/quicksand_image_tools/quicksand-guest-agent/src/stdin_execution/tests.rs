use super::*;
use serde_json::json;
use tokio::time::{sleep, timeout};

const ID: &str = "0123456789abcdef0123456789abcdef";
const OTHER_ID: &str = "fedcba9876543210fedcba9876543210";

fn request(command: &str) -> ExecutionRequest {
    ExecutionRequest {
        stdin_id: ID.into(),
        command: command.into(),
        shell: "/bin/sh".into(),
        cwd: None,
        user: None,
        timeout: 5.0,
        exclusive: false,
    }
}

fn data(stdin_id: &str, bytes: &[u8]) -> InputRequest {
    InputRequest::parse(json!({"stdin_id": stdin_id, "data": STANDARD.encode(bytes)})).unwrap()
}

async fn write(executions: &StdinExecutions, stdin_id: &str, bytes: &[u8]) -> bool {
    executions
        .submit(data(stdin_id, bytes))
        .unwrap()
        .wait()
        .await
        .unwrap()
}

async fn eof(executions: &StdinExecutions, stdin_id: &str) -> bool {
    executions
        .submit(InputRequest::parse(json!({"stdin_id": stdin_id, "eof": true})).unwrap())
        .unwrap()
        .wait()
        .await
        .unwrap()
}

async fn next(output: &mut mpsc::Receiver<OutputEvent>) -> OutputEvent {
    timeout(Duration::from_secs(3), output.recv())
        .await
        .expect("output timed out")
        .expect("output closed before exit")
}

async fn ready(output: &mut mpsc::Receiver<OutputEvent>) {
    assert!(matches!(next(output).await, OutputEvent::Ready));
}

async fn finish(mut output: mpsc::Receiver<OutputEvent>) -> (String, String, i32) {
    let mut stdout = String::new();
    let mut stderr = String::new();
    loop {
        match next(&mut output).await {
            OutputEvent::Stdout { data } => stdout.push_str(&data),
            OutputEvent::Stderr { data } => stderr.push_str(&data),
            OutputEvent::Exit { exit_code } => return (stdout, stderr, exit_code),
            OutputEvent::Ready => panic!("unexpected ready event"),
        }
    }
}

impl StdinExecutions {
    pub(crate) fn assert_no_registrations(&self) {
        assert!(self.entries.lock().unwrap().is_empty());
    }
}

fn assert_clean(executions: &StdinExecutions, busy: &AtomicBool) {
    executions.assert_no_registrations();
    assert!(!busy.load(Ordering::SeqCst));
}

#[test]
fn requested_user_configuration_matches_legacy_and_preserves_cwd_override() {
    let user = (
        "root".to_string(),
        crate::lookup_user("root").expect("root must exist in /etc/passwd"),
    );
    for cwd in [None, Some("src")] {
        let mut request = request("cat");
        request.user = Some(user.0.clone());
        request.cwd = cwd.map(str::to_string);
        // Inspect configured commands without spawning a privilege-changing
        // child or modifying accounts on the host running these tests.
        let streaming = request.build_command().unwrap();
        let mut legacy = std::process::Command::new("/bin/sh");
        crate::configure_user(&mut legacy, cwd, Some(&user));
        for command in [streaming.as_std(), &legacy] {
            assert_eq!(
                command.get_current_dir(),
                Some(std::path::Path::new(cwd.unwrap_or(&user.1.home)))
            );
            for (key, value) in [
                ("HOME", user.1.home.as_str()),
                ("USER", user.0.as_str()),
                ("LOGNAME", user.0.as_str()),
            ] {
                let actual = command
                    .get_envs()
                    .find(|(name, _)| *name == key)
                    .and_then(|(_, value)| value);
                assert_eq!(actual, Some(std::ffi::OsStr::new(value)));
            }
        }
    }
}

#[tokio::test]
async fn unknown_user_is_rejected_before_registration_or_exclusivity() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let missing_user = "quicksand-no-such-stdin-user";
    assert!(crate::lookup_user(missing_user).is_none());
    for already_busy in [false, true] {
        busy.store(already_busy, Ordering::SeqCst);
        let mut request = request("printf should-not-run");
        request.user = Some(missing_user.into());
        request.exclusive = true;
        assert!(matches!(
            executions.start(request, Arc::clone(&busy)),
            Err(RequestError::Invalid(message)) if message == format!("No such user: {missing_user}")
        ));
        assert!(executions.entries.lock().unwrap().is_empty());
        assert_eq!(busy.load(Ordering::SeqCst), already_busy);
        assert!(write(&executions, ID, b"not registered").await);
        assert!(!executions.cancel(ID).await);
    }

    busy.store(false, Ordering::SeqCst);
    let mut output = executions
        .start(request("cat >/dev/null"), Arc::clone(&busy))
        .unwrap();
    ready(&mut output).await;
    assert!(eof(&executions, ID).await);
    assert_eq!(finish(output).await, ("".into(), "".into(), 0));
    assert_clean(&executions, &busy);
}

pub(super) async fn assert_process_stopped(pid: i32) {
    timeout(Duration::from_secs(3), async {
        loop {
            if unsafe { libc::kill(pid, 0) } < 0 {
                assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
                return;
            }
            // Orphan descendants may be zombies until the platform's init reaps
            // them. They are terminated; the directly owned child is reaped here.
            let status = std::process::Command::new("ps")
                .args(["-o", "stat=", "-p", &pid.to_string()])
                .output()
                .unwrap();
            if String::from_utf8_lossy(&status.stdout)
                .trim_start()
                .starts_with('Z')
            {
                return;
            }
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("command process survived cleanup");
}

#[test]
fn input_validation_is_strict_and_bounded() {
    for value in [
        json!({}),
        json!({"stdin_id": ID}),
        json!({"stdin_id": "invalid", "data": ""}),
        json!({"stdin_id": ID, "data": null}),
        json!({"stdin_id": ID, "data": "%%%"}),
        json!({"stdin_id": ID, "data": "Zg"}),
        json!({"stdin_id": ID, "data": "Zg==", "eof": true}),
        json!({"stdin_id": ID, "data": "Zg==", "eof": null}),
        json!({"stdin_id": ID, "eof": false}),
        json!({"stdin_id": ID, "eof": null}),
        json!({"stdin_id": ID, "eof": "true"}),
        json!({"stdin_id": ID, "eof": true, "extra": 1}),
        json!({"stdin_id": ID, "data": STANDARD.encode(vec![0; MAX_STDIN_CHUNK + 1])}),
        json!({"stdin_id": ID, "data": "A".repeat(MAX_ENCODED_CHUNK + 4)}),
    ] {
        assert!(
            matches!(
                InputRequest::parse(value.clone()),
                Err(RequestError::Invalid(_))
            ),
            "accepted malformed input: {value}"
        );
    }
    assert!(InputRequest::parse(json!({"stdin_id": ID, "data": ""})).is_ok());
    assert!(InputRequest::parse(json!({"stdin_id": ID, "eof": true})).is_ok());
    assert!(InputRequest::parse(json!({
        "stdin_id": ID, "data": STANDARD.encode(vec![255; MAX_STDIN_CHUNK])
    }))
    .is_ok());
    assert_eq!(parse_cancel(json!({"stdin_id": ID})).unwrap(), ID);
    for value in [
        json!({}),
        json!({"stdin_id": null}),
        json!({"stdin_id": "invalid"}),
        json!({"stdin_id": ID, "eof": true}),
    ] {
        assert!(parse_cancel(value).is_err());
    }
}

#[test]
fn utf8_decoder_preserves_split_characters_and_replaces_invalid_bytes() {
    let mut decoder = Utf8Decoder::default();
    assert_eq!(decoder.decode(b"prompt:\xf0", false), "prompt:");
    assert_eq!(decoder.decode(b"\x9f\x98", false), "");
    assert_eq!(decoder.decode(b"\x80!", false), "😀!");
    assert_eq!(decoder.decode(b"\xffx\xe2\x82", false), "�x");
    assert_eq!(decoder.decode(b"", true), "�");
    assert!(decoder.pending.is_empty());

    let bytes = "hello € 😀 world".as_bytes();
    for split in 0..=bytes.len() {
        let mut decoder = Utf8Decoder::default();
        let actual =
            decoder.decode(&bytes[..split], false) + &decoder.decode(&bytes[split..], true);
        assert_eq!(actual, "hello € 😀 world");
    }
}

#[tokio::test]
async fn output_is_incremental_without_newlines_or_utf8_corruption() {
    let (mut producer, reader) = tokio::io::duplex(32);
    let (tx, mut rx) = mpsc::channel(4);
    let reader = tokio::spawn(async move { read_output(reader, false, &tx).await });
    producer.write_all(b"prompt:\xe2").await.unwrap();
    assert!(matches!(next(&mut rx).await, OutputEvent::Stdout { data } if data == "prompt:"));
    producer.write_all(b"\x82").await.unwrap();
    assert!(timeout(Duration::from_millis(20), rx.recv()).await.is_err());
    producer.write_all(b"\xac!").await.unwrap();
    assert!(matches!(next(&mut rx).await, OutputEvent::Stdout { data } if data == "€!"));
    producer.write_all(b"\xf0\x9f").await.unwrap();
    drop(producer);
    assert!(matches!(next(&mut rx).await, OutputEvent::Stdout { data } if data == "�"));
    reader.await.unwrap().unwrap();
    assert!(rx.recv().await.is_none());
}

#[tokio::test]
async fn binary_chunks_are_written_exactly_once_and_eof_finishes() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut output = executions
        .start(request("od -An -v -tx1"), Arc::clone(&busy))
        .unwrap();
    ready(&mut output).await;
    let output = tokio::spawn(finish(output));
    let maximum: Vec<_> = (0u8..=255).cycle().take(MAX_STDIN_CHUNK).collect();
    let chunks: [&[u8]; 4] = [b"\0\xff\n", &maximum, b"", b"\x80\r\0end"];
    for chunk in chunks {
        assert!(!write(&executions, ID, chunk).await);
    }
    assert!(eof(&executions, ID).await);
    let (stdout, stderr, code) = output.await.unwrap();
    let actual: Vec<_> = stdout
        .split_whitespace()
        .map(|hex| u8::from_str_radix(hex, 16).unwrap())
        .collect();
    assert_eq!(actual, chunks.concat());
    assert_eq!((stderr.as_str(), code), ("", 0));
    assert_clean(&executions, &busy);
    assert!(write(&executions, ID, b"late").await);
    assert!(eof(&executions, ID).await);
    assert!(!executions.cancel(ID).await);
}

#[tokio::test]
async fn command_can_prompt_before_input_is_produced() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut output = executions
        .start(
            request("printf 'prompt:'; IFS= read -r line; printf '<%s>' \"$line\"; printf err >&2"),
            Arc::clone(&busy),
        )
        .unwrap();
    ready(&mut output).await;
    assert!(matches!(next(&mut output).await, OutputEvent::Stdout { data } if data == "prompt:"));
    assert!(!write(&executions, ID, b"hello\n").await);
    assert!(eof(&executions, ID).await);
    assert_eq!(finish(output).await, ("<hello>".into(), "err".into(), 0));
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn requested_shell_and_cwd_are_honored() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut request =
        request("test -n \"$BASH_VERSION\" || exit 42; printf '%s' \"$PWD\"; cat >/dev/null");
    request.shell = "/bin/bash".into();
    request.cwd = Some("src".into());
    let mut output = executions.start(request, Arc::clone(&busy)).unwrap();
    ready(&mut output).await;
    assert!(eof(&executions, ID).await);
    let (stdout, stderr, code) = finish(output).await;
    assert_eq!(
        stdout,
        std::env::current_dir()
            .unwrap()
            .join("src")
            .to_string_lossy()
    );
    assert_eq!((stderr.as_str(), code), ("", 0));
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn broken_pipe_is_closed_input_not_a_command_failure() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut output = executions
        .start(
            request("exec 0<&-; printf done; sleep 0.1"),
            Arc::clone(&busy),
        )
        .unwrap();
    ready(&mut output).await;
    assert!(matches!(next(&mut output).await, OutputEvent::Stdout { data } if data == "done"));
    assert!(write(&executions, ID, b"not consumed").await);
    assert_eq!(finish(output).await, ("".into(), "".into(), 0));
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn closed_input_remains_cancellable_while_the_command_is_processing() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    for (command, close_with_eof) in [
        ("cat >/dev/null; printf processing; sleep 30", true),
        ("exec 0<&-; printf processing; sleep 30", false),
    ] {
        let mut request = request(command);
        request.exclusive = true;
        let mut output = executions.start(request, Arc::clone(&busy)).unwrap();
        ready(&mut output).await;
        if close_with_eof {
            assert!(!write(&executions, ID, b"input").await);
            assert!(eof(&executions, ID).await);
        }
        assert!(
            matches!(next(&mut output).await, OutputEvent::Stdout { data } if data == "processing")
        );
        if !close_with_eof {
            assert!(write(&executions, ID, b"broken pipe").await);
        }
        assert!(write(&executions, ID, b"already closed").await);
        assert!(executions.entries.lock().unwrap().contains_key(ID));
        assert!(busy.load(Ordering::SeqCst));
        assert!(timeout(Duration::from_secs(2), executions.cancel(ID))
            .await
            .unwrap());
        let (stdout, stderr, code) = finish(output).await;
        assert_eq!((stdout.as_str(), code), ("", -1));
        assert!(stderr.contains("Command cancelled"));
        assert_clean(&executions, &busy);
    }
}

#[tokio::test]
async fn early_exit_does_not_wait_for_input_or_eof() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut output = executions
        .start(request("printf done"), Arc::clone(&busy))
        .unwrap();
    ready(&mut output).await;
    assert_eq!(finish(output).await, ("done".into(), "".into(), 0));
    assert!(write(&executions, ID, b"late").await);
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn duplicate_ids_and_exclusive_commands_are_rejected_without_overwriting() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut first = request("cat >/dev/null");
    first.exclusive = true;
    let mut output = executions.start(first, Arc::clone(&busy)).unwrap();
    ready(&mut output).await;
    assert!(matches!(
        executions.start(request("printf wrong"), Arc::clone(&busy)),
        Err(RequestError::Conflict(_))
    ));
    let mut other = request("printf wrong");
    other.stdin_id = OTHER_ID.into();
    assert!(matches!(
        executions.start(other, Arc::clone(&busy)),
        Err(RequestError::ExclusiveBusy)
    ));
    assert!(!write(&executions, ID, b"data").await);
    assert!(eof(&executions, ID).await);
    assert_eq!(finish(output).await, ("".into(), "".into(), 0));
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn queued_input_has_no_ack_until_written_and_is_bounded() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let output = executions
        .start(request("cat >/dev/null"), Arc::clone(&busy))
        .unwrap();
    let mut ack = executions.submit(data(ID, b"one")).unwrap();
    assert!(matches!(
        ack.pending.as_mut().unwrap().try_recv(),
        Err(oneshot::error::TryRecvError::Empty)
    ));
    assert!(matches!(
        executions.submit(data(ID, b"two")),
        Err(RequestError::Conflict(_))
    ));
    assert!(executions.cancel(ID).await);
    assert!(ack.wait().await.unwrap());
    drop(output);
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn startup_failures_are_terminal_and_release_exclusivity() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    for invalid_shell in [true, false] {
        let mut request = request("cat");
        request.exclusive = true;
        if invalid_shell {
            request.shell = "./quicksand-no-such-shell".into();
        } else {
            request.cwd = Some("./quicksand-no-such-working-directory".into());
        }
        let output = executions.start(request, Arc::clone(&busy)).unwrap();
        let (stdout, stderr, code) = finish(output).await;
        assert_eq!((stdout.as_str(), code), ("", -1));
        assert!(stderr.contains("Failed to start command"));
        assert_clean(&executions, &busy);
        assert!(write(&executions, ID, b"late").await);
    }
}

#[tokio::test]
async fn invalid_timeouts_do_not_create_executions() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    for seconds in [f64::NAN, f64::INFINITY, -1.0, f64::MAX] {
        let mut request = request("cat");
        request.timeout = seconds;
        request.exclusive = true;
        assert!(matches!(
            executions.start(request, Arc::clone(&busy)),
            Err(RequestError::Invalid(_))
        ));
        assert_clean(&executions, &busy);
    }
}

#[tokio::test]
async fn timeout_kills_the_process_group_and_releases_exclusivity() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut request = request("sleep 30 & printf '%s %s\\n' $$ $!; wait");
    request.timeout = 0.3;
    request.exclusive = true;
    let mut output = executions.start(request, Arc::clone(&busy)).unwrap();
    ready(&mut output).await;
    let (stdout, stderr, code) = finish(output).await;
    assert_eq!(code, -1);
    assert!(stderr.contains("Command timed out after 0.3 seconds"));
    for pid in stdout.split_whitespace() {
        assert_process_stopped(pid.parse().unwrap()).await;
    }
    assert_eq!(stdout.split_whitespace().count(), 2);
    assert!(write(&executions, ID, b"late").await);
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn cancellation_interrupts_a_blocked_write_and_preserves_unrelated_execution() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut other = request("IFS= read -r line; printf '%s' \"$line\"");
    other.stdin_id = OTHER_ID.into();
    let mut other_output = executions.start(other, Arc::clone(&busy)).unwrap();
    ready(&mut other_output).await;

    let mut blocked = request("sleep 30 & printf '%s %s\\n' $$ $!; wait");
    blocked.exclusive = true;
    let mut output = executions.start(blocked, Arc::clone(&busy)).unwrap();
    ready(&mut output).await;
    let writer_executions = executions.clone();
    let writer = tokio::spawn(async move {
        for _ in 0..128 {
            if write(&writer_executions, ID, &vec![0; MAX_STDIN_CHUNK]).await {
                return true;
            }
        }
        false
    });
    sleep(Duration::from_millis(100)).await;
    assert!(
        !writer.is_finished(),
        "a non-reading child should backpressure input"
    );
    assert!(timeout(Duration::from_secs(2), executions.cancel(ID))
        .await
        .unwrap());
    assert!(timeout(Duration::from_secs(2), writer)
        .await
        .unwrap()
        .unwrap());
    let (stdout, stderr, code) = finish(output).await;
    assert_eq!(code, -1);
    assert!(stderr.contains("Command cancelled"));
    for pid in stdout.split_whitespace() {
        assert_process_stopped(pid.parse().unwrap()).await;
    }
    assert!(!busy.load(Ordering::SeqCst));
    assert!(!write(&executions, OTHER_ID, b"survived\n").await);
    assert!(eof(&executions, OTHER_ID).await);
    assert_eq!(
        finish(other_output).await,
        ("survived".into(), "".into(), 0)
    );
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn cancellation_does_not_wait_for_a_blocked_output_consumer() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut request = request("while :; do printf 'output without newline'; done");
    request.exclusive = true;
    let mut output = executions.start(request, Arc::clone(&busy)).unwrap();
    ready(&mut output).await;
    sleep(Duration::from_millis(50)).await;
    assert!(timeout(Duration::from_secs(2), executions.cancel(ID))
        .await
        .unwrap());
    assert_clean(&executions, &busy);
    drop(output);
}

#[tokio::test]
async fn output_disconnection_cancels_and_reaps_execution() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut request = request("printf '%s' $$; sleep 30");
    request.exclusive = true;
    let mut output = executions.start(request, Arc::clone(&busy)).unwrap();
    ready(&mut output).await;
    let pid = match next(&mut output).await {
        OutputEvent::Stdout { data } => data.parse().unwrap(),
        event => panic!("unexpected event: {event:?}"),
    };
    let mut done = executions.entries.lock().unwrap()[ID].done.clone();
    drop(output);
    timeout(Duration::from_secs(2), done.wait_for(|done| *done))
        .await
        .unwrap()
        .unwrap();
    assert_process_stopped(pid).await;
    assert_clean(&executions, &busy);
}

#[tokio::test]
async fn successful_exit_kills_background_descendants() {
    let executions = StdinExecutions::default();
    let busy = Arc::new(AtomicBool::new(false));
    let mut output = executions
        .start(request("sleep 30 & printf '%s' $!"), Arc::clone(&busy))
        .unwrap();
    ready(&mut output).await;
    let (stdout, stderr, code) = finish(output).await;
    assert_eq!((stderr.as_str(), code), ("", 0));
    assert_process_stopped(stdout.parse().unwrap()).await;
    assert_clean(&executions, &busy);
}
