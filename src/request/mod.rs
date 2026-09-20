//! HTTP Request types for Cello.
//!
//! This module provides:
//! - HTTP Request wrapper with typed parameters
//! - Lazy body parsing (JSON, form, multipart)
//! - Request context for middleware data
//! - Streaming multipart uploads

pub mod multipart_streaming;
pub mod parsing;

use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;

use crate::json::{json_to_python, parse_json, python_to_json};
use crate::multipart::parse_urlencoded;

pub use multipart_streaming::{MultipartPart, StreamingMultipart};
pub use parsing::{LazyBody, ParamError, TypedParams};

// ============================================================================
// HTTP Request
// ============================================================================

/// HTTP Request wrapper exposed to Python.
#[pyclass]
#[derive(Clone, Default)]
pub struct Request {
    /// HTTP method (GET, POST, etc.)
    #[pyo3(get)]
    pub method: String,

    /// Request path (e.g., "/users/123")
    #[pyo3(get)]
    pub path: String,

    /// Path parameters extracted from the route (e.g., {"id": "123"})
    ///
    /// PERF: shared via `Arc` (read-only after construction) so cloning a
    /// request is O(1) instead of copying the map.
    pub params: Arc<HashMap<String, String>>,

    /// Query string parameters
    pub query_params: Arc<HashMap<String, String>>,

    /// Request headers
    pub headers: Arc<HashMap<String, String>>,

    /// Request body as bytes
    pub body: Vec<u8>,

    /// Content type
    content_type: Option<String>,

    /// Request context for middleware data sharing (internal)
    pub context: HashMap<String, serde_json::Value>,

    /// Lazy body cache (internal)
    lazy_cache: LazyCache,

    /// Python-level Redis client injected when app.enable_redis() is configured.
    /// Wrapped in Arc so Clone stays GIL-free (atomic refcount only).
    pub redis_client: Option<Arc<PyObject>>,

    /// Python-level Database pool injected when app.enable_database() is configured.
    /// Wrapped in Arc so Clone stays GIL-free (atomic refcount only).
    pub database_client: Option<Arc<PyObject>>,
}

/// Internal cache for lazy parsing results.
/// Uses RwLock for thread-safety to support async middleware.
#[derive(Clone, Default)]
pub struct LazyCache {
    json_parsed: std::sync::Arc<parking_lot::RwLock<Option<Result<serde_json::Value, String>>>>,
    form_parsed:
        std::sync::Arc<parking_lot::RwLock<Option<Result<HashMap<String, String>, String>>>>,
    text_parsed: std::sync::Arc<parking_lot::RwLock<Option<Result<String, String>>>>,
}

#[pymethods]
impl Request {
    /// Create a new Request (primarily for testing).
    #[new]
    #[pyo3(signature = (method, path, params=None, query=None, headers=None, body=None))]
    pub fn py_new(
        method: String,
        path: String,
        params: Option<HashMap<String, String>>,
        query: Option<HashMap<String, String>>,
        headers: Option<HashMap<String, String>>,
        body: Option<Vec<u8>>,
    ) -> Self {
        let headers_map = headers.unwrap_or_default();
        let content_type = headers_map.get("content-type").cloned();

        Request {
            method,
            path,
            params: Arc::new(params.unwrap_or_default()),
            query_params: Arc::new(query.unwrap_or_default()),
            headers: Arc::new(headers_map),
            body: body.unwrap_or_default(),
            content_type,
            context: HashMap::new(),
            lazy_cache: LazyCache::default(),
            redis_client: None,
            database_client: None,
        }
    }

    /// Get the path parameters dict.
    #[getter]
    pub fn params(&self) -> HashMap<String, String> {
        (*self.params).clone()
    }

    /// Get the query parameters dict (alias of `query`).
    #[getter]
    pub fn query_params(&self) -> HashMap<String, String> {
        (*self.query_params).clone()
    }

    /// Get the request headers dict.
    #[getter]
    pub fn headers(&self) -> HashMap<String, String> {
        (*self.headers).clone()
    }

    /// Get the query parameters dict.
    #[getter]
    pub fn query(&self) -> HashMap<String, String> {
        (*self.query_params).clone()
    }

    /// Get the request body as a string (cached).
    pub fn text(&self) -> PyResult<String> {
        let mut cache = self.lazy_cache.text_parsed.write();
        if let Some(ref result) = *cache {
            return result
                .clone()
                .map_err(pyo3::exceptions::PyValueError::new_err);
        }

        let result = String::from_utf8(self.body.clone()).map_err(|e| e.to_string());
        let return_value = result.clone();
        *cache = Some(result);

        return_value.map_err(pyo3::exceptions::PyValueError::new_err)
    }

    /// Get the request body as bytes.
    pub fn body(&self) -> Vec<u8> {
        self.body.clone()
    }

    /// Parse the request body as JSON using SIMD acceleration (cached).
    pub fn json(&self, py: Python<'_>) -> PyResult<PyObject> {
        let mut cache = self.lazy_cache.json_parsed.write();

        let value = if let Some(ref result) = *cache {
            result
                .clone()
                .map_err(pyo3::exceptions::PyValueError::new_err)?
        } else {
            let text = String::from_utf8(self.body.clone())
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

            let result = parse_json(&text);
            let value = result
                .clone()
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.clone()))?;
            *cache = Some(result);
            value
        };

        json_to_python(py, &value)
    }

    /// Parse the request body as form data (cached).
    pub fn form(&self) -> PyResult<HashMap<String, String>> {
        let mut cache = self.lazy_cache.form_parsed.write();

        if let Some(ref result) = *cache {
            return result
                .clone()
                .map_err(pyo3::exceptions::PyValueError::new_err);
        }

        let result = parse_urlencoded(&self.body);
        let return_value = result.clone();
        *cache = Some(result);

        return_value.map_err(pyo3::exceptions::PyValueError::new_err)
    }

    /// Get the content type.
    pub fn content_type(&self) -> Option<String> {
        self.content_type.clone()
    }

    /// Check if the request is JSON.
    #[inline]
    pub fn is_json(&self) -> bool {
        self.content_type
            .as_ref()
            .map(|ct| ct.contains("application/json"))
            .unwrap_or(false)
    }

    /// Check if the request is form data.
    #[inline]
    pub fn is_form(&self) -> bool {
        self.content_type
            .as_ref()
            .map(|ct| ct.contains("application/x-www-form-urlencoded"))
            .unwrap_or(false)
    }

    /// Check if the request is multipart.
    #[inline]
    pub fn is_multipart(&self) -> bool {
        self.content_type
            .as_ref()
            .map(|ct| ct.contains("multipart/form-data"))
            .unwrap_or(false)
    }

    /// Get a query parameter by name.
    #[pyo3(signature = (key, default=None))]
    pub fn get_query_param(&self, key: &str, default: Option<&str>) -> Option<String> {
        self.query_params
            .get(key)
            .cloned()
            .or_else(|| default.map(|s| s.to_string()))
    }

    /// Get a query parameter as integer.
    #[pyo3(signature = (key, default=None))]
    pub fn get_query_int(&self, key: &str, default: Option<i64>) -> PyResult<Option<i64>> {
        match self.query_params.get(key) {
            Some(value) => value.parse::<i64>().map(Some).map_err(|_| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Query parameter '{key}' is not a valid integer"
                ))
            }),
            None => Ok(default),
        }
    }

    /// Get a query parameter as float.
    #[pyo3(signature = (key, default=None))]
    pub fn get_query_float(&self, key: &str, default: Option<f64>) -> PyResult<Option<f64>> {
        match self.query_params.get(key) {
            Some(value) => value.parse::<f64>().map(Some).map_err(|_| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Query parameter '{key}' is not a valid float"
                ))
            }),
            None => Ok(default),
        }
    }

    /// Get a query parameter as boolean.
    #[pyo3(signature = (key, default=None))]
    pub fn get_query_bool(&self, key: &str, default: Option<bool>) -> Option<bool> {
        match self.query_params.get(key) {
            Some(value) => {
                let lower = value.to_lowercase();
                Some(lower == "true" || lower == "1" || lower == "yes" || lower == "on")
            }
            None => default,
        }
    }

    /// Get a header by name (case-insensitive).
    #[pyo3(signature = (key, default=None))]
    pub fn get_header(&self, key: &str, default: Option<&str>) -> Option<String> {
        // PERF: header names arrive lowercase from hyper and callers pass
        // lowercase keys, so the common case is a single O(1) lookup. The old
        // implementation lowercased *every* stored key on each lookup, which made
        // this O(n) with n allocations — and it is called repeatedly per request
        // (client_ip, user_agent, is_secure, accepts, middleware, …).
        if let Some(value) = self.headers.get(key) {
            return Some(value.clone());
        }
        if key.bytes().any(|b| b.is_ascii_uppercase()) {
            if let Some(value) = self.headers.get(&key.to_ascii_lowercase()) {
                return Some(value.clone());
            }
        }
        // Fallback: maps built with non-canonical casing (e.g. Request(...)
        // constructed from Python). Only reached on a miss.
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(key))
            .map(|(_, v)| v.clone())
            .or_else(|| default.map(|s| s.to_string()))
    }

    /// Get a path parameter by name.
    #[pyo3(signature = (key, default=None))]
    pub fn get_param(&self, key: &str, default: Option<&str>) -> Option<String> {
        self.params
            .get(key)
            .cloned()
            .or_else(|| default.map(|s| s.to_string()))
    }

    /// Get a path parameter as integer.
    #[pyo3(signature = (key, default=None))]
    pub fn get_param_int(&self, key: &str, default: Option<i64>) -> PyResult<Option<i64>> {
        match self.params.get(key) {
            Some(value) => value.parse::<i64>().map(Some).map_err(|_| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Path parameter '{key}' is not a valid integer"
                ))
            }),
            None => Ok(default),
        }
    }

    /// Get the client IP address (from X-Forwarded-For or X-Real-IP).
    pub fn client_ip(&self) -> Option<String> {
        // Check X-Forwarded-For first (get first IP in chain)
        if let Some(xff) = self.get_header("x-forwarded-for", None) {
            if let Some(ip) = xff.split(',').next() {
                return Some(ip.trim().to_string());
            }
        }
        // Fall back to X-Real-IP
        self.get_header("x-real-ip", None)
    }

    /// Get the User-Agent header.
    pub fn user_agent(&self) -> Option<String> {
        self.get_header("user-agent", None)
    }

    /// Check if the request accepts a specific content type.
    pub fn accepts(&self, content_type: &str) -> bool {
        self.get_header("accept", None)
            .map(|accept| accept.contains(content_type))
            .unwrap_or(false)
    }

    /// Check if the request is an AJAX/XHR request.
    pub fn is_xhr(&self) -> bool {
        self.get_header("x-requested-with", None)
            .map(|v| v.to_lowercase() == "xmlhttprequest")
            .unwrap_or(false)
    }

    /// Check if the request is secure (HTTPS).
    pub fn is_secure(&self) -> bool {
        // Check X-Forwarded-Proto header
        if let Some(proto) = self.get_header("x-forwarded-proto", None) {
            return proto.to_lowercase() == "https";
        }
        // Check scheme header
        self.get_header("x-forwarded-scheme", None)
            .map(|s| s.to_lowercase() == "https")
            .unwrap_or(false)
    }

    /// Get the request host.
    pub fn host(&self) -> Option<String> {
        self.get_header("x-forwarded-host", None)
            .or_else(|| self.get_header("host", None))
    }

    /// Get the request ID from context.
    pub fn request_id(&self) -> Option<String> {
        self.context
            .get("request_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
    }

    /// Get a context value by key.
    pub fn get_context(&self, py: Python<'_>, key: &str) -> PyResult<PyObject> {
        match self.context.get(key) {
            Some(value) => json_to_python(py, value),
            None => Ok(py.None()),
        }
    }

    /// Set a context value by key.
    pub fn set_context(&mut self, py: Python<'_>, key: String, value: PyObject) -> PyResult<()> {
        let json_value = python_to_json(py, value.as_ref(py))
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        self.context.insert(key, json_value);
        Ok(())
    }

    /// Get a context value as string by key.
    pub fn get_context_string(&self, key: &str) -> Option<String> {
        self.context.get(key).and_then(|v| match v {
            serde_json::Value::String(s) => Some(s.clone()),
            serde_json::Value::Number(n) => Some(n.to_string()),
            serde_json::Value::Bool(b) => Some(b.to_string()),
            _ => None,
        })
    }

    /// Access the Redis client configured via app.enable_redis().
    #[getter]
    pub fn redis(&self, py: Python<'_>) -> PyResult<PyObject> {
        self.redis_client
            .as_ref()
            .map(|arc| arc.clone_ref(py))
            .ok_or_else(|| {
                pyo3::exceptions::PyAttributeError::new_err(
                    "Redis not configured. Call app.enable_redis() before registering routes \
                     to use request.redis.",
                )
            })
    }

    /// Inject the Redis client into this request (called by the Python App wrapper).
    pub fn _inject_redis(&mut self, client: PyObject) {
        self.redis_client = Some(Arc::new(client));
    }

    /// Access the Database pool configured via app.enable_database().
    #[getter]
    pub fn database(&self, py: Python<'_>) -> PyResult<PyObject> {
        self.database_client
            .as_ref()
            .map(|arc| arc.clone_ref(py))
            .ok_or_else(|| {
                pyo3::exceptions::PyAttributeError::new_err(
                    "Database not configured. Call app.enable_database() before registering \
                     routes to use request.database.",
                )
            })
    }

    /// Alias for `database` (asyncpg users reach for `request.db`).
    #[getter]
    pub fn db(&self, py: Python<'_>) -> PyResult<PyObject> {
        self.database(py)
    }

    /// Inject the Database pool into this request (called by the Python App wrapper).
    pub fn _inject_database(&mut self, client: PyObject) {
        self.database_client = Some(Arc::new(client));
    }
}

impl Request {
    /// Create a new request (Rust API).
    pub fn new(method: &str, path: &str) -> Self {
        Request {
            method: method.to_string(),
            path: path.to_string(),
            params: Arc::new(HashMap::new()),
            query_params: Arc::new(HashMap::new()),
            headers: Arc::new(HashMap::new()),
            body: Vec::new(),
            content_type: None,
            context: HashMap::new(),
            lazy_cache: LazyCache::default(),
            redis_client: None,
            database_client: None,
        }
    }

    /// Create a request from HTTP components (internal use).
    #[inline]
    pub fn from_http(
        method: String,
        path: String,
        params: HashMap<String, String>,
        query: HashMap<String, String>,
        headers: HashMap<String, String>,
        body: Vec<u8>,
    ) -> Self {
        let content_type = headers.get("content-type").cloned();

        Request {
            method,
            path,
            params: Arc::new(params),
            query_params: Arc::new(query),
            headers: Arc::new(headers),
            body,
            content_type,
            context: HashMap::new(),
            lazy_cache: LazyCache::default(),
            redis_client: None,
            database_client: None,
        }
    }

    /// Create a lightweight clone without body bytes or lazy cache.
    /// PERF: Used for after-middleware which only needs method, path, headers, and context.
    /// Avoids cloning the potentially large body Vec<u8> and the Arc<RwLock> cache structures.
    /// The read-only maps are `Arc`-shared, so this is O(1) for them.
    #[inline]
    pub fn clone_without_body(&self) -> Self {
        Request {
            method: self.method.clone(),
            path: self.path.clone(),
            params: Arc::clone(&self.params),
            query_params: Arc::clone(&self.query_params),
            headers: Arc::clone(&self.headers),
            body: Vec::new(),
            content_type: self.content_type.clone(),
            context: self.context.clone(),
            lazy_cache: LazyCache::default(),
            redis_client: self.redis_client.clone(),
            database_client: self.database_client.clone(),
        }
    }

    /// Get the raw body bytes (internal use).
    #[inline]
    pub fn body_bytes(&self) -> &[u8] {
        &self.body
    }

    /// Get typed parameters helper.
    pub fn typed_params(&self) -> TypedParams {
        TypedParams::from_map(&self.params)
    }

    /// Get typed query parameters helper.
    pub fn typed_query(&self) -> TypedParams {
        TypedParams::from_map(&self.query_params)
    }

    /// Get lazy body parser.
    pub fn lazy_body(&self) -> LazyBody {
        LazyBody::new(&self.body)
    }

    /// Get the multipart boundary if this is a multipart request.
    pub fn multipart_boundary(&self) -> Option<String> {
        self.content_type.as_ref().and_then(|ct| {
            if ct.contains("multipart/form-data") {
                ct.split("boundary=")
                    .nth(1)
                    .map(|b| b.trim_matches('"').to_string())
            } else {
                None
            }
        })
    }

    /// Set a context value (Rust-only, internal use).
    #[inline]
    pub fn set_context_internal(&mut self, key: &str, value: serde_json::Value) {
        self.context.insert(key.to_string(), value);
    }

    /// Get a context value by key (Rust-only, internal use).
    #[inline]
    pub fn get_context_internal(&self, key: &str) -> Option<serde_json::Value> {
        self.context.get(key).cloned()
    }

    /// Get a context value as string (Rust-only, internal use).
    #[inline]
    pub fn get_context_str(&self, key: &str) -> Option<String> {
        self.context
            .get(key)
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_request_creation() {
        let request = Request::new("GET", "/users/123");
        assert_eq!(request.method, "GET");
        assert_eq!(request.path, "/users/123");
    }

    #[test]
    fn test_typed_params() {
        let mut params = HashMap::new();
        params.insert("id".to_string(), "123".to_string());
        params.insert("name".to_string(), "test".to_string());

        let typed = TypedParams::from_map(&params);
        assert_eq!(typed.get::<i64>("id").unwrap(), 123);
        assert_eq!(typed.get::<String>("name").unwrap(), "test");
    }

    #[test]
    fn test_query_params() {
        let mut query = HashMap::new();
        query.insert("page".to_string(), "2".to_string());
        query.insert("active".to_string(), "true".to_string());

        let mut request = Request::new("GET", "/items");
        request.query_params = std::sync::Arc::new(query);

        assert_eq!(request.get_query_param("page", None), Some("2".to_string()));
        assert_eq!(request.get_query_bool("active", None), Some(true));
    }

    #[test]
    fn test_context() {
        let mut request = Request::new("GET", "/test");
        request.set_context_internal("user_id", serde_json::json!(123));
        request.set_context_internal("role", serde_json::json!("admin"));

        assert_eq!(
            request.get_context_internal("user_id"),
            Some(serde_json::json!(123))
        );
        assert_eq!(request.get_context_str("role"), Some("admin".to_string()));
    }

    #[test]
    fn test_multipart_boundary() {
        let mut request = Request::new("POST", "/upload");
        request.content_type =
            Some("multipart/form-data; boundary=----WebKitFormBoundary123".to_string());

        assert_eq!(
            request.multipart_boundary(),
            Some("----WebKitFormBoundary123".to_string())
        );
    }
}
