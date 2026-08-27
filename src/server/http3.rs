//! HTTP/3 (QUIC) server support (issue #9).
//!
//! Runs the same application (router, handlers, middleware, metrics) over
//! HTTP/3 by binding a QUIC UDP endpoint with `quinn`, then serving each
//! client connection through `h3`. Requests are translated into hyper
//! requests (body fed through `hyper::body::Body::channel()`) so the full
//! pipeline in `crate::server::handle_request` is reused unchanged.

use bytes::{Buf, Bytes};
use h3::error::ErrorLevel;
use http::{Request as HttpRequest, Response as HttpResponse};
use hyper::{Request as HyperRequest, Response as HyperResponse};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use crate::error::ErrorHandlerRegistry;
use crate::handler::HandlerRegistry;
use crate::middleware::{guards::GuardsMiddleware, MiddlewareChain};
use crate::router::Router;
use crate::server::protocols::Http3Config;
use crate::server::{handle_request, ServerMetrics, ShutdownCoordinator};

/// Shared serving context cloned into every connection/stream task.
#[derive(Clone)]
pub struct ServeCtx {
    pub router: Arc<Router>,
    pub handlers: Arc<HandlerRegistry>,
    pub middleware: Arc<MiddlewareChain>,
    pub metrics: Arc<ServerMetrics>,
    pub shutdown: Arc<ShutdownCoordinator>,
    pub dependency_container: Arc<crate::dependency::DependencyContainer>,
    pub guards: Arc<GuardsMiddleware>,
    pub prometheus: Arc<
        parking_lot::RwLock<Option<crate::middleware::prometheus::PrometheusMiddleware>>,
    >,
    pub error_handlers: Arc<ErrorHandlerRegistry>,
    pub max_body_size: usize,
    pub read_body_timeout: Option<Duration>,
    pub handler_timeout: Option<Duration>,
}

/// Run the HTTP/3 server: bind the QUIC UDP endpoint and serve requests until
/// shutdown.
pub async fn run(
    config: &Http3Config,
    cert_path: &str,
    key_path: &str,
    addr: SocketAddr,
    ctx: ServeCtx,
) -> Result<(), String> {
    use h3_quinn::quinn::{self, crypto::rustls::QuicServerConfig};
    use rustls::pki_types::CertificateDer;
    use std::fs::File;
    use std::io::BufReader;

    // Load the PEM certificate/key (the same files used by the TLS acceptor).
    let mut cert_reader = BufReader::new(
        File::open(cert_path).map_err(|e| format!("failed to open HTTP/3 certificate: {e}"))?,
    );
    let certs: Vec<CertificateDer<'static>> = rustls_pemfile::certs(&mut cert_reader)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("failed to parse HTTP/3 certificate: {e}"))?;
    if certs.is_empty() {
        return Err("HTTP/3 certificate file contains no certificates".to_string());
    }
    let mut key_reader = BufReader::new(
        File::open(key_path).map_err(|e| format!("failed to open HTTP/3 private key: {e}"))?,
    );
    let key = rustls_pemfile::private_key(&mut key_reader)
        .map_err(|e| format!("failed to parse HTTP/3 private key: {e}"))?
        .ok_or_else(|| "HTTP/3 private key file contains no private key".to_string())?;

    let mut tls_config = rustls::ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .map_err(|e| format!("invalid HTTP/3 certificate/key pair: {e}"))?;
    // HTTP/3 mandates the "h3" ALPN protocol.
    tls_config.alpn_protocols = vec![b"h3".to_vec()];
    if config.enable_0rtt {
        tls_config.max_early_data_size = u32::MAX;
    }

    let mut server_config = quinn::ServerConfig::with_crypto(Arc::new(
        QuicServerConfig::try_from(tls_config).map_err(|e| format!("invalid QUIC config: {e}"))?,
    ));

    // Apply the Http3Config to the QUIC transport.
    let mut transport = quinn::TransportConfig::default();
    let varint = |value: u64| quinn::VarInt::from_u64(value).unwrap_or_default();
    transport.max_idle_timeout(Some(varint(config.max_idle_timeout.as_secs())));
    transport.max_udp_payload_size(config.max_udp_payload_size);
    transport.initial_max_data(varint(config.initial_max_data));
    transport.initial_max_stream_data_bidi_local(varint(config.initial_max_stream_data_bidi));
    transport.initial_max_stream_data_bidi_remote(varint(config.initial_max_stream_data_bidi));
    transport.initial_max_stream_data_uni(varint(config.initial_max_stream_data_uni));
    transport.initial_max_streams_bidi(varint(config.initial_max_streams_bidi));
    transport.initial_max_streams_uni(varint(config.initial_max_streams_uni));
    server_config.transport_config(Arc::new(transport));

    let endpoint = quinn::Endpoint::server(server_config, addr)
        .map_err(|e| format!("failed to bind QUIC endpoint on {addr}: {e}"))?;

    while let Some(incoming) = endpoint.accept().await {
        let ctx = ctx.clone();
        tokio::spawn(async move {
            match incoming.await {
                Ok(conn) => {
                    let mut h3_conn = match h3::server::Connection::new(
                        h3_quinn::Connection::new(conn),
                    )
                    .await
                    {
                        Ok(conn) => conn,
                        Err(err) => {
                            eprintln!("HTTP/3 connection setup failed: {err}");
                            return;
                        }
                    };
                    loop {
                        match h3_conn.accept().await {
                            Ok(Some((req, stream))) => {
                                let ctx = ctx.clone();
                                tokio::spawn(async move {
                                    serve_stream(req, stream, &ctx).await;
                                });
                            }
                            Ok(None) => break,
                            Err(err) => match err.get_error_level() {
                                ErrorLevel::ConnectionError => break,
                                _ => continue,
                            },
                        }
                    }
                }
                Err(err) => {
                    eprintln!("HTTP/3 connection accept failed: {err}");
                }
            }
        });
    }

    endpoint.wait_idle().await;
    Ok(())
}

/// Serve one HTTP/3 request through the shared request pipeline.
async fn serve_stream<C>(
    req: HttpRequest<()>,
    mut stream: h3::server::RequestStream<C, Bytes>,
    ctx: &ServeCtx,
) where
    C: h3::quic::BidiStream<Bytes>,
{
    use http_body_util::BodyExt;

    let method = req.method().clone();
    let body_bytes = read_request_body(&mut stream, method.as_str()).await;

    // Feed the body through a public hyper body channel so the shared
    // `handle_request` pipeline can stream it like any other request body.
    let (mut tx, body) = hyper::body::Body::channel();
    if !body_bytes.is_empty() {
        let _ = tx.send_data(Bytes::from(body_bytes)).await;
    }
    tx.close();

    let mut builder = HyperRequest::builder().method(method).uri(req.uri().clone());
    for (key, value) in req.headers() {
        builder = builder.header(key.as_str(), value);
    }
    let hyper_req = match builder.body(body) {
        Ok(request) => request,
        Err(_) => return,
    };

    let response = match handle_request(
        hyper_req,
        &ctx.router,
        &ctx.handlers,
        &ctx.middleware,
        &ctx.metrics,
        &ctx.dependency_container,
        &ctx.guards,
        &ctx.prometheus,
        &ctx.error_handlers,
        ctx.max_body_size,
        ctx.read_body_timeout,
        ctx.handler_timeout,
    )
    .await
    {
        Ok(response) => response,
        Err(_) => {
            // `handle_request` returns Infallible; defensive fallback.
            let head = HttpResponse::builder()
                .status(http::StatusCode::INTERNAL_SERVER_ERROR)
                .body(())
                .expect("valid response");
            let _ = stream.send_response(head).await;
            let _ = stream.finish().await;
            return;
        }
    };

    send_response(stream, response).await;
}

/// Drain the request body from the h3 stream (empty for GET/HEAD).
async fn read_request_body<C>(
    stream: &mut h3::server::RequestStream<C, Bytes>,
    method: &str,
) -> Vec<u8>
where
    C: h3::quic::BidiStream<Bytes>,
{
    if method == "GET" || method == "HEAD" {
        return Vec::new();
    }
    let mut buffer = Vec::new();
    loop {
        match stream.recv_data().await {
            Ok(Some(mut chunk)) => {
                buffer.extend_from_slice(&chunk.copy_to_bytes(chunk.remaining()));
            }
            Ok(None) => break,
            Err(_) => break,
        }
    }
    buffer
}

/// Stream a hyper response back over the h3 request stream.
async fn send_response<C>(
    mut stream: h3::server::RequestStream<C, Bytes>,
    response: HyperResponse<http_body_util::Full<Bytes>>,
) where
    C: h3::quic::BidiStream<Bytes>,
{
    use http_body_util::BodyExt;

    let status = response.status();
    let mut builder = HttpResponse::builder().status(status);
    for (key, value) in response.headers() {
        builder = builder.header(key.as_str(), value);
    }
    let head = match builder.body(()) {
        Ok(head) => head,
        Err(_) => return,
    };

    if stream.send_response(head).await.is_err() {
        return;
    }
    let body = response.into_body();
    if let Ok(collected) = body.collect().await {
        let bytes = collected.to_bytes();
        if !bytes.is_empty() {
            let _ = stream.send_data(bytes).await;
        }
    }
    let _ = stream.finish().await;
}
