//! Structured logging for Cello.
//!
//! Provides:
//! - `LogFormat` / `PyLoggingConfig` Python configuration classes
//! - `configure_logging()` which installs a global `tracing_subscriber` in
//!   text or JSON format (ELK/Loki-friendly, one JSON object per line)
//! - A process-wide `LoggingConfig` that `LoggingMiddleware` consults so the
//!   request/response access log shares the configured format, level, and
//!   trace-context behaviour.
//!
//! # Example
//! ```python
//! from cello.logging import configure_logging, LogFormat
//!
//! configure_logging(
//!     format=LogFormat.JSON,
//!     level="INFO",
//!     exclude_paths=["/health", "/metrics"],
//! )
//! ```

use pyo3::prelude::*;
use std::sync::OnceLock;

/// Structured logging output format.
// pyo3 0.20 simple enums compare by variant out of the box; `rename_all`
// exposes them to Python as `LogFormat.TEXT` / `LogFormat.JSON`.
#[pyclass(name = "LogFormat", rename_all = "SCREAMING_SNAKE_CASE")]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LogFormat {
    /// Human-readable, single-line text output.
    Text,
    /// Machine-readable JSON output (one object per line).
    Json,
}

/// Effective logging configuration used by the middleware and subscribers.
#[derive(Clone, Debug)]
pub struct LoggingConfig {
    /// Output format (text or JSON).
    pub format: LogFormat,
    /// Log level filter, e.g. "info", "debug", "warn".
    pub level: String,
    /// Whether request logs carry the active trace id / span id fields.
    pub include_trace_context: bool,
    /// Paths excluded from the access log.
    pub exclude_paths: Vec<String>,
    /// Whether to log request bodies.
    pub log_body: bool,
    /// Whether to log request headers.
    pub log_headers: bool,
}

impl Default for LoggingConfig {
    fn default() -> Self {
        Self {
            format: LogFormat::Text,
            level: "info".to_string(),
            include_trace_context: true,
            exclude_paths: vec!["/health".to_string(), "/metrics".to_string()],
            log_body: false,
            log_headers: false,
        }
    }
}

/// Python-exposed logging configuration.
#[pyclass(name = "LoggingConfig")]
#[derive(Clone)]
pub struct PyLoggingConfig {
    /// Output format: `LogFormat.TEXT` or `LogFormat.JSON`.
    #[pyo3(get, set)]
    pub format: LogFormat,
    /// Log level filter: "trace", "debug", "info", "warn", "error".
    #[pyo3(get, set)]
    pub level: String,
    /// Include `trace_id` / `span_id` fields when trace context is present.
    #[pyo3(get, set)]
    pub include_trace_context: bool,
    /// Paths excluded from the request/response access log.
    #[pyo3(get, set)]
    pub exclude_paths: Vec<String>,
    /// Log request bodies (off by default).
    #[pyo3(get, set)]
    pub log_body: bool,
    /// Log request headers (off by default).
    #[pyo3(get, set)]
    pub log_headers: bool,
}

#[pymethods]
impl PyLoggingConfig {
    #[new]
    #[pyo3(signature = (
        format = LogFormat::Text,
        level = "info".to_string(),
        include_trace_context = true,
        exclude_paths = None,
        log_body = false,
        log_headers = false
    ))]
    pub fn new(
        format: LogFormat,
        level: String,
        include_trace_context: bool,
        exclude_paths: Option<Vec<String>>,
        log_body: bool,
        log_headers: bool,
    ) -> Self {
        Self {
            format,
            level,
            include_trace_context,
            exclude_paths: exclude_paths.unwrap_or_default(),
            log_body,
            log_headers,
        }
    }
}

/// The process-wide logging configuration installed by `configure_logging()`.
static GLOBAL_CONFIG: OnceLock<LoggingConfig> = OnceLock::new();

/// Return the currently configured logging config (defaults when unset).
pub fn current_config() -> LoggingConfig {
    GLOBAL_CONFIG.get().cloned().unwrap_or_default()
}

/// Install the global `tracing_subscriber` for the given configuration.
///
/// Idempotent: if another subscriber (e.g. OpenTelemetry) already owns the
/// global subscriber, tracing keeps the existing one, but the configuration
/// below is still recorded so `LoggingMiddleware` honours it.
#[pyfunction]
#[pyo3(signature = (config = None))]
pub fn configure_logging(config: Option<&PyLoggingConfig>) -> PyResult<()> {
    let cfg = config.map(py_to_inner).unwrap_or_default();
    let _ = GLOBAL_CONFIG.set(cfg.clone());

    let filter = tracing_subscriber::EnvFilter::try_new(&cfg.level)
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));

    match cfg.format {
        LogFormat::Json => {
            // `.json()` needs the `json` feature (enabled in Cargo.toml); it
            // must be called on the default builder before env-filter/target
            // tweaks. `try_init` is idempotent when another subscriber already
            // owns the global registry.
            let _ = tracing_subscriber::fmt()
                .json()
                .with_env_filter(filter)
                .with_target(false)
                .try_init();
        }
        LogFormat::Text => {
            let _ = tracing_subscriber::fmt()
                .with_env_filter(filter)
                .with_target(false)
                .try_init();
        }
    }

    Ok(())
}

fn py_to_inner(config: &PyLoggingConfig) -> LoggingConfig {
    LoggingConfig {
        format: config.format,
        level: config.level.clone(),
        include_trace_context: config.include_trace_context,
        exclude_paths: config.exclude_paths.clone(),
        log_body: config.log_body,
        log_headers: config.log_headers,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_defaults() {
        let cfg = LoggingConfig::default();
        assert_eq!(cfg.format, LogFormat::Text);
        assert_eq!(cfg.level, "info");
        assert!(cfg.include_trace_context);
        assert!(cfg.exclude_paths.contains(&"/health".to_string()));
    }

    #[test]
    fn test_format_eq() {
        assert_eq!(LogFormat::Text, LogFormat::Text);
        assert_ne!(LogFormat::Text, LogFormat::Json);
    }

    #[test]
    fn test_py_config_roundtrip() {
        let config = PyLoggingConfig::new(
            LogFormat::Json,
            "debug".to_string(),
            false,
            Some(vec!["/ping".to_string()]),
            false,
            true,
        );
        let inner = py_to_inner(&config);
        assert_eq!(inner.format, LogFormat::Json);
        assert_eq!(inner.level, "debug");
        assert!(!inner.include_trace_context);
        assert_eq!(inner.exclude_paths, vec!["/ping".to_string()]);
        assert!(inner.log_headers);
    }
}
