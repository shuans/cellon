//! Database exception hierarchy and the mapping from driver / server errors.
//!
//! ```text
//! RuntimeError
//! └── DatabaseError                 sqlstate / detail / constraint / table / column
//!     ├── IntegrityError            SQLSTATE class 23
//!     │   ├── UniqueViolation       23505
//!     │   ├── NotNullViolation      23502
//!     │   ├── ForeignKeyViolation   23503
//!     │   └── CheckViolation        23514
//!     ├── DataError                 SQLSTATE class 22, and values that cannot be bound
//!     │   ├── ParameterTypeError    (also a TypeError)
//!     │   ├── ParameterRangeError   (also an OverflowError)
//!     │   └── ParameterValueError   (also a ValueError)
//!     ├── OperationalError          connection / pool failures, classes 08 40 53 57 58
//!     └── ProgrammingError          SQLSTATE class 42, wrong parameter count
//! ```
//!
//! `DatabaseError` subclasses `RuntimeError`, so code written against the 1.3.0
//! data layer (`except RuntimeError`) keeps working, and the parameter errors
//! also subclass the builtin they used to be raised as.
//!
//! The driver only ever renders a server error as the string `db error`, so the
//! SQLSTATE, `DETAIL`, constraint and table names are read off
//! `tokio_postgres::Error::as_db_error` and carried to Python as attributes:
//!
//! ```python
//! try:
//!     await request.database.execute("INSERT INTO users (email) VALUES ($1)", email)
//! except UniqueViolation as exc:
//!     return Response.json({"field": exc.constraint}, status=409)
//! ```
//!
//! Errors travel across the I/O thread as plain Rust data ([`PgError`]) and are
//! turned into Python exceptions where the GIL is held.

use std::error::Error as StdError;
use std::fmt;

use parking_lot::RwLock;
use pyo3::exceptions::{PyOverflowError, PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple, PyType};

pyo3::create_exception!(
    cello,
    DatabaseError,
    PyRuntimeError,
    "Base class for database errors. Subclasses RuntimeError."
);
pyo3::create_exception!(
    cello,
    IntegrityError,
    DatabaseError,
    "A constraint was violated (SQLSTATE class 23)."
);
pyo3::create_exception!(
    cello,
    UniqueViolation,
    IntegrityError,
    "Unique constraint violated (23505)."
);
pyo3::create_exception!(
    cello,
    NotNullViolation,
    IntegrityError,
    "NOT NULL constraint violated (23502)."
);
pyo3::create_exception!(
    cello,
    ForeignKeyViolation,
    IntegrityError,
    "Foreign key constraint violated (23503)."
);
pyo3::create_exception!(
    cello,
    CheckViolation,
    IntegrityError,
    "CHECK constraint violated (23514)."
);
pyo3::create_exception!(
    cello,
    DataError,
    DatabaseError,
    "Invalid data (SQLSTATE class 22), or a value that cannot be bound."
);
pyo3::create_exception!(
    cello,
    OperationalError,
    DatabaseError,
    "Connection, pool or server-availability failure."
);
pyo3::create_exception!(
    cello,
    ProgrammingError,
    DatabaseError,
    "Invalid SQL or schema reference (SQLSTATE class 42), or wrong parameter count."
);

/// The parameter-error classes, built with two bases each (`type(name, bases,
/// ns)`) because `create_exception!` takes a single base and these must also be
/// the builtin they replaced. Created once in [`register`].
static PARAM_TYPES: RwLock<Option<[Py<PyAny>; 3]>> = RwLock::new(None);

/// Attributes every `DatabaseError` carries; `None` when the driver did not
/// supply them, so attribute access never raises `AttributeError`.
const ATTRS: [&str; 5] = ["sqlstate", "detail", "constraint", "table", "column"];

/// Build one `DataError` + builtin class with `type(name, bases, ns)`.
fn make_param_type<'py>(
    py: Python<'py>,
    name: &str,
    builtin: Bound<'py, PyType>,
    doc: &str,
) -> PyResult<Py<PyAny>> {
    let ns = PyDict::new(py);
    ns.set_item("__module__", "cello")?;
    ns.set_item("__doc__", doc)?;
    let bases = PyTuple::new(py, [py.get_type::<DataError>(), builtin])?;
    let created = py.get_type::<PyType>().call1((name, bases, ns))?;
    Ok(created.unbind())
}

fn build_param_types(py: Python<'_>) -> PyResult<[Py<PyAny>; 3]> {
    Ok([
        make_param_type(
            py,
            "ParameterTypeError",
            py.get_type::<PyTypeError>(),
            "A query parameter has a Python type that cannot be bound to the \
             parameter's Postgres type. Also a TypeError.",
        )?,
        make_param_type(
            py,
            "ParameterRangeError",
            py.get_type::<PyOverflowError>(),
            "A query parameter is out of range for the parameter's Postgres \
             type. Also an OverflowError.",
        )?,
        make_param_type(
            py,
            "ParameterValueError",
            py.get_type::<PyValueError>(),
            "A query parameter's value cannot be parsed as the parameter's \
             Postgres type. Also a ValueError.",
        )?,
    ])
}

/// The parameter-error classes, if [`register`] has run.
fn param_types(py: Python<'_>) -> Option<[Py<PyAny>; 3]> {
    let guard = PARAM_TYPES.read();
    let classes = guard.as_ref()?;
    Some([
        classes[0].clone_ref(py),
        classes[1].clone_ref(py),
        classes[2].clone_ref(py),
    ])
}

/// The `DataError` subclass for a parameter problem.
fn param_class<'py>(py: Python<'py>, class: ErrClass) -> Option<Bound<'py, PyAny>> {
    let classes = param_types(py)?;
    Some(match class {
        ErrClass::ParamRange => classes[1].clone_ref(py).into_bound(py),
        ErrClass::ParamValue => classes[2].clone_ref(py).into_bound(py),
        _ => classes[0].clone_ref(py).into_bound(py),
    })
}

/// Register the hierarchy on the extension module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();

    // Class-level defaults, so `exc.constraint` is readable on every error.
    let base = py.get_type::<DatabaseError>();
    for attr in ATTRS {
        base.setattr(attr, py.None())?;
    }

    m.add("DatabaseError", base)?;
    m.add("IntegrityError", py.get_type::<IntegrityError>())?;
    m.add("UniqueViolation", py.get_type::<UniqueViolation>())?;
    m.add("NotNullViolation", py.get_type::<NotNullViolation>())?;
    m.add("ForeignKeyViolation", py.get_type::<ForeignKeyViolation>())?;
    m.add("CheckViolation", py.get_type::<CheckViolation>())?;
    m.add("DataError", py.get_type::<DataError>())?;
    m.add("OperationalError", py.get_type::<OperationalError>())?;
    m.add("ProgrammingError", py.get_type::<ProgrammingError>())?;

    let params = build_param_types(py)?;
    m.add("ParameterTypeError", params[0].clone_ref(py))?;
    m.add("ParameterRangeError", params[1].clone_ref(py))?;
    m.add("ParameterValueError", params[2].clone_ref(py))?;
    *PARAM_TYPES.write() = Some(params);
    Ok(())
}

// ── Rust-side error carried to the loop thread ─────────────────────────

/// The SQLSTATE class an error maps to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrClass {
    Database,
    Integrity,
    Unique,
    NotNull,
    ForeignKey,
    Check,
    Data,
    Operational,
    Programming,
    ParamType,
    ParamRange,
    ParamValue,
}

/// A database error as plain data: `Send`, and no GIL needed to build it.
#[derive(Debug)]
pub struct PgError {
    pub class: ErrClass,
    pub msg: String,
    pub sqlstate: Option<String>,
    pub detail: Option<String>,
    pub constraint: Option<String>,
    pub table: Option<String>,
    pub column: Option<String>,
}

fn class_for_sqlstate(code: &str) -> ErrClass {
    match code {
        "23505" => ErrClass::Unique,
        "23502" => ErrClass::NotNull,
        "23503" => ErrClass::ForeignKey,
        "23514" => ErrClass::Check,
        _ => match code.get(..2) {
            Some("23") => ErrClass::Integrity,
            Some("22") => ErrClass::Data,
            Some("42") => ErrClass::Programming,
            Some("08" | "40" | "53" | "57" | "58") => ErrClass::Operational,
            _ => ErrClass::Database,
        },
    }
}

impl PgError {
    pub fn new(class: ErrClass, msg: impl Into<String>) -> Self {
        Self {
            class,
            msg: msg.into(),
            sqlstate: None,
            detail: None,
            constraint: None,
            table: None,
            column: None,
        }
    }

    /// Classify a driver / server error.
    pub fn from_pg(error: &tokio_postgres::Error) -> Self {
        if let Some(db) = error.as_db_error() {
            let code = db.code().code();
            return Self {
                class: class_for_sqlstate(code),
                // tokio-postgres renders a server error as just "db error";
                // the server's message is the useful part.
                msg: format!("db error: {db}"),
                sqlstate: Some(code.to_string()),
                detail: db.detail().map(str::to_string),
                constraint: db.constraint().map(str::to_string),
                table: db.table().map(str::to_string),
                column: db.column().map(str::to_string),
            };
        }
        let text = error.to_string();
        let connection_failure = error.is_closed()
            || error
                .source()
                .is_some_and(|s| s.downcast_ref::<std::io::Error>().is_some());
        if connection_failure {
            // Connection failures: the useful part (a TLS or pg_hba rejection,
            // for instance) is in the cause chain, not in the kind's message.
            return Self::new(ErrClass::Operational, with_causes(error));
        }
        if text.starts_with("expected ") && text.contains(" parameters but got ") {
            return Self::new(ErrClass::Programming, text);
        }
        Self::new(ErrClass::Database, text)
    }

    /// A pool check-out failure, with the whole error chain.
    pub fn pool(error: impl StdError) -> Self {
        Self::new(ErrClass::Operational, with_causes(&error))
    }

    /// Build the Python exception (GIL held).
    pub fn into_pyerr_with(self, py: Python<'_>) -> PyErr {
        let class = match self.class {
            ErrClass::Database => DatabaseError::type_object(py).into_any(),
            ErrClass::Integrity => IntegrityError::type_object(py).into_any(),
            ErrClass::Unique => UniqueViolation::type_object(py).into_any(),
            ErrClass::NotNull => NotNullViolation::type_object(py).into_any(),
            ErrClass::ForeignKey => ForeignKeyViolation::type_object(py).into_any(),
            ErrClass::Check => CheckViolation::type_object(py).into_any(),
            ErrClass::Data => DataError::type_object(py).into_any(),
            ErrClass::Operational => OperationalError::type_object(py).into_any(),
            ErrClass::Programming => ProgrammingError::type_object(py).into_any(),
            // Fall back to the base class if the module was never registered
            // (only reachable outside the extension module itself).
            ErrClass::ParamType | ErrClass::ParamRange | ErrClass::ParamValue => {
                let fallback = DataError::type_object(py).into_any();
                param_class(py, self.class).unwrap_or(fallback)
            }
        };

        let build = || -> PyResult<PyErr> {
            let instance = class.call1((self.msg.as_str(),))?;
            let values = [
                &self.sqlstate,
                &self.detail,
                &self.constraint,
                &self.table,
                &self.column,
            ];
            for (name, value) in ATTRS.iter().zip(values) {
                if let Some(value) = value {
                    instance.setattr(*name, value.as_str())?;
                }
            }
            Ok(PyErr::from_value(instance))
        };
        build().unwrap_or_else(|e| e)
    }

    /// Build the Python exception, acquiring the GIL if needed.
    ///
    /// Called from the I/O thread, where the GIL is not held.
    pub fn into_pyerr(self) -> PyErr {
        Python::attach(|py| self.into_pyerr_with(py))
    }
}

/// Raise a parameter error synchronously (Python → Rust conversion, GIL held).
pub fn param_type_error(msg: impl Into<String>) -> PyErr {
    PgError::new(ErrClass::ParamType, msg).into_pyerr()
}

/// A value the driver could not decode (a malformed or unsupported column).
pub fn decode_error(msg: impl fmt::Display) -> PyErr {
    PgError::new(ErrClass::Data, format!("cannot decode column: {msg}")).into_pyerr()
}

/// `error` followed by each distinct message in its `source()` chain.
fn with_causes(error: &dyn StdError) -> String {
    let mut msg = error.to_string();
    let mut current = error.source();
    while let Some(cause) = current {
        let part = cause.to_string();
        if !part.is_empty() && !msg.contains(&part) {
            msg.push_str(": ");
            msg.push_str(&part);
        }
        current = cause.source();
    }
    msg
}

impl fmt::Display for PgError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.msg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every SQLSTATE class reaches the error class a caller would expect,
    /// including the generic fallbacks for classes with no specific rule.
    #[test]
    fn test_class_for_sqlstate() {
        assert_eq!(class_for_sqlstate("23505"), ErrClass::Unique);
        assert_eq!(class_for_sqlstate("23502"), ErrClass::NotNull);
        assert_eq!(class_for_sqlstate("23503"), ErrClass::ForeignKey);
        assert_eq!(class_for_sqlstate("23514"), ErrClass::Check);
        assert_eq!(class_for_sqlstate("23500"), ErrClass::Integrity);
        assert_eq!(class_for_sqlstate("22003"), ErrClass::Data);
        assert_eq!(class_for_sqlstate("42P01"), ErrClass::Programming);
        assert_eq!(class_for_sqlstate("08006"), ErrClass::Operational);
        assert_eq!(class_for_sqlstate("57P03"), ErrClass::Operational);
        assert_eq!(class_for_sqlstate("XX000"), ErrClass::Database);
    }

    /// A pool failure's own message is only the error kind; the useful part is
    /// in the cause chain, and the message has to carry it.
    #[test]
    fn test_operational_message_keeps_the_cause_chain() {
        #[derive(Debug)]
        struct Outer(Inner);
        #[derive(Debug)]
        struct Inner;

        impl fmt::Display for Outer {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("error performing TLS handshake")
            }
        }
        impl StdError for Outer {
            fn source(&self) -> Option<&(dyn StdError + 'static)> {
                Some(&self.0)
            }
        }
        impl fmt::Display for Inner {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("invalid peer certificate: NotValidForName")
            }
        }
        impl StdError for Inner {}

        let error = PgError::pool(Outer(Inner));
        assert_eq!(error.class, ErrClass::Operational);
        assert!(error.msg.contains("NotValidForName"), "{}", error.msg);
    }
}
