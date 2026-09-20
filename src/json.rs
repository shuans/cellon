//! SIMD-accelerated JSON handling.
//!
//! Uses simd-json for fast JSON parsing and serialization,
//! with serde_json as fallback.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyList, PyLong, PyString, PyTuple};

/// Parse JSON string to serde_json::Value.
/// Uses SIMD acceleration on x86_64 and aarch64 (NEON), falls back to serde_json
/// on other architectures for maximum cross-platform compatibility.
#[inline]
pub fn parse_json(input: &str) -> Result<serde_json::Value, String> {
    #[cfg(any(target_arch = "x86", target_arch = "x86_64", target_arch = "aarch64"))]
    {
        // simd-json requires mutable input, so we need to copy
        let mut input_bytes = input.as_bytes().to_vec();
        simd_json::serde::from_slice(&mut input_bytes).map_err(|e| format!("JSON parse error: {e}"))
    }
    #[cfg(not(any(target_arch = "x86", target_arch = "x86_64", target_arch = "aarch64")))]
    {
        serde_json::from_str(input).map_err(|e| format!("JSON parse error: {e}"))
    }
}

/// Parse JSON bytes to serde_json::Value.
/// Uses SIMD acceleration where available, serde_json fallback otherwise.
#[inline]
pub fn parse_json_bytes(input: &mut [u8]) -> Result<serde_json::Value, String> {
    #[cfg(any(target_arch = "x86", target_arch = "x86_64", target_arch = "aarch64"))]
    {
        simd_json::serde::from_slice(input).map_err(|e| format!("JSON parse error: {e}"))
    }
    #[cfg(not(any(target_arch = "x86", target_arch = "x86_64", target_arch = "aarch64")))]
    {
        serde_json::from_slice(input).map_err(|e| format!("JSON parse error: {e}"))
    }
}

/// Serialize a serde_json::Value to JSON string.
#[inline]
pub fn serialize_json(value: &serde_json::Value) -> Result<String, String> {
    serde_json::to_string(value).map_err(|e| format!("JSON serialize error: {e}"))
}

/// Serialize a serde_json::Value to JSON bytes.
#[inline]
pub fn serialize_json_bytes(value: &serde_json::Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|e| format!("JSON serialize error: {e}"))
}

/// Serialize a serde_json::Value to pretty JSON string.
#[inline]
pub fn serialize_json_pretty(value: &serde_json::Value) -> Result<String, String> {
    serde_json::to_string_pretty(value).map_err(|e| format!("JSON serialize error: {e}"))
}

/// Convert a Python object to serde_json::Value.
#[inline]
pub fn python_to_json<'py>(py: Python<'py>, obj: &Bound<'py, PyAny>) -> Result<serde_json::Value, String> {
    // Handle None
    if obj.is_none() {
        return Ok(serde_json::Value::Null);
    }

    // PERF: Dispatch on the Python type with `downcast` (a cheap isinstance check)
    // rather than probing each scalar with `extract::<T>()`. A failed `extract`
    // builds and clears a Python exception, so the old try-each-type chain paid
    // that cost repeatedly per value — worst for strings, which failed the
    // bool/int/float probes first.

    // Handle bool (must come before int check since bool is subclass of int in Python)
    if let Ok(b) = obj.downcast::<PyBool>() {
        return Ok(serde_json::Value::Bool(b.is_true()));
    }

    // Handle int. Try i64 first, then u64 for large unsigned values (e.g. 64-bit IDs
    // and values in (i64::MAX, u64::MAX]) so they are not silently downgraded to f64
    // and corrupted. Integers beyond u64 still fall through to the float path.
    if let Ok(long) = obj.downcast::<PyLong>() {
        if let Ok(i) = long.extract::<i64>() {
            return Ok(serde_json::Value::Number(i.into()));
        }
        if let Ok(u) = long.extract::<u64>() {
            return Ok(serde_json::Value::Number(u.into()));
        }
        if let Ok(f) = long.extract::<f64>() {
            return Ok(serde_json::json!(f));
        }
    }

    // Handle float
    if let Ok(float) = obj.downcast::<PyFloat>() {
        if let Ok(f) = float.extract::<f64>() {
            return Ok(serde_json::json!(f));
        }
    }

    // Handle string
    if let Ok(s) = obj.downcast::<PyString>() {
        if let Ok(s) = s.to_str() {
            return Ok(serde_json::Value::String(s.to_owned()));
        }
    }

    // Handle list
    if let Ok(list) = obj.downcast::<PyList>() {
        // PERF: Pre-allocate vec with known capacity
        let mut items = Vec::with_capacity(list.len());
        for item in list.iter() {
            items.push(python_to_json(py, &item)?);
        }
        return Ok(serde_json::Value::Array(items));
    }

    // Handle dict
    if let Ok(dict) = obj.downcast::<PyDict>() {
        // PERF: Pre-allocate map with known capacity
        let mut map = serde_json::Map::with_capacity(dict.len());
        for (key, value) in dict.iter() {
            let key_str = key
                .extract::<String>()
                .map_err(|_| "Dict keys must be strings".to_string())?;
            let value_json = python_to_json(py, &value)?;
            map.insert(key_str, value_json);
        }
        return Ok(serde_json::Value::Object(map));
    }

    // Handle tuple
    if let Ok(tuple) = obj.downcast::<PyTuple>() {
        // PERF: Pre-allocate vec with known capacity
        let mut items = Vec::with_capacity(tuple.len());
        for item in tuple.iter() {
            items.push(python_to_json(py, &item)?);
        }
        return Ok(serde_json::Value::Array(items));
    }

    // Handle Response object - check by class name
    let class_name = obj.get_type().name().unwrap_or("");
    if class_name == "Response" {
        let mut response_obj = serde_json::Map::new();
        response_obj.insert(
            "__cello_response__".to_string(),
            serde_json::Value::Bool(true),
        );

        if let Ok(status) = obj.getattr("status") {
            if let Ok(s) = status.extract::<u16>() {
                response_obj.insert("status".to_string(), serde_json::Value::Number(s.into()));
            }
        }

        if let Ok(headers) = obj.getattr("headers") {
            if let Ok(dict) = headers.downcast::<PyDict>() {
                let mut headers_map = serde_json::Map::new();
                for (key, value) in dict.iter() {
                    if let (Ok(k), Ok(v)) = (key.extract::<String>(), value.extract::<String>()) {
                        headers_map.insert(k, serde_json::Value::String(v));
                    }
                }
                response_obj.insert(
                    "headers".to_string(),
                    serde_json::Value::Object(headers_map),
                );
            }
        }

        // Get body - use body() which is Python-accessible
        if let Ok(body_bytes) = obj.call_method0("body") {
            if let Ok(bytes) = body_bytes.extract::<Vec<u8>>() {
                if let Ok(body_str) = String::from_utf8(bytes) {
                    response_obj.insert("body".to_string(), serde_json::Value::String(body_str));
                }
            }
        }

        return Ok(serde_json::Value::Object(response_obj));
    }

    // Fallback for objects that are not exact builtins but still convert through
    // the numeric/str protocols (e.g. numpy scalars, Decimal, IntEnum).
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(serde_json::Value::Number(i.into()));
    }
    if let Ok(u) = obj.extract::<u64>() {
        return Ok(serde_json::Value::Number(u.into()));
    }
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(serde_json::json!(f));
    }
    if let Ok(s) = obj.extract::<String>() {
        return Ok(serde_json::Value::String(s));
    }

    Err(format!("Cannot convert Python object to JSON: {obj:?}"))
}

/// PERF: Convert a Python object directly to JSON bytes, skipping intermediate serde_json::Value.
/// Returns Ok(Some(bytes)) for normal dicts/lists/primitives,
/// Returns Ok(None) for Response objects (caller must fall back to python_to_json).
#[inline]
pub fn python_to_json_bytes_direct<'py>(
    py: Python<'py>,
    obj: &Bound<'py, PyAny>,
) -> Result<Option<Vec<u8>>, String> {
    // PERF: Type-check (a cheap isinstance) instead of probing with
    // `extract::<T>()`, which allocates and clears a Python exception on every
    // failed attempt. dict/list first (most handlers return a dict), then scalars.
    let is_container = obj.downcast::<PyDict>().is_ok() || obj.downcast::<PyList>().is_ok();
    let is_scalar = obj.is_none()
        || obj.downcast::<PyBool>().is_ok()
        || obj.downcast::<PyLong>().is_ok()
        || obj.downcast::<PyFloat>().is_ok()
        || obj.downcast::<PyString>().is_ok();
    if is_container || is_scalar {
        let mut buf = Vec::with_capacity(128);
        write_json_value(py, obj, &mut buf)?;
        return Ok(Some(buf));
    }

    // Not a simple type - likely a Response object, fall back to Value path
    Ok(None)
}

/// Write a Python object as JSON directly to a byte buffer.
fn write_json_value<'py>(py: Python<'py>, obj: &Bound<'py, PyAny>, buf: &mut Vec<u8>) -> Result<(), String> {
    use std::io::Write;

    // Handle None
    if obj.is_none() {
        buf.extend_from_slice(b"null");
        return Ok(());
    }

    // PERF: Dispatch on the Python type with `downcast` (cheap isinstance) rather
    // than probing each scalar with `extract::<T>()`, which builds and clears a
    // Python exception on every failed probe.

    // Handle bool (must come before int check since bool is subclass of int in Python)
    if let Ok(b) = obj.downcast::<PyBool>() {
        buf.extend_from_slice(if b.is_true() { b"true" } else { b"false" });
        return Ok(());
    }

    // Handle int. Try i64, then u64 for large unsigned values, so 64-bit IDs and
    // values in (i64::MAX, u64::MAX] are written exactly instead of being coerced to
    // an imprecise float. Integers beyond u64 still fall through to the float path.
    if let Ok(long) = obj.downcast::<PyLong>() {
        if let Ok(i) = long.extract::<i64>() {
            write!(buf, "{i}").map_err(|e| e.to_string())?;
            return Ok(());
        }
        if let Ok(u) = long.extract::<u64>() {
            write!(buf, "{u}").map_err(|e| e.to_string())?;
            return Ok(());
        }
        if let Ok(f) = long.extract::<f64>() {
            write_json_float(f, buf);
            return Ok(());
        }
    }

    // Handle float
    if let Ok(float) = obj.downcast::<PyFloat>() {
        if let Ok(f) = float.extract::<f64>() {
            write_json_float(f, buf);
            return Ok(());
        }
    }

    // Handle string - need to JSON-escape
    if let Ok(s) = obj.downcast::<PyString>() {
        if let Ok(s) = s.to_str() {
            write_json_string(s, buf);
            return Ok(());
        }
    }

    // Handle list
    if let Ok(list) = obj.downcast::<PyList>() {
        buf.push(b'[');
        for (i, item) in list.iter().enumerate() {
            if i > 0 {
                buf.push(b',');
            }
            write_json_value(py, &item, buf)?;
        }
        buf.push(b']');
        return Ok(());
    }

    // Handle dict
    if let Ok(dict) = obj.downcast::<PyDict>() {
        buf.push(b'{');
        let mut first = true;
        for (key, value) in dict.iter() {
            let key_str = key
                .extract::<String>()
                .map_err(|_| "Dict keys must be strings".to_string())?;
            if !first {
                buf.push(b',');
            }
            first = false;
            write_json_string(&key_str, buf);
            buf.push(b':');
            write_json_value(py, &value, buf)?;
        }
        buf.push(b'}');
        return Ok(());
    }

    // Handle tuple
    if let Ok(tuple) = obj.downcast::<PyTuple>() {
        buf.push(b'[');
        for (i, item) in tuple.iter().enumerate() {
            if i > 0 {
                buf.push(b',');
            }
            write_json_value(py, &item, buf)?;
        }
        buf.push(b']');
        return Ok(());
    }

    // Fallback for objects that are not exact builtins but still convert through
    // the numeric/str protocols (e.g. numpy scalars, Decimal, IntEnum).
    if let Ok(i) = obj.extract::<i64>() {
        write!(buf, "{i}").map_err(|e| e.to_string())?;
        return Ok(());
    }
    if let Ok(u) = obj.extract::<u64>() {
        write!(buf, "{u}").map_err(|e| e.to_string())?;
        return Ok(());
    }
    if let Ok(f) = obj.extract::<f64>() {
        write_json_float(f, buf);
        return Ok(());
    }
    if let Ok(s) = obj.extract::<String>() {
        write_json_string(&s, buf);
        return Ok(());
    }

    Err(format!("Cannot convert Python object to JSON: {obj:?}"))
}

/// Write a JSON number for a float, mapping non-finite values to `null` (which
/// is what `serde_json` does and what the previous inline code did).
#[inline]
fn write_json_float(f: f64, buf: &mut Vec<u8>) {
    use std::io::Write;
    if f.is_finite() {
        let _ = write!(buf, "{f}");
    } else {
        buf.extend_from_slice(b"null");
    }
}

/// Write a JSON-escaped string to the buffer.
#[inline]
fn write_json_string(s: &str, buf: &mut Vec<u8>) {
    buf.push(b'"');
    let bytes = s.as_bytes();
    // PERF: most strings contain nothing that needs escaping — copy them in one
    // shot rather than byte-by-byte.
    if !bytes.iter().any(|&b| b < 0x20 || b == b'"' || b == b'\\') {
        buf.extend_from_slice(bytes);
    } else {
        for &byte in bytes {
            match byte {
                b'"' => buf.extend_from_slice(b"\\\""),
                b'\\' => buf.extend_from_slice(b"\\\\"),
                b'\n' => buf.extend_from_slice(b"\\n"),
                b'\r' => buf.extend_from_slice(b"\\r"),
                b'\t' => buf.extend_from_slice(b"\\t"),
                b if b < 0x20 => {
                    // Control characters: \u00XX
                    buf.extend_from_slice(b"\\u00");
                    let high = b >> 4;
                    let low = b & 0x0f;
                    buf.push(if high < 10 {
                        b'0' + high
                    } else {
                        b'a' + high - 10
                    });
                    buf.push(if low < 10 {
                        b'0' + low
                    } else {
                        b'a' + low - 10
                    });
                }
                _ => buf.push(byte),
            }
        }
    }
    buf.push(b'"');
}

/// Convert a serde_json::Value to a Python object.
#[inline]
pub fn json_to_python(py: Python<'_>, value: &serde_json::Value) -> PyResult<PyObject> {
    match value {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => (*b).into_py_any(py),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_py_any(py)
            } else if let Some(f) = n.as_f64() {
                f.into_py_any(py)
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => s.as_str().into_py_any(py),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(json_to_python(py, item)?)?;
            }
            list.into_py_any(py)
        }
        serde_json::Value::Object(obj) => {
            let dict = PyDict::new(py);
            for (key, val) in obj {
                dict.set_item(key, json_to_python(py, val)?)?;
            }
            dict.into_py_any(py)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_json() {
        let result = parse_json(r#"{"name": "test", "value": 42}"#);
        assert!(result.is_ok());
        let value = result.unwrap();
        assert_eq!(value["name"], "test");
        assert_eq!(value["value"], 42);
    }

    #[test]
    fn test_parse_json_array() {
        let result = parse_json(r#"[1, 2, 3, "four"]"#);
        assert!(result.is_ok());
        let value = result.unwrap();
        assert!(value.is_array());
        assert_eq!(value[0], 1);
        assert_eq!(value[3], "four");
    }

    #[test]
    fn test_serialize_json() {
        let value = serde_json::json!({
            "message": "hello",
            "count": 10
        });
        let result = serialize_json(&value);
        assert!(result.is_ok());
        let json_str = result.unwrap();
        assert!(json_str.contains("hello"));
        assert!(json_str.contains("10"));
    }
}
