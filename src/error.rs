//! Advanced error handling for Cello.
//!
//! This module provides:
//! - RFC 7807 Problem Details for structured errors
//! - Error handler registry (global, status-based, exception-based)
//! - Blueprint-scoped error handlers
//! - Python exception capture with traceback

use parking_lot::RwLock;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use thiserror::Error;

use crate::json::python_to_json;
use crate::request::Request;
use crate::response::Response;

/// Whether *unhandled* error details are described to the client (development)
/// or replaced by a generic Problem Details body (production).
///
/// An unhandled handler error carries the Python exception's message (and, in
/// debug mode, its traceback) and a database failure carries server diagnostics
/// such as constraint names and row values, so echoing either to a client leaks
/// internals. `App.run(debug=False)` turns this off: the details go to the
/// server log and the response keeps only the Problem Details skeleton.
///
/// Only the *default* 5xx response is affected. Handlers registered with
/// `@app.exception_handler(...)` / `add_status_handler` still receive the
/// untouched error, so redaction can never silence a handler the user wrote.
static EXPOSE_ERROR_DETAILS: AtomicBool = AtomicBool::new(true);

/// Set whether unhandled-error details are described in 5xx responses.
pub fn set_expose_error_details(expose: bool) {
    EXPOSE_ERROR_DETAILS.store(expose, Ordering::Relaxed);
}

/// Whether unhandled-error details are described in 5xx responses.
pub fn expose_error_details() -> bool {
    EXPOSE_ERROR_DETAILS.load(Ordering::Relaxed)
}

/// Whether this error's message is internal detail that must not reach clients.
///
/// Client-facing errors (`Validation`, `NotFound`, `BadRequest`, …) carry a
/// message written for the caller, and so do framework 504s; the ones below are
/// the variants whose text comes from an exception or the database.
fn is_internal_detail(error: &AppError) -> bool {
    match error {
        AppError::Internal(_) | AppError::Handler(_) | AppError::PythonException(_) => {
            error.status_code() >= 500
        }
        AppError::Custom { status, .. } => *status >= 500,
        _ => false,
    }
}

/// RFC 7807 Problem Details structure.
///
/// This provides a standardized format for HTTP error responses.
/// See: <https://www.rfc-editor.org/rfc/rfc7807>
#[derive(Debug, Clone, Serialize, Deserialize)]
#[pyclass]
pub struct ProblemDetails {
    /// URI reference identifying the problem type.
    #[pyo3(get, set)]
    #[serde(rename = "type")]
    pub type_uri: String,

    /// Short, human-readable summary of the problem.
    #[pyo3(get, set)]
    pub title: String,

    /// HTTP status code.
    #[pyo3(get, set)]
    pub status: u16,

    /// Human-readable explanation specific to this occurrence.
    #[pyo3(get, set)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,

    /// URI reference for the specific occurrence.
    #[pyo3(get, set)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub instance: Option<String>,

    /// Additional problem-specific members (not exposed to Python).
    #[serde(flatten)]
    pub extensions: HashMap<String, JsonValue>,
}

#[pymethods]
impl ProblemDetails {
    #[new]
    #[pyo3(signature = (type_uri, title, status, detail=None, instance=None))]
    pub fn new(
        type_uri: String,
        title: String,
        status: u16,
        detail: Option<String>,
        instance: Option<String>,
    ) -> Self {
        Self {
            type_uri,
            title,
            status,
            detail,
            instance,
            extensions: HashMap::new(),
        }
    }

    /// Add an extension field (accepts string value).
    pub fn with_extension(&mut self, key: String, value: String) {
        self.extensions.insert(key, JsonValue::String(value));
    }

    /// Convert to JSON string.
    pub fn to_json(&self) -> PyResult<String> {
        serde_json::to_string(self)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    /// Convert to Response.
    pub fn to_response(&self) -> Response {
        let body = serde_json::to_vec(self).unwrap_or_default();
        let mut response = Response::new(self.status);
        response.set_body(body);
        response.set_header("Content-Type", "application/problem+json");
        response
    }
}

impl Default for ProblemDetails {
    fn default() -> Self {
        Self {
            type_uri: "about:blank".to_string(),
            title: "Internal Server Error".to_string(),
            status: 500,
            detail: None,
            instance: None,
            extensions: HashMap::new(),
        }
    }
}

/// Application error types.
#[derive(Error, Debug, Clone)]
pub enum AppError {
    #[error("Validation error: {message}")]
    Validation {
        message: String,
        errors: Vec<FieldError>,
    },

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Unauthorized: {0}")]
    Unauthorized(String),

    #[error("Forbidden: {0}")]
    Forbidden(String),

    #[error("Bad request: {0}")]
    BadRequest(String),

    #[error("Conflict: {0}")]
    Conflict(String),

    #[error("Rate limited")]
    RateLimited {
        retry_after: Option<u64>,
        message: String,
    },

    #[error("Timeout: {0}")]
    Timeout(String),

    #[error("Handler error: {0}")]
    Handler(String),

    #[error("Internal error: {0}")]
    Internal(String),

    #[error("Python exception: {}: {}", .0.exception_type, .0.message)]
    PythonException(PythonExceptionInfo),

    #[error("{message}")]
    Custom {
        status: u16,
        message: String,
        type_uri: Option<String>,
    },
}

impl AppError {
    /// Get the HTTP status code for this error.
    pub fn status_code(&self) -> u16 {
        match self {
            AppError::Validation { .. } => 422,
            AppError::NotFound(_) => 404,
            AppError::Unauthorized(_) => 401,
            AppError::Forbidden(_) => 403,
            AppError::BadRequest(_) => 400,
            AppError::Conflict(_) => 409,
            AppError::RateLimited { .. } => 429,
            AppError::Timeout(_) => 504,
            AppError::Handler(_) => 500,
            AppError::Internal(_) => 500,
            AppError::PythonException(info) => info.status.unwrap_or(500),
            AppError::Custom { status, .. } => *status,
        }
    }

    /// Convert to ProblemDetails.
    pub fn to_problem_details(&self) -> ProblemDetails {
        let (type_uri, title) = match self {
            AppError::Validation { .. } => ("/errors/validation", "Validation Error"),
            AppError::NotFound(_) => ("/errors/not-found", "Not Found"),
            AppError::Unauthorized(_) => ("/errors/unauthorized", "Unauthorized"),
            AppError::Forbidden(_) => ("/errors/forbidden", "Forbidden"),
            AppError::BadRequest(_) => ("/errors/bad-request", "Bad Request"),
            AppError::Conflict(_) => ("/errors/conflict", "Conflict"),
            AppError::RateLimited { .. } => ("/errors/rate-limited", "Too Many Requests"),
            AppError::Timeout(_) => ("/errors/timeout", "Gateway Timeout"),
            AppError::Handler(_) => ("/errors/handler-error", "Handler Error"),
            AppError::Internal(_) => ("/errors/internal", "Internal Server Error"),
            // A raised `cello.exceptions` error declares its status on the class,
            // so a `NotFoundError` renders exactly like a route miss would. Only
            // an exception with no declared status (or a 5xx) is a Python
            // exception as far as the body is concerned.
            AppError::PythonException(info) => match info.status {
                Some(status) if status < 500 => status_problem_meta(status),
                _ => ("/errors/python-exception", "Internal Server Error"),
            },
            AppError::Custom { type_uri, .. } => {
                (type_uri.as_deref().unwrap_or("/errors/custom"), "Error")
            }
        };

        // pyo3 renders a `PyErr` message as `TypeName: message`; a mapped 4xx
        // shows the exception's own text, and an internal error keeps the type
        // name, which is diagnostic there.
        let detail = match self {
            AppError::PythonException(info) => match info.status {
                Some(status) if status < 500 => info.bare_message().to_string(),
                _ => info.message.clone(),
            },
            _ => self.to_string(),
        };

        let mut details = ProblemDetails {
            type_uri: type_uri.to_string(),
            title: title.to_string(),
            status: self.status_code(),
            detail: Some(detail),
            instance: None,
            extensions: HashMap::new(),
        };

        // Add error-specific extensions
        match self {
            AppError::Validation { errors, .. } => {
                details.extensions.insert(
                    "errors".to_string(),
                    serde_json::to_value(errors).unwrap_or_default(),
                );
            }
            AppError::RateLimited { retry_after, .. } => {
                if let Some(seconds) = retry_after {
                    details
                        .extensions
                        .insert("retry_after".to_string(), serde_json::json!(seconds));
                }
            }
            AppError::PythonException(info) => {
                details.extensions.insert(
                    "exception_type".to_string(),
                    serde_json::json!(info.exception_type),
                );
            }
            _ => {}
        }

        details
    }

    /// Convert to Response.
    pub fn to_response(&self) -> Response {
        self.to_problem_details().to_response()
    }

    /// Convert to a Response with every internal detail removed.
    ///
    /// Keeps the Problem Details skeleton (type / title / status) so the body is
    /// still valid RFC 7807, but drops `detail` and the machine-readable
    /// extensions (`exception_type`, `traceback`, …) — the fields that can carry
    /// an exception message, a traceback, or Postgres diagnostics.
    pub fn to_redacted_response(&self) -> Response {
        let mut details = self.to_problem_details();
        details.detail = None;
        details.extensions.clear();
        details.to_response()
    }
}

/// `(type_uri, title)` for a bare HTTP status.
///
/// Used when a raised Python exception declares its own status, so the response
/// body matches what the equivalent `AppError` variant would produce.
fn status_problem_meta(status: u16) -> (&'static str, &'static str) {
    match status {
        400 => ("/errors/bad-request", "Bad Request"),
        401 => ("/errors/unauthorized", "Unauthorized"),
        403 => ("/errors/forbidden", "Forbidden"),
        404 => ("/errors/not-found", "Not Found"),
        409 => ("/errors/conflict", "Conflict"),
        422 => ("/errors/validation", "Validation Error"),
        429 => ("/errors/rate-limited", "Too Many Requests"),
        500 => ("/errors/internal", "Internal Server Error"),
        504 => ("/errors/timeout", "Gateway Timeout"),
        _ => ("/errors/custom", "Error"),
    }
}

/// Field-level validation error.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldError {
    /// Field name or path.
    pub field: String,
    /// Error message.
    pub message: String,
    /// Error code for programmatic handling.
    pub code: String,
}

impl FieldError {
    pub fn new(
        field: impl Into<String>,
        message: impl Into<String>,
        code: impl Into<String>,
    ) -> Self {
        Self {
            field: field.into(),
            message: message.into(),
            code: code.into(),
        }
    }
}

/// Python exception information with traceback.
#[derive(Debug)]
pub struct PythonExceptionInfo {
    /// Exception type name (e.g., "ValueError").
    pub exception_type: String,
    /// Exception message.
    pub message: String,
    /// Full traceback (if available).
    pub traceback: Option<String>,
    /// Source file (if available).
    pub file: Option<String>,
    /// Line number (if available).
    pub line: Option<u32>,
    /// The original Python exception instance, preserved for custom handlers.
    pub exception: Option<Py<PyAny>>,
    /// HTTP status the exception declares on its class via `status_code`
    /// (`cello.exceptions.*` sets it). `None` for exceptions that do not, which
    /// stay internal errors — see [`AppError::status_code`] and
    /// [`AppError::to_problem_details`].
    pub status: Option<u16>,
}

impl Clone for PythonExceptionInfo {
    fn clone(&self) -> Self {
        Self {
            exception_type: self.exception_type.clone(),
            message: self.message.clone(),
            traceback: self.traceback.clone(),
            file: self.file.clone(),
            line: self.line,
            status: self.status,
            // `Py<T>` is not `Clone` in pyo3 0.29, so the ref is cloned under the GIL.
            exception: self
                .exception
                .as_ref()
                .map(|e| Python::attach(|py| e.clone_ref(py))),
        }
    }
}

impl PythonExceptionInfo {
    /// Create from a PyErr.
    pub fn from_pyerr(py: Python<'_>, err: &PyErr) -> Self {
        let exception_type = err
            .get_type(py)
            .name()
            .map(|n| n.to_string())
            .unwrap_or_else(|_| "Unknown".to_string());

        let message = err.to_string();

        let traceback = err.traceback(py).and_then(|tb| tb.format().ok());

        // `cello.exceptions.*` declares the HTTP status on the exception class
        // (`NotFoundError.status_code == 404`). Read it here, where the GIL is
        // held. Only a plausible status is accepted, and anything unreadable
        // leaves the exception an internal error.
        let status = err
            .value(py)
            .getattr(pyo3::intern!(py, "status_code"))
            .ok()
            .and_then(|value| value.extract::<u16>().ok())
            .filter(|code| (100..=599).contains(code));

        Self {
            exception_type,
            message,
            traceback,
            file: None,
            line: None,
            exception: Some(err.value(py).clone().into_any().unbind()),
            status,
        }
    }

    /// The exception's own message, without the `TypeName: ` prefix that
    /// [`Self::message`] carries (pyo3 renders a `PyErr` that way).
    pub fn bare_message(&self) -> &str {
        let short = self
            .exception_type
            .rsplit('.')
            .next()
            .unwrap_or(&self.exception_type);
        self.message
            .strip_prefix(short)
            .and_then(|rest| rest.strip_prefix(": "))
            .unwrap_or(&self.message)
    }
}

impl std::fmt::Display for PythonExceptionInfo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.exception_type, self.message)
    }
}

/// Error handler function type.
pub type ErrorHandlerFn = Arc<dyn Fn(&AppError, &Request) -> Response + Send + Sync>;

/// Python error handler wrapper.
pub struct PyErrorHandler {
    handler: Py<PyAny>,
}

impl PyErrorHandler {
    pub fn new(handler: Py<PyAny>) -> Self {
        Self { handler }
    }

    pub fn handle(&self, error: &AppError, request: &Request) -> Response {
        Python::attach(|py| {
            // Create error info dict for Python
            let error_dict = pyo3::types::PyDict::new(py);
            let _ = error_dict.set_item("message", error.to_string());
            let _ = error_dict.set_item("status", error.status_code());
            let _ = error_dict.set_item("type", format!("{:?}", std::mem::discriminant(error)));

            if let AppError::PythonException(info) = error {
                let _ = error_dict.set_item("exception_type", &info.exception_type);
                if let Some(tb) = &info.traceback {
                    let _ = error_dict.set_item("traceback", tb);
                }
            }

            let exception = match error {
                AppError::PythonException(info) => info
                    .exception
                    .as_ref()
                    .map(|value| value.clone_ref(py))
                    .unwrap_or_else(|| error_dict.clone().into_any().unbind()),
                _ => error_dict.clone().into_any().unbind(),
            };

            match self.handler.call1(py, (request.clone(), exception)) {
                Ok(result) => {
                    // Check if result is a Response
                    if let Ok(response) = result.extract::<Response>(py) {
                        return response;
                    }
                    // Check if result is a ProblemDetails
                    if let Ok(problem) = result.extract::<ProblemDetails>(py) {
                        return problem.to_response();
                    }
                    // Fallback: try to convert result to string
                    if let Ok(s) = result.extract::<String>(py) {
                        let mut resp = Response::new(error.status_code());
                        resp.set_body(s.into_bytes());
                        resp.set_header("Content-Type", "application/json");
                        return resp;
                    }
                    // Accept any JSON-serializable value as a response body.
                    if let Ok(json_value) = python_to_json(py, result.bind(py)) {
                        return Response::from_json_value(json_value, error.status_code());
                    }
                    // Fallback to default error response
                    error.to_response()
                }
                Err(_) => error.to_response(),
            }
        })
    }
}

/// Error handler registry.
///
/// Manages error handlers at different levels:
/// - Global handlers
/// - Status-code specific handlers
/// - Exception-type specific handlers
/// - Blueprint-scoped handlers
pub struct ErrorHandlerRegistry {
    /// Global error handler.
    global: RwLock<Option<Arc<PyErrorHandler>>>,
    /// Per-status code handlers.
    status_handlers: RwLock<HashMap<u16, Arc<PyErrorHandler>>>,
    /// Per-exception type handlers (by type name string).
    exception_handlers: RwLock<HashMap<String, Arc<PyErrorHandler>>>,
    /// Blueprint-scoped handler registries.
    blueprint_handlers: RwLock<HashMap<String, Arc<ErrorHandlerRegistry>>>,
    /// Debug mode flag (shows tracebacks).
    debug_mode: RwLock<bool>,
}

/// Map an `AppError` variant to the Python exception class name users register
/// with `@app.exception_handler(...)`. Returns `None` for variants that don't
/// have a dedicated Python exception class.
fn app_error_to_exception_name(error: &AppError) -> Option<&'static str> {
    match error {
        AppError::NotFound(_) => Some("NotFoundError"),
        AppError::Internal(_) | AppError::Handler(_) => Some("InternalServerError"),
        AppError::Unauthorized(_) => Some("AuthenticationError"),
        AppError::Forbidden(_) => Some("AuthorizationError"),
        AppError::BadRequest(_) => Some("BadRequestError"),
        AppError::Conflict(_) => Some("ConflictError"),
        AppError::Validation { .. } => Some("ValidationError"),
        AppError::RateLimited { .. } => Some("RateLimitError"),
        AppError::Timeout(_) => Some("TimeoutError"),
        AppError::PythonException(_) | AppError::Custom { .. } => None,
    }
}

impl ErrorHandlerRegistry {
    /// Create a new error handler registry.
    pub fn new() -> Self {
        Self {
            global: RwLock::new(None),
            status_handlers: RwLock::new(HashMap::new()),
            exception_handlers: RwLock::new(HashMap::new()),
            blueprint_handlers: RwLock::new(HashMap::new()),
            debug_mode: RwLock::new(false),
        }
    }

    /// Set debug mode.
    pub fn set_debug(&self, debug: bool) {
        *self.debug_mode.write() = debug;
    }

    /// Check if debug mode is enabled.
    pub fn is_debug(&self) -> bool {
        *self.debug_mode.read()
    }

    /// Register a global error handler.
    pub fn set_global_handler(&self, handler: Py<PyAny>) {
        *self.global.write() = Some(Arc::new(PyErrorHandler::new(handler)));
    }

    /// Register a handler for a specific status code.
    pub fn set_status_handler(&self, status: u16, handler: Py<PyAny>) {
        self.status_handlers
            .write()
            .insert(status, Arc::new(PyErrorHandler::new(handler)));
    }

    /// Register a handler for a specific exception type.
    pub fn set_exception_handler(&self, exception_type: impl Into<String>, handler: Py<PyAny>) {
        self.exception_handlers.write().insert(
            exception_type.into(),
            Arc::new(PyErrorHandler::new(handler)),
        );
    }

    /// Register a blueprint-scoped handler registry.
    pub fn set_blueprint_handlers(
        &self,
        blueprint: impl Into<String>,
        registry: Arc<ErrorHandlerRegistry>,
    ) {
        self.blueprint_handlers
            .write()
            .insert(blueprint.into(), registry);
    }

    /// Handle an error, finding the most specific handler.
    pub fn handle(&self, error: &AppError, request: &Request, blueprint: Option<&str>) -> Response {
        // Try blueprint-scoped handlers first
        if let Some(bp_name) = blueprint {
            if let Some(bp_registry) = self.blueprint_handlers.read().get(bp_name) {
                let response = bp_registry.handle(error, request, None);
                // Only use blueprint handler if it produced a non-default response
                if response.status != 500 || bp_registry.has_handlers() {
                    return response;
                }
            }
        }

        // Try exception-type handler for Python exceptions.
        //
        // Python 3.12 exposes the fully-qualified type name (e.g.
        // `cello.exceptions.NotFoundError`) via `PyType::name()`, so match both
        // the full name and its last component (the plain class name that users
        // pass to `register_exception_handler` / `@app.exception_handler`).
        if let AppError::PythonException(info) = error {
            let exception_handlers = self.exception_handlers.read();
            if let Some(handler) = exception_handlers.get(&info.exception_type) {
                return handler.handle(error, request);
            }
            if let Some(short) = info.exception_type.rsplit('.').next() {
                if let Some(handler) = exception_handlers.get(short) {
                    return handler.handle(error, request);
                }
            }
        }

        // Try the AppError variant's Python exception class name (e.g. a route
        // miss produces `AppError::NotFound`, which users hook with
        // `@app.exception_handler(NotFoundError)`).
        if let Some(exception_name) = app_error_to_exception_name(error) {
            if let Some(handler) = self.exception_handlers.read().get(exception_name) {
                return handler.handle(error, request);
            }
        }

        // An unhandled exception from a handler is a server error; route it to
        // the `InternalServerError` handler when registered so
        // `@app.exception_handler(InternalServerError)` catches any 500.
        if let AppError::PythonException(_) = error {
            if let Some(handler) = self.exception_handlers.read().get("InternalServerError") {
                return handler.handle(error, request);
            }
        }

        // Try status code handler
        let status = error.status_code();
        if let Some(handler) = self.status_handlers.read().get(&status) {
            return handler.handle(error, request);
        }

        // Try global handler
        if let Some(handler) = self.global.read().as_ref() {
            return handler.handle(error, request);
        }

        // Default: convert error to response
        let mut response = error.to_response();

        // In debug mode, include traceback for Python exceptions
        if self.is_debug() {
            if let AppError::PythonException(info) = error {
                if let Some(tb) = &info.traceback {
                    let mut problem = error.to_problem_details();
                    problem
                        .extensions
                        .insert("traceback".to_string(), serde_json::json!(tb));
                    response = problem.to_response();
                }
            }
        }

        // Outside debug mode the details of an unhandled error never reach the
        // client: the message can carry the Python exception text (and, in debug
        // mode, its traceback) or a database error with constraint names and row
        // values. The response keeps only the Problem Details skeleton and the
        // real error goes to the server log. This runs last, after every branch
        // above, so nothing can put the detail back. Reaching here means no
        // user-registered handler matched, so nothing a user wrote is overridden.
        if !expose_error_details() && is_internal_detail(error) {
            eprintln!(
                "[cello] unhandled {} {method} {path}: {error}",
                error.status_code(),
                method = request.method,
                path = request.path,
            );
            response = error.to_redacted_response();
        }

        response
    }

    /// Check if any handlers are registered.
    pub fn has_handlers(&self) -> bool {
        self.global.read().is_some()
            || !self.status_handlers.read().is_empty()
            || !self.exception_handlers.read().is_empty()
    }

    /// Whether any registered handler would match the given error.
    ///
    /// Mirrors the dispatch order in [`Self::handle`] (excluding
    /// blueprint-scoped handlers, which callers pass explicitly).
    pub fn has_handler_for(&self, error: &AppError) -> bool {
        if let AppError::PythonException(info) = error {
            let handlers = self.exception_handlers.read();
            if handlers.contains_key(&info.exception_type)
                || info
                    .exception_type
                    .rsplit('.')
                    .next()
                    .map_or(false, |short| handlers.contains_key(short))
            {
                return true;
            }
        }
        if let Some(name) = app_error_to_exception_name(error) {
            if self.exception_handlers.read().contains_key(name) {
                return true;
            }
        }
        if self
            .status_handlers
            .read()
            .contains_key(&error.status_code())
        {
            return true;
        }
        self.global.read().is_some()
    }
}

impl Default for ErrorHandlerRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl Clone for ErrorHandlerRegistry {
    fn clone(&self) -> Self {
        Self {
            global: RwLock::new(self.global.read().clone()),
            status_handlers: RwLock::new(self.status_handlers.read().clone()),
            exception_handlers: RwLock::new(self.exception_handlers.read().clone()),
            blueprint_handlers: RwLock::new(self.blueprint_handlers.read().clone()),
            debug_mode: RwLock::new(*self.debug_mode.read()),
        }
    }
}

/// Python-exposed error handler registry.
#[pyclass]
pub struct PyErrorHandlerRegistry {
    inner: Arc<ErrorHandlerRegistry>,
}

#[pymethods]
impl PyErrorHandlerRegistry {
    #[new]
    pub fn new() -> Self {
        Self {
            inner: Arc::new(ErrorHandlerRegistry::new()),
        }
    }

    /// Set debug mode.
    pub fn set_debug(&self, debug: bool) {
        self.inner.set_debug(debug);
    }

    /// Register a global error handler.
    pub fn error_handler(&self, handler: Py<PyAny>) {
        self.inner.set_global_handler(handler);
    }

    /// Register a status code handler.
    pub fn status_handler(&self, status: u16, handler: Py<PyAny>) {
        self.inner.set_status_handler(status, handler);
    }

    /// Register an exception type handler.
    pub fn exception_handler(&self, exception_type: String, handler: Py<PyAny>) {
        self.inner.set_exception_handler(exception_type, handler);
    }
}

impl Default for PyErrorHandlerRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_app_error_status_codes() {
        assert_eq!(AppError::NotFound("test".into()).status_code(), 404);
        assert_eq!(AppError::Unauthorized("test".into()).status_code(), 401);
        assert_eq!(AppError::Forbidden("test".into()).status_code(), 403);
        assert_eq!(AppError::BadRequest("test".into()).status_code(), 400);
        assert_eq!(
            AppError::RateLimited {
                retry_after: Some(60),
                message: "test".into()
            }
            .status_code(),
            429
        );
    }

    #[test]
    fn test_problem_details_serialization() {
        let problem = ProblemDetails {
            type_uri: "/errors/test".to_string(),
            title: "Test Error".to_string(),
            status: 400,
            detail: Some("Something went wrong".to_string()),
            instance: None,
            extensions: HashMap::new(),
        };

        let json = serde_json::to_string(&problem).unwrap();
        assert!(json.contains("\"type\":\"/errors/test\""));
        assert!(json.contains("\"title\":\"Test Error\""));
        assert!(json.contains("\"status\":400"));
    }

    #[test]
    fn test_field_error() {
        let error = FieldError::new("email", "Invalid email format", "invalid_format");
        assert_eq!(error.field, "email");
        assert_eq!(error.message, "Invalid email format");
        assert_eq!(error.code, "invalid_format");
    }

    /// Only errors whose text comes from an exception or the database are
    /// redacted; a 4xx message is written for the caller and stays.
    #[test]
    fn test_only_internal_details_are_redacted() {
        let python_error = AppError::PythonException(PythonExceptionInfo {
            exception_type: "ValueError".into(),
            message: "secret".into(),
            traceback: None,
            file: None,
            line: None,
            exception: None,
            status: None,
        });
        assert!(is_internal_detail(&python_error));
        assert!(is_internal_detail(&AppError::Internal("secret".into())));
        assert!(is_internal_detail(&AppError::Custom {
            status: 503,
            message: "secret".into(),
            type_uri: None,
        }));

        assert!(!is_internal_detail(&AppError::NotFound(
            "no such user".into()
        )));
        assert!(!is_internal_detail(&AppError::Timeout(
            "took too long".into()
        )));
        assert!(!is_internal_detail(&AppError::Custom {
            status: 409,
            message: "conflict".into(),
            type_uri: None,
        }));
    }

    fn python_exception(
        exception_type: &str,
        message: &str,
        status: Option<u16>,
    ) -> PythonExceptionInfo {
        PythonExceptionInfo {
            exception_type: exception_type.into(),
            message: message.into(),
            traceback: None,
            file: None,
            line: None,
            exception: None,
            status,
        }
    }

    /// A `cello.exceptions` subclass declares its status, and a raised one has to
    /// answer with it — the 1:1 mapping the classes document.
    #[test]
    fn test_raised_exception_keeps_its_declared_status() {
        let info = python_exception(
            "NotFoundError",
            "NotFoundError: user 42 missing",
            Some(404),
        );
        let not_found = AppError::PythonException(info);
        assert_eq!(not_found.status_code(), 404);

        // The body is the RFC 7807 document a route miss produces, carrying the
        // exception's own text without its type name.
        let details = not_found.to_problem_details();
        assert_eq!(details.type_uri, "/errors/not-found");
        assert_eq!(details.title, "Not Found");
        assert_eq!(details.detail.as_deref(), Some("user 42 missing"));
        // A caller-facing 404 is never redacted.
        assert!(!is_internal_detail(&not_found));

        let validation = python_exception("ValidationError", "ValidationError: bad", Some(422));
        assert_eq!(AppError::PythonException(validation).status_code(), 422);

        // No declared status: still an internal error, still redacted, and the
        // detail keeps the exception type, which is diagnostic there.
        let info = python_exception("ValueError", "ValueError: boom", None);
        let plain = AppError::PythonException(info);
        assert_eq!(plain.status_code(), 500);
        assert_eq!(
            plain.to_problem_details().detail.as_deref(),
            Some("ValueError: boom")
        );
        assert!(is_internal_detail(&plain));

        // A 5xx declared by the exception is internal too.
        let down = python_exception("InternalServerError", "db is down", Some(500));
        let down = AppError::PythonException(down);
        assert_eq!(down.status_code(), 500);
        assert!(is_internal_detail(&down));
    }

    #[test]
    fn test_bare_message_strips_the_exception_type() {
        // pyo3 renders the type name in the message; a mapped 4xx body should not
        // repeat it, and a name that does not prefix the message is left alone.
        let qualified = python_exception(
            "cello.exceptions.NotFoundError",
            "NotFoundError: user 42 missing",
            Some(404),
        );
        assert_eq!(qualified.bare_message(), "user 42 missing");

        let bare = python_exception("ValueError", "boom", None);
        assert_eq!(bare.bare_message(), "boom");
    }

    #[test]
    fn test_redacted_response_drops_the_detail_and_extensions() {
        let error = AppError::PythonException(PythonExceptionInfo {
            exception_type: "psycopg.errors.UniqueViolation".into(),
            message: "duplicate key value violates unique constraint users_pkey".into(),
            traceback: Some("traceback: users_pkey".into()),
            file: None,
            line: None,
            exception: None,
            status: None,
        });

        let full = String::from_utf8(error.to_response().body_buffer().to_vec()).unwrap();
        assert!(full.contains("users_pkey"));
        assert!(full.contains("exception_type"));

        let redacted =
            String::from_utf8(error.to_redacted_response().body_buffer().to_vec()).unwrap();
        assert!(!redacted.contains("users_pkey"));
        assert!(!redacted.contains("exception_type"));
        // The Problem Details skeleton survives, so clients still get RFC 7807.
        assert!(redacted.contains("\"status\":500"));
        assert!(redacted.contains("Internal Server Error"));
        assert_eq!(error.to_redacted_response().status, 500);
    }
}
