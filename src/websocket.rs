//! WebSocket support for Cello.
//!
//! Real WebSocket connections (issue #19):
//! - The server performs the RFC 6455 upgrade handshake (`Sec-WebSocket-Accept`
//!   computed from `sha1` + base64), then wraps the stream in
//!   `tokio-tungstenite`.
//! - Every connection owns a pair of `tokio` channels: the Python handler
//!   queues outbound messages which a background writer task flushes to the
//!   socket, and a reader task forwards socket messages into an inbound queue
//!   the Python side drains via `recv()` / `await ws.receive()`.
//! - `WebSocket::new()` remains a fully functional in-memory stub for tests and
//!   offline usage; `run_session()` builds the real, channel-backed handle.

use parking_lot::RwLock;
use pyo3::prelude::*;
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::sync::mpsc;
use tokio::sync::Mutex as AsyncMutex;

/// WebSocket message types for Python.
///
/// Note: the `text` / `binary` static constructors intentionally keep those
/// names (they are used across the test suite and docs), so the payload
/// accessor is exposed as `payload` to avoid a pyo3 name clash.
#[pyclass]
#[derive(Clone)]
pub struct WebSocketMessage {
    /// Message type: "text", "binary", "ping", "pong", "close"
    #[pyo3(get)]
    pub msg_type: String,

    /// Text data (for text messages)
    pub text: Option<String>,

    /// Binary data (for binary messages)
    pub data: Option<Vec<u8>>,
}

#[pymethods]
impl WebSocketMessage {
    /// Create a text message.
    #[staticmethod]
    #[pyo3(name = "text")]
    pub fn from_text(content: &str) -> Self {
        WebSocketMessage {
            msg_type: "text".to_string(),
            text: Some(content.to_string()),
            data: None,
        }
    }

    /// Create a binary message.
    #[staticmethod]
    #[pyo3(name = "binary")]
    pub fn from_binary(content: Vec<u8>) -> Self {
        WebSocketMessage {
            msg_type: "binary".to_string(),
            text: None,
            data: Some(content),
        }
    }

    /// Create a ping message.
    #[staticmethod]
    pub fn ping() -> Self {
        WebSocketMessage {
            msg_type: "ping".to_string(),
            text: None,
            data: None,
        }
    }

    /// Create a pong message.
    #[staticmethod]
    pub fn pong() -> Self {
        WebSocketMessage {
            msg_type: "pong".to_string(),
            text: None,
            data: None,
        }
    }

    /// Create a close message.
    #[staticmethod]
    pub fn close() -> Self {
        WebSocketMessage {
            msg_type: "close".to_string(),
            text: None,
            data: None,
        }
    }

    /// The message payload: the text for text messages, bytes for binary
    /// messages, or `None` for control frames.
    #[getter]
    fn payload(&self, py: Python<'_>) -> Py<PyAny> {
        if let Some(text) = &self.text {
            let value: Py<PyAny> = text.clone().into_py(py);
            value
        } else if let Some(bytes) = &self.data {
            let value: Py<PyAny> = pyo3::types::PyBytes::new(py, bytes).into_py(py);
            value
        } else {
            py.None()
        }
    }

    /// Check if this is a text message.
    pub fn is_text(&self) -> bool {
        self.msg_type == "text"
    }

    /// Check if this is a binary message.
    pub fn is_binary(&self) -> bool {
        self.msg_type == "binary"
    }

    /// Check if this is a close message.
    pub fn is_close(&self) -> bool {
        self.msg_type == "close"
    }
}

/// Backend storage for a `WebSocket` handle.
#[derive(Clone)]
enum WsBackend {
    /// In-memory connection used by `WebSocket::new()` (tests/offline usage).
    Stub(Arc<RwLock<VecDeque<WebSocketMessage>>>),
    /// Real connection backed by tokio channels to the tungstenite session.
    Real(RealBackend),
}

/// Channel pair bridging the Python handle and the socket session.
#[derive(Clone)]
struct RealBackend {
    /// Outbound queue drained by the writer task.
    outbound: mpsc::UnboundedSender<WebSocketMessage>,
    /// Inbound queue fed by the reader task.
    inbound: Arc<AsyncMutex<mpsc::UnboundedReceiver<WebSocketMessage>>>,
    /// Set when either side closed the connection.
    closed: Arc<AtomicBool>,
}

/// WebSocket connection handle exposed to Python handlers.
#[pyclass]
pub struct WebSocket {
    /// Connection state at creation time.
    #[pyo3(get)]
    pub connected: bool,
    /// Remote peer address (empty string for test stubs).
    #[pyo3(get)]
    pub peer: String,
    /// Underlying backend (stub or real channel pair).
    backend: WsBackend,
    /// Set once the connection has been closed by either side.
    closed: Arc<AtomicBool>,
}

#[pymethods]
impl WebSocket {
    /// Create a WebSocket stub (for testing/mocking, no real socket).
    #[new]
    pub fn new() -> Self {
        WebSocket {
            connected: true,
            peer: String::new(),
            backend: WsBackend::Stub(Arc::new(RwLock::new(VecDeque::new()))),
            closed: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Send a text message (queues for sending).
    pub fn send_text(&self, text: &str) -> PyResult<()> {
        self.push(WebSocketMessage::from_text(text))
    }

    /// Send a binary message (queues for sending).
    pub fn send_binary(&self, data: Vec<u8>) -> PyResult<()> {
        self.push(WebSocketMessage::from_binary(data))
    }

    /// Send a JSON-serializable value as a text message.
    pub fn send_json<'py>(&self, py: Python<'py>, obj: &'py PyAny) -> PyResult<()> {
        let json_str: String = py.import("json")?.call_method1("dumps", (obj,))?.extract()?;
        self.send_text(&json_str)
    }

    /// Send a message (queues for sending).
    pub fn send(&self, message: WebSocketMessage) -> PyResult<()> {
        self.push(message)
    }

    /// Non-blocking receive. Returns `None` when the queue is empty.
    pub fn recv(&self) -> Option<WebSocketMessage> {
        match &self.backend {
            WsBackend::Stub(queue) => queue.write().pop_front(),
            WsBackend::Real(real) => match real.inbound.try_lock() {
                Ok(mut rx) => rx.try_recv().ok(),
                Err(_) => None,
            },
        }
    }

    /// Check whether the connection has been closed.
    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::Relaxed)
    }

    /// Close the WebSocket connection (sends a close frame, then closes).
    pub fn close(&self) -> PyResult<()> {
        // Queue the close frame first — `push` refuses messages once the
        // closed flag is set.
        let _ = self.push(WebSocketMessage::close());
        self.closed.store(true, Ordering::Relaxed);
        Ok(())
    }

    /// Get queued messages (test stub support).
    pub fn get_queued_messages(&self) -> Vec<WebSocketMessage> {
        match &self.backend {
            WsBackend::Stub(queue) => queue.read().iter().cloned().collect(),
            WsBackend::Real(_) => Vec::new(),
        }
    }

    /// Accept the connection (already accepted by the server; kept for API compat).
    pub fn accept(&self) -> PyResult<()> {
        Ok(())
    }

    // ── Async API (driven on the persistent asyncio loop) ────────────────────

    /// Await the next message. Returns `None` when the connection closes.
    pub fn receive<'py>(&self, py: Python<'py>) -> PyResult<&'py PyAny> {
        let backend = self.backend.clone();
        pyo3_asyncio::tokio::future_into_py(py, async move {
            Ok(receive_from_backend(backend).await)
        })
    }

    /// Await the next text message.
    pub fn receive_text<'py>(&self, py: Python<'py>) -> PyResult<&'py PyAny> {
        let backend = self.backend.clone();
        pyo3_asyncio::tokio::future_into_py(py, async move {
            match receive_from_backend(backend).await {
                Some(msg) if msg.msg_type == "text" => Ok(msg.text),
                _ => Ok(None),
            }
        })
    }

    /// Await the next binary message (returned as `bytes`).
    pub fn receive_binary<'py>(&self, py: Python<'py>) -> PyResult<&'py PyAny> {
        let backend = self.backend.clone();
        pyo3_asyncio::tokio::future_into_py(py, async move {
            match receive_from_backend(backend).await {
                Some(msg) if msg.msg_type == "binary" => {
                    let bytes = msg.data.unwrap_or_default();
                    let data: Py<PyAny> = Python::with_gil(|py| {
                        pyo3::types::PyBytes::new(py, &bytes).into_py(py)
                    });
                    Ok(Some(data))
                }
                _ => Ok(None),
            }
        })
    }

    /// Await the next text message and parse it as JSON.
    pub fn receive_json<'py>(&self, py: Python<'py>) -> PyResult<&'py PyAny> {
        let backend = self.backend.clone();
        pyo3_asyncio::tokio::future_into_py(py, async move {
            let msg = receive_from_backend(backend).await;
            match msg {
                Some(msg) if msg.msg_type == "text" => match msg.text {
                    Some(text) => Python::with_gil(|py| {
                        let value = py.import("json")?.call_method1("loads", (text,))?;
                        Ok(Some(value.into_py(py)))
                    }),
                    None => Ok(None),
                },
                _ => Ok(None),
            }
        })
    }
}

impl WebSocket {
    /// Build a real, channel-backed handle for an accepted connection.
    pub fn from_parts(
        peer: String,
        outbound: mpsc::UnboundedSender<WebSocketMessage>,
        inbound: Arc<AsyncMutex<mpsc::UnboundedReceiver<WebSocketMessage>>>,
        closed: Arc<AtomicBool>,
    ) -> Self {
        WebSocket {
            connected: true,
            peer,
            backend: WsBackend::Real(RealBackend {
                outbound,
                inbound,
                closed: closed.clone(),
            }),
            closed,
        }
    }

    /// Queue a message for sending (stub or real backend).
    fn push(&self, message: WebSocketMessage) -> PyResult<()> {
        match &self.backend {
            WsBackend::Stub(queue) => {
                queue.write().push_back(message);
                Ok(())
            }
            WsBackend::Real(real) => {
                if real.closed.load(Ordering::Relaxed) {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(
                        "WebSocket is closed",
                    ));
                }
                let _ = real.outbound.send(message);
                Ok(())
            }
        }
    }
}

impl Default for WebSocket {
    fn default() -> Self {
        Self::new()
    }
}

/// Shared async receive: waits for the next inbound message.
async fn receive_from_backend(backend: WsBackend) -> Option<WebSocketMessage> {
    match backend {
        WsBackend::Stub(queue) => queue.write().pop_front(),
        WsBackend::Real(real) => real.inbound.lock().await.recv().await,
    }
}

/// WebSocket handler registry.
pub struct WebSocketRegistry {
    handlers: Arc<RwLock<HashMap<String, PyObject>>>,
}

impl WebSocketRegistry {
    pub fn new() -> Self {
        WebSocketRegistry {
            handlers: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    pub fn register(&self, path: &str, handler: PyObject) {
        self.handlers.write().insert(path.to_string(), handler);
    }

    pub fn get(&self, path: &str) -> Option<PyObject> {
        self.handlers.read().get(path).cloned()
    }

    pub fn contains(&self, path: &str) -> bool {
        self.handlers.read().contains_key(path)
    }
}

impl Default for WebSocketRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl Clone for WebSocketRegistry {
    fn clone(&self) -> Self {
        WebSocketRegistry {
            handlers: self.handlers.clone(),
        }
    }
}

// ============================================================================
// RFC 6455 handshake helpers
// ============================================================================

/// The magic GUID defined by RFC 6455 §1.3 for the handshake digest.
const WEBSOCKET_GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

/// Compute the `Sec-WebSocket-Accept` header value for a client key.
///
/// `base64(sha1(client_key + GUID))` per RFC 6455 §4.2.2.
pub fn accept_key(sec_websocket_key: &str) -> String {
    use base64::Engine;
    use sha1::{Digest, Sha1};

    let mut hasher = Sha1::new();
    hasher.update(sec_websocket_key.as_bytes());
    hasher.update(WEBSOCKET_GUID.as_bytes());
    base64::engine::general_purpose::STANDARD.encode(hasher.finalize())
}

/// Check whether a request is a WebSocket upgrade handshake (RFC 6455 §4.2.1).
pub fn is_websocket_upgrade(headers: &hyper::HeaderMap) -> bool {
    let upgrade = headers
        .get(hyper::header::UPGRADE)
        .and_then(|v| v.to_str().ok())
        .map(|v| v.eq_ignore_ascii_case("websocket"))
        .unwrap_or(false);
    let connection = headers
        .get(hyper::header::CONNECTION)
        .and_then(|v| v.to_str().ok())
        .map(|v| {
            v.to_ascii_lowercase()
                .split(',')
                .any(|token| token.trim() == "upgrade")
        })
        .unwrap_or(false);
    let has_key = headers.get("sec-websocket-key").is_some();
    upgrade && connection && has_key
}

/// Extract the client `Sec-WebSocket-Key` from a handshake request.
pub fn websocket_key(headers: &hyper::HeaderMap) -> Option<String> {
    headers
        .get("sec-websocket-key")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.to_string())
}

// ============================================================================
// WebSocket session
// ============================================================================

/// Run a full WebSocket session over an upgraded HTTP connection.
///
/// Wraps the upgraded IO in `tokio-tungstenite`, spawns a writer task that
/// drains the outbound channel into the socket and a reader task that forwards
/// socket messages into the inbound channel, then invokes the registered Python
/// handler with the channel-backed `WebSocket` handle. Async handlers are
/// driven on the persistent asyncio loop. Returns once the handler completes.
pub async fn run_session(upgraded: hyper::upgrade::Upgraded, handler: PyObject, peer: String) {
    use futures_util::{SinkExt, StreamExt};
    use hyper_util::rt::TokioIo;
    use tokio_tungstenite::tungstenite::protocol::Role;
    use tokio_tungstenite::tungstenite::Message;
    use tokio_tungstenite::WebSocketStream;

    let io = TokioIo::new(upgraded);
    let ws_stream = WebSocketStream::from_raw_socket(io, Role::Server, None).await;
    let (mut sink, mut stream) = ws_stream.split();

    let (out_tx, mut out_rx) = mpsc::unbounded_channel::<WebSocketMessage>();
    let (in_tx, in_rx) = mpsc::unbounded_channel::<WebSocketMessage>();
    let closed = Arc::new(AtomicBool::new(false));

    // Writer task: flush queued outbound messages to the socket.
    let writer = tokio::spawn(async move {
        while let Some(msg) = out_rx.recv().await {
            let wire = match msg.msg_type.as_str() {
                "text" => msg.text.map(Message::Text),
                "binary" => msg.data.map(Message::Binary),
                "ping" => Some(Message::Ping(Vec::new())),
                "pong" => Some(Message::Pong(Vec::new())),
                "close" => Some(Message::Close(None)),
                _ => None,
            };
            match wire {
                Some(Message::Close(_)) => {
                    let _ = sink.send(Message::Close(None)).await;
                    break;
                }
                Some(wire) => {
                    if sink.send(wire).await.is_err() {
                        break;
                    }
                }
                None => {}
            }
        }
        let _ = sink.close().await;
    });

    // Reader task: forward socket messages into the inbound channel.
    let reader_closed = closed.clone();
    let reader = tokio::spawn(async move {
        while let Some(item) = stream.next().await {
            match item {
                Ok(Message::Text(text)) => {
                    if in_tx.send(WebSocketMessage::from_text(&text)).is_err() {
                        break;
                    }
                }
                Ok(Message::Binary(data)) => {
                    if in_tx.send(WebSocketMessage::from_binary(data)).is_err() {
                        break;
                    }
                }
                Ok(Message::Ping(payload)) => {
                    let msg = WebSocketMessage {
                        msg_type: "ping".to_string(),
                        text: None,
                        data: Some(payload),
                    };
                    if in_tx.send(msg).is_err() {
                        break;
                    }
                }
                Ok(Message::Close(_)) => {
                    let _ = in_tx.send(WebSocketMessage::close());
                    reader_closed.store(true, Ordering::Relaxed);
                    break;
                }
                _ => {}
            }
        }
        // Dropping in_tx signals the Python side that the stream ended.
    });

    let ws = WebSocket::from_parts(
        peer,
        out_tx,
        Arc::new(AsyncMutex::new(in_rx)),
        closed.clone(),
    );

    // Invoke the Python handler; drive coroutines on the persistent loop.
    let call = Python::with_gil(|py| -> PyResult<PyObject> {
        let obj = handler.call1(py, (ws,))?;
        Ok(obj.into_py(py))
    });

    match call {
        Ok(obj) => {
            let is_coro = Python::with_gil(|py| {
                py.import("inspect")
                    .and_then(|inspect| {
                        inspect.call_method1("iscoroutine", (obj.as_ref(py),))
                    })
                    .and_then(|r| r.is_true())
                    .unwrap_or(false)
            });
            if is_coro {
                let _ = tokio::task::spawn_blocking(move || {
                    let _ = Python::with_gil(|py| {
                        crate::async_loop::run_coroutine_blocking(py, obj.as_ref(py))
                    });
                })
                .await;
            }
        }
        Err(err) => {
            eprintln!("WebSocket handler call failed: {err}");
        }
    }

    closed.store(true, Ordering::Relaxed);
    let _ = writer.await;
    let _ = reader.await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_websocket_message_text() {
        let msg = WebSocketMessage::from_text("Hello");
        assert!(msg.is_text());
        assert!(!msg.is_binary());
        assert_eq!(msg.text, Some("Hello".to_string()));
    }

    #[test]
    fn test_websocket_message_binary() {
        let msg = WebSocketMessage::from_binary(vec![1, 2, 3]);
        assert!(msg.is_binary());
        assert!(!msg.is_text());
    }

    #[test]
    fn test_websocket_registry() {
        let registry = WebSocketRegistry::new();
        assert!(!registry.contains("/ws"));
    }

    #[test]
    fn test_accept_key_rfc6455_vectors() {
        // RFC 6455 §1.3 example
        assert_eq!(
            accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        );
        // Second canonical example
        assert_eq!(
            accept_key("x3JJHMbDL1EzLkh9GBhXDw=="),
            "HSmrc0sMlYUkAGmm5OPpG2HaGWk="
        );
    }

    #[test]
    fn test_accept_key_stable() {
        let a = accept_key("abc");
        let b = accept_key("abc");
        assert_eq!(a, b);
        assert_ne!(a, accept_key("abd"));
    }

    #[test]
    fn test_stub_websocket_roundtrip() {
        let ws = WebSocket::new();
        ws.send_text("hello").unwrap();
        ws.send_binary(vec![9, 8, 7]).unwrap();
        ws.send(WebSocketMessage::from_text("third")).unwrap();

        let first = ws.recv().unwrap();
        assert!(first.is_text());
        assert_eq!(first.text, Some("hello".to_string()));

        let second = ws.recv().unwrap();
        assert!(second.is_binary());
        assert_eq!(second.data, Some(vec![9, 8, 7]));

        let third = ws.recv().unwrap();
        assert_eq!(third.text, Some("third".to_string()));

        assert!(ws.recv().is_none());
        assert_eq!(ws.get_queued_messages().len(), 3);
    }

    #[test]
    fn test_stub_websocket_close() {
        let ws = WebSocket::new();
        ws.close().unwrap();
        assert!(ws.is_closed());
        let msg = ws.recv().unwrap();
        assert!(msg.is_close());
    }
}
