use super::*;
use axum::{
    body::{to_bytes, Body, BodyDataStream},
    http::Request,
    response::Response,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use tokio::time::sleep;
use tower::ServiceExt;

const ID: &str = "0123456789abcdef0123456789abcdef";
const TOKEN: &str = "test-token";
const MISSING_USER: &str = "quicksand-no-such-stdin-user";

#[test]
fn stdin_request_conversion_preserves_user_and_execution_options() {
    let request: ExecuteRequest = serde_json::from_value(json!({
        "stdin_id": ID,
        "command": "cat",
        "shell": "/bin/bash",
        "cwd": "src",
        "user": "quicksand-stream-user",
        "timeout": 12.5,
        "exclusive": true
    }))
    .unwrap();
    let execution = request.into_stdin_execution();
    assert_eq!(execution.stdin_id, ID);
    assert_eq!(execution.command, "cat");
    assert_eq!(execution.shell, "/bin/bash");
    assert_eq!(execution.cwd.as_deref(), Some("src"));
    assert_eq!(execution.user.as_deref(), Some("quicksand-stream-user"));
    assert_eq!(execution.timeout, 12.5);
    assert!(execution.exclusive);

    let request: ExecuteRequest =
        serde_json::from_value(json!({"stdin_id": ID, "command": "cat"})).unwrap();
    let execution = request.into_stdin_execution();
    assert_eq!(execution.shell, "/bin/sh");
    assert!(execution.cwd.is_none());
    assert!(execution.user.is_none());
    assert_eq!(execution.timeout, 30.0);
    assert!(!execution.exclusive);
}

fn state() -> AppState {
    AppState {
        token: Arc::new(TOKEN.into()),
        exclusive_busy: Arc::new(AtomicBool::new(false)),
        stdin_executions: StdinExecutions::default(),
    }
}

async fn post_json(app: &Router, path: &str, value: Value, authenticated: bool) -> Response {
    let mut request = Request::builder()
        .method("POST")
        .uri(path)
        .header(header::CONTENT_TYPE, "application/json");
    if authenticated {
        request = request.header(header::AUTHORIZATION, format!("Bearer {TOKEN}"));
    }
    app.clone()
        .oneshot(request.body(Body::from(value.to_string())).unwrap())
        .await
        .unwrap()
}

async fn response_json(response: Response) -> Value {
    let bytes = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

async fn sse_next(stream: &mut BodyDataStream) -> Value {
    let frame = timeout(Duration::from_secs(3), stream.next())
        .await
        .expect("SSE event timed out")
        .expect("SSE ended before exit")
        .unwrap();
    let frame = std::str::from_utf8(&frame).unwrap();
    serde_json::from_str(frame.trim().strip_prefix("data:").unwrap().trim_start()).unwrap()
}

async fn wait_until_idle(busy: &AtomicBool) {
    timeout(Duration::from_secs(3), async {
        while busy.load(Ordering::SeqCst) {
            sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("exclusive state was not released");
}

#[tokio::test]
async fn http_capability_gating_and_control_authentication() {
    let app = http_router(state());
    let response = post_json(&app, "/authenticate", json!({"token": TOKEN}), false).await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response_json(response).await,
        json!({"authenticated": true, "capabilities": ["stdin_streaming"]})
    );
    let response = post_json(&app, "/authenticate", json!({"token": "wrong"}), false).await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(
        response_json(response).await,
        json!({"authenticated": false})
    );
    for (path, params) in [
        ("/stdin", json!({"stdin_id": ID, "data": ""})),
        ("/stdin", json!({"stdin_id": ID, "eof": true})),
        ("/cancel", json!({"stdin_id": ID})),
        ("/create_user", json!({"name": "invalid:name"})),
        ("/delete_user", json!({"name": "invalid:name"})),
    ] {
        let response = post_json(&app, path, params, false).await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(
            response_json(response).await,
            json!({"detail": "Invalid token"})
        );
    }
}

#[tokio::test]
async fn http_account_management_routes_remain_available() {
    let app = http_router(state());
    // Invalid names exercise both handlers without touching any host accounts.
    for path in ["/create_user", "/delete_user"] {
        let response = post_json(&app, path, json!({"name": "invalid:name"}), true).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(response).await,
            json!({"detail": "Invalid username: invalid:name"})
        );
    }
}

#[tokio::test]
async fn http_unknown_users_are_rejected_before_exclusive_state_changes() {
    assert!(lookup_user(MISSING_USER).is_none());
    let state = state();
    let app = http_router(state.clone());
    for already_busy in [false, true] {
        state.exclusive_busy.store(already_busy, Ordering::SeqCst);
        for (path, piped) in [
            ("/execute", false),
            ("/execute_stream", false),
            ("/execute_stream", true),
        ] {
            let mut params = json!({
                "command": "printf should-not-run",
                "user": MISSING_USER,
                "exclusive": true
            });
            if piped {
                params["stdin_id"] = json!(ID);
            }
            let response = post_json(&app, path, params, true).await;
            assert_eq!(response.status(), StatusCode::BAD_REQUEST);
            assert_eq!(
                response_json(response).await,
                json!({"detail": format!("No such user: {MISSING_USER}")})
            );
            assert_eq!(
                state.exclusive_busy.load(Ordering::SeqCst),
                already_busy
            );
            let response = post_json(&app, "/cancel", json!({"stdin_id": ID}), true).await;
            assert_eq!(response_json(response).await, json!({"cancelled": false}));
        }
    }
}

#[tokio::test]
async fn http_binary_input_eof_and_exclusive_controls() {
    let state = state();
    let app = http_router(state.clone());
    let response = post_json(
        &app,
        "/execute_stream",
        json!({"stdin_id": ID, "command": "od -An -v -tx1", "exclusive": true}),
        true,
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers()[header::CONTENT_TYPE],
        "text/event-stream"
    );
    let mut output = response.into_body().into_data_stream();
    assert_eq!(sse_next(&mut output).await, json!({"stream": "ready"}));
    let response = post_json(&app, "/execute", json!({"command": "printf wrong"}), true).await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    for bytes in [&b"\0\xff"[..], &b"\x80\n\r"[..]] {
        let response = post_json(
            &app,
            "/stdin",
            json!({"stdin_id": ID, "data": STANDARD.encode(bytes)}),
            true,
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response_json(response).await, json!({"closed": false}));
    }
    let response = post_json(&app, "/stdin", json!({"stdin_id": ID, "eof": true}), true).await;
    assert_eq!(response_json(response).await, json!({"closed": true}));
    let mut stdout = String::new();
    loop {
        let event = sse_next(&mut output).await;
        assert!(event.get("id").is_none());
        match event["stream"].as_str().unwrap() {
            "stdout" => stdout.push_str(event["data"].as_str().unwrap()),
            "exit" => {
                assert_eq!(event["exit_code"], 0);
                break;
            }
            stream => panic!("unexpected stream {stream}: {event}"),
        }
    }
    assert_eq!(
        stdout.split_whitespace().collect::<Vec<_>>(),
        ["00", "ff", "80", "0a", "0d"]
    );
    assert!(!state.exclusive_busy.load(Ordering::SeqCst));
    let response = post_json(&app, "/stdin", json!({"stdin_id": ID, "data": ""}), true).await;
    assert_eq!(response_json(response).await, json!({"closed": true}));
    let response = post_json(&app, "/cancel", json!({"stdin_id": ID}), true).await;
    assert_eq!(response_json(response).await, json!({"cancelled": false}));
}

#[tokio::test]
async fn http_validation_duplicate_ids_and_startup_errors_are_explicit() {
    let state = state();
    let app = http_router(state.clone());
    for params in [
        json!({"stdin_id": ID, "eof": false}),
        json!({"stdin_id": ID, "eof": true, "data": ""}),
        json!({"stdin_id": ID, "data": "!!!"}),
        json!({"stdin_id": ID, "data": STANDARD.encode(vec![0; 65537])}),
    ] {
        let response = post_json(&app, "/stdin", params, true).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert!(response_json(response).await["detail"].is_string());
    }
    for params in [
        json!({"stdin_id": "invalid", "command": "cat"}),
        json!({"stdin_id": ID, "command": "cat", "timeout": -1}),
    ] {
        let response = post_json(&app, "/execute_stream", params, true).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
    let params = json!({"stdin_id": ID, "command": "cat", "exclusive": true});
    let response = post_json(&app, "/execute_stream", params.clone(), true).await;
    let mut output = response.into_body().into_data_stream();
    assert_eq!(sse_next(&mut output).await["stream"], "ready");
    let duplicate = post_json(&app, "/execute_stream", params, true).await;
    assert_eq!(duplicate.status(), StatusCode::CONFLICT);
    let response = post_json(&app, "/cancel", json!({"stdin_id": ID}), true).await;
    assert_eq!(response_json(response).await, json!({"cancelled": true}));
    assert!(!state.exclusive_busy.load(Ordering::SeqCst));
    assert_eq!(sse_next(&mut output).await["stream"], "stderr");
    assert_eq!(
        sse_next(&mut output).await,
        json!({"stream": "exit", "exit_code": -1})
    );
    let response = post_json(
        &app,
        "/execute_stream",
        json!({"stdin_id": ID, "command": "cat", "shell": "./quicksand-no-such-shell", "exclusive": true}),
        true,
    )
    .await;
    let mut output = response.into_body().into_data_stream();
    let error = sse_next(&mut output).await;
    assert_eq!(error["stream"], "stderr");
    assert!(error["data"]
        .as_str()
        .unwrap()
        .contains("Failed to start command"));
    assert_eq!(
        sse_next(&mut output).await,
        json!({"stream": "exit", "exit_code": -1})
    );
    assert!(!state.exclusive_busy.load(Ordering::SeqCst));
}

#[tokio::test]
async fn http_sse_disconnection_cancels_the_execution() {
    let state = state();
    let app = http_router(state.clone());
    let response = post_json(
        &app,
        "/execute_stream",
        json!({"stdin_id": ID, "command": "sleep 30", "exclusive": true}),
        true,
    )
    .await;
    let mut output = response.into_body().into_data_stream();
    assert_eq!(sse_next(&mut output).await["stream"], "ready");
    drop(output);
    wait_until_idle(&state.exclusive_busy).await;
    assert!(!state.stdin_executions.cancel(ID).await);
}

#[tokio::test]
async fn http_disconnection_before_ready_cancels_execution() {
    let state = state();
    let app = http_router(state.clone());
    let response = post_json(
        &app,
        "/execute_stream",
        json!({"stdin_id": ID, "command": "sleep 30", "exclusive": true}),
        true,
    )
    .await;
    assert!(state.exclusive_busy.load(Ordering::SeqCst));
    // Abandon the response without ever polling its SSE body.
    drop(response);
    wait_until_idle(&state.exclusive_busy).await;
    assert!(!state.stdin_executions.cancel(ID).await);
}

#[tokio::test]
async fn http_legacy_execution_shapes_and_line_streaming_are_unchanged() {
    let app = http_router(state());
    let response = post_json(&app, "/execute", json!({"command": "printf legacy"}), true).await;
    assert_eq!(
        response_json(response).await,
        json!({"stdout": "legacy", "stderr": "", "exit_code": 0})
    );
    let response = post_json(
        &app,
        "/execute_stream",
        json!({"command": "printf legacy"}),
        true,
    )
    .await;
    let mut output = response.into_body().into_data_stream();
    assert_eq!(
        sse_next(&mut output).await,
        json!({"stream": "stdout", "data": "legacy\n"})
    );
    assert_eq!(
        sse_next(&mut output).await,
        json!({"stream": "exit", "exit_code": 0})
    );
}

struct SerialTest {
    peer: Arc<SerialChannel>,
    server: tokio::task::JoinHandle<()>,
    executions: StdinExecutions,
    busy: Arc<AtomicBool>,
}

impl SerialTest {
    fn new() -> Self {
        let (server, peer) = std::os::unix::net::UnixStream::pair().unwrap();
        server.set_nonblocking(true).unwrap();
        peer.set_nonblocking(true).unwrap();
        let channel = |socket: std::os::unix::net::UnixStream| {
            Arc::new(SerialChannel {
                fd: tokio::io::unix::AsyncFd::new(std::os::fd::OwnedFd::from(socket)).unwrap(),
            })
        };
        let executions = StdinExecutions::default();
        let busy = Arc::new(AtomicBool::new(false));
        let server = tokio::spawn(serve_virtio_serial(
            channel(server),
            TOKEN.into(),
            Arc::clone(&busy),
            executions.clone(),
        ));
        Self {
            peer: channel(peer),
            server,
            executions,
            busy,
        }
    }

    async fn send(&self, id: u64, method: &str, params: Value) {
        write_frame_serial(
            &self.peer,
            &json!({"id": id, "method": method, "params": params}),
        )
        .await
        .unwrap();
    }

    async fn recv(&self) -> Value {
        timeout(Duration::from_secs(3), read_frame_serial(&self.peer))
            .await
            .expect("serial response timed out")
            .unwrap()
    }

    async fn authenticate(&self) {
        self.send(1, "authenticate", json!({"token": TOKEN})).await;
        assert_eq!(
            self.recv().await,
            json!({"id": 1, "result": {"authenticated": true, "capabilities": ["stdin_streaming"]}})
        );
    }

    async fn disconnect(self) {
        drop(self.peer);
        timeout(Duration::from_secs(3), self.server)
            .await
            .expect("serial server did not shut down")
            .unwrap();
        assert!(!self.busy.load(Ordering::SeqCst));
        assert!(!self.executions.cancel(ID).await);
    }
}

#[tokio::test]
async fn serial_controls_require_authentication_and_legacy_execution_still_works() {
    let serial = SerialTest::new();
    for (id, method, params) in [
        (10, "stdin", json!({"stdin_id": ID, "data": ""})),
        (11, "cancel", json!({"stdin_id": ID})),
        (15, "create_user", json!({"name": "invalid:name"})),
        (16, "delete_user", json!({"name": "invalid:name"})),
    ] {
        serial.send(id, method, params).await;
        assert_eq!(
            serial.recv().await,
            json!({"id": id, "error": {"message": "Not authenticated"}})
        );
    }
    serial
        .send(12, "authenticate", json!({"token": "wrong"}))
        .await;
    assert_eq!(
        serial.recv().await,
        json!({"id": 12, "result": {"authenticated": false}})
    );
    serial.authenticate().await;
    serial
        .send(13, "execute", json!({"command": "printf legacy"}))
        .await;
    assert_eq!(
        serial.recv().await,
        json!({"id": 13, "result": {"stdout": "legacy", "stderr": "", "exit_code": 0}})
    );
    serial
        .send(14, "execute_stream", json!({"command": "printf legacy"}))
        .await;
    assert_eq!(
        serial.recv().await,
        json!({"id": 14, "stream": "stdout", "data": "legacy\n"})
    );
    assert_eq!(
        serial.recv().await,
        json!({"id": 14, "stream": "exit", "exit_code": 0})
    );
    serial.disconnect().await;
}

#[tokio::test]
async fn serial_account_management_methods_remain_available() {
    let serial = SerialTest::new();
    serial.authenticate().await;
    for (id, method) in [(2, "create_user"), (3, "delete_user")] {
        serial.send(id, method, json!({"name": "invalid:name"})).await;
        assert_eq!(
            serial.recv().await,
            json!({"id": id, "error": {"message": "Invalid username: invalid:name"}})
        );
    }
    serial.disconnect().await;
}

#[tokio::test]
async fn serial_unknown_users_are_rejected_before_exclusive_state_changes() {
    assert!(lookup_user(MISSING_USER).is_none());
    let serial = SerialTest::new();
    serial.authenticate().await;
    for already_busy in [false, true] {
        serial.busy.store(already_busy, Ordering::SeqCst);
        for (id, method, piped) in [
            (2, "execute", false),
            (3, "execute_stream", false),
            (4, "execute_stream", true),
        ] {
            let mut params = json!({
                "command": "printf should-not-run",
                "user": MISSING_USER,
                "exclusive": true
            });
            if piped {
                params["stdin_id"] = json!(ID);
            }
            serial.send(id, method, params).await;
            let message = format!("No such user: {MISSING_USER}");
            let expected = if method == "execute" {
                json!({"id": id, "result": {"stdout": "", "stderr": message, "exit_code": -1}})
            } else if piped {
                json!({"id": id, "error": {"message": message}})
            } else {
                json!({"id": id, "stream": "stderr", "data": format!("{message}\n")})
            };
            assert_eq!(serial.recv().await, expected);
            if method == "execute_stream" {
                assert_eq!(
                    serial.recv().await,
                    json!({"id": id, "stream": "exit", "exit_code": -1})
                );
            }
            assert_eq!(serial.busy.load(Ordering::SeqCst), already_busy);
            assert!(!serial.executions.cancel(ID).await);
        }
    }
    serial.busy.store(false, Ordering::SeqCst);
    serial.disconnect().await;
}

#[tokio::test]
async fn serial_ready_incremental_output_shell_and_control_ack_ids() {
    let serial = SerialTest::new();
    serial.authenticate().await;
    serial
        .send(
            2,
            "execute_stream",
            json!({
                "stdin_id": ID,
                "command": "test -n \"$BASH_VERSION\" || exit 42; printf 'prompt:'; IFS= read -r line; printf '<%s>' \"$line\"; printf err >&2",
                "shell": "/bin/bash",
                "exclusive": true
            }),
        )
        .await;
    assert_eq!(serial.recv().await, json!({"id": 2, "stream": "ready"}));
    assert_eq!(
        serial.recv().await,
        json!({"id": 2, "stream": "stdout", "data": "prompt:"})
    );
    serial
        .send(
            3,
            "stdin",
            json!({"stdin_id": ID, "data": STANDARD.encode(b"hello\n")}),
        )
        .await;
    let mut events = Vec::new();
    loop {
        let frame = serial.recv().await;
        if frame["id"] == 3 {
            assert_eq!(frame, json!({"id": 3, "result": {"closed": false}}));
            break;
        }
        events.push(frame);
    }
    serial
        .send(4, "stdin", json!({"stdin_id": ID, "eof": true}))
        .await;
    let mut eof_ack = false;
    while !eof_ack || !events.iter().any(|event| event["stream"] == "exit") {
        let frame = serial.recv().await;
        if frame["id"] == 4 {
            assert_eq!(frame, json!({"id": 4, "result": {"closed": true}}));
            eof_ack = true;
        } else {
            events.push(frame);
        }
    }
    let mut stdout = String::new();
    let mut stderr = String::new();
    for event in events {
        assert_eq!(event["id"], 2);
        match event["stream"].as_str().unwrap() {
            "stdout" => stdout.push_str(event["data"].as_str().unwrap()),
            "stderr" => stderr.push_str(event["data"].as_str().unwrap()),
            "exit" => assert_eq!(event["exit_code"], 0),
            stream => panic!("unexpected stream {stream}"),
        }
    }
    assert_eq!((stdout.as_str(), stderr.as_str()), ("<hello>", "err"));
    serial.disconnect().await;
}

#[tokio::test]
async fn serial_cancel_before_ready_observation_finds_registered_execution() {
    let serial = SerialTest::new();
    serial.authenticate().await;
    let mut frames = Vec::new();
    for frame in [
        json!({
            "id": 2,
            "method": "execute_stream",
            "params": {"stdin_id": ID, "command": "sleep 30", "exclusive": true}
        }),
        json!({"id": 3, "method": "cancel", "params": {"stdin_id": ID}}),
    ] {
        let payload = serde_json::to_vec(&frame).unwrap();
        frames.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        frames.extend_from_slice(&payload);
    }
    // Queue both frames in order, without waiting for or reading ready.
    serial.peer.write_all(&frames).await.unwrap();
    let mut cancelled = false;
    let mut exited = false;
    while !cancelled || !exited {
        let response = serial.recv().await;
        if response["id"] == 3 {
            assert_eq!(response, json!({"id": 3, "result": {"cancelled": true}}));
            cancelled = true;
        } else {
            assert_eq!(response["id"], 2);
            match response["stream"].as_str().unwrap() {
                "ready" => {}
                "stderr" => assert!(response["data"].as_str().unwrap().contains("cancelled")),
                "exit" => {
                    assert_eq!(response["exit_code"], -1);
                    exited = true;
                }
                stream => panic!("unexpected stream {stream}"),
            }
        }
    }
    assert!(!serial.busy.load(Ordering::SeqCst));
    serial
        .send(4, "stdin", json!({"stdin_id": ID, "eof": true}))
        .await;
    assert_eq!(
        serial.recv().await,
        json!({"id": 4, "result": {"closed": true}})
    );
    serial.disconnect().await;
}

#[tokio::test]
async fn serial_cancel_is_dispatched_while_a_pipe_write_is_blocked() {
    let serial = SerialTest::new();
    serial.authenticate().await;
    serial
        .send(
            2,
            "execute_stream",
            json!({"stdin_id": ID, "command": "sleep 30", "exclusive": true}),
        )
        .await;
    assert_eq!(serial.recv().await, json!({"id": 2, "stream": "ready"}));
    let mut blocked_id = None;
    let mut events = Vec::new();
    for id in 10..42 {
        serial
            .send(
                id,
                "stdin",
                json!({"stdin_id": ID, "data": STANDARD.encode(vec![0; 65536])}),
            )
            .await;
        let response = read_frame_serial(&serial.peer);
        tokio::pin!(response);
        match timeout(Duration::from_millis(50), &mut response).await {
            Ok(frame) => assert_eq!(
                frame.unwrap(),
                json!({"id": id, "result": {"closed": false}})
            ),
            Err(_) => {
                serial.send(100, "cancel", json!({"stdin_id": ID})).await;
                events.push(
                    timeout(Duration::from_secs(3), &mut response)
                        .await
                        .unwrap()
                        .unwrap(),
                );
                blocked_id = Some(id);
                break;
            }
        }
    }
    let blocked_id = blocked_id.expect("non-reading child did not backpressure stdin");
    while !events.iter().any(|event| event["id"] == 100)
        || !events.iter().any(|event| event["id"] == blocked_id)
        || !events.iter().any(|event| event["stream"] == "exit")
    {
        events.push(serial.recv().await);
    }
    assert!(events.contains(&json!({"id": 100, "result": {"cancelled": true}})));
    assert!(events.contains(&json!({"id": blocked_id, "result": {"closed": true}})));
    assert!(events.contains(&json!({"id": 2, "stream": "exit", "exit_code": -1})));
    assert!(!serial.busy.load(Ordering::SeqCst));
    serial.send(101, "ping", json!({})).await;
    let ping = serial.recv().await;
    assert_eq!(ping["id"], 101);
    assert_eq!(ping["result"]["pong"], true);
    serial.disconnect().await;
}

#[tokio::test]
async fn serial_validation_duplicate_ids_and_startup_errors_are_terminal() {
    let serial = SerialTest::new();
    serial.authenticate().await;
    for (id, method, params) in [
        (10, "stdin", json!({"stdin_id": ID, "eof": false})),
        (11, "cancel", json!({"stdin_id": ID, "data": ""})),
    ] {
        serial.send(id, method, params).await;
        let response = serial.recv().await;
        assert_eq!(response["id"], id);
        assert!(response["error"]["message"].is_string());
    }
    serial
        .send(
            20,
            "execute_stream",
            json!({"stdin_id": ID, "command": "cat >/dev/null"}),
        )
        .await;
    assert_eq!(serial.recv().await, json!({"id": 20, "stream": "ready"}));
    serial
        .send(
            21,
            "execute_stream",
            json!({"stdin_id": ID, "command": "printf wrong"}),
        )
        .await;
    assert_eq!(
        serial.recv().await,
        json!({"id": 21, "error": {"message": "stdin_id is already active"}})
    );
    assert_eq!(
        serial.recv().await,
        json!({"id": 21, "stream": "exit", "exit_code": -1})
    );
    serial
        .send(22, "stdin", json!({"stdin_id": ID, "eof": true}))
        .await;
    let mut completed = false;
    let mut acknowledged = false;
    while !completed || !acknowledged {
        let response = serial.recv().await;
        if response["id"] == 20 {
            assert_eq!(
                response,
                json!({"id": 20, "stream": "exit", "exit_code": 0})
            );
            completed = true;
        } else {
            assert_eq!(response, json!({"id": 22, "result": {"closed": true}}));
            acknowledged = true;
        }
    }
    serial
        .send(
            30,
            "execute_stream",
            json!({"stdin_id": ID, "command": "cat", "shell": "./quicksand-no-such-shell", "exclusive": true}),
        )
        .await;
    let error = serial.recv().await;
    assert_eq!(error["id"], 30);
    assert_eq!(error["stream"], "stderr");
    assert!(error["data"]
        .as_str()
        .unwrap()
        .contains("Failed to start command"));
    assert_eq!(
        serial.recv().await,
        json!({"id": 30, "stream": "exit", "exit_code": -1})
    );
    assert!(!serial.busy.load(Ordering::SeqCst));
    serial.disconnect().await;
}

#[tokio::test]
async fn serial_disconnect_cancels_active_stdin_executions() {
    let serial = SerialTest::new();
    serial.authenticate().await;
    serial
        .send(
            2,
            "execute_stream",
            json!({"stdin_id": ID, "command": "sleep 30", "exclusive": true}),
        )
        .await;
    assert_eq!(serial.recv().await, json!({"id": 2, "stream": "ready"}));
    assert!(serial.busy.load(Ordering::SeqCst));
    serial.disconnect().await;
}
