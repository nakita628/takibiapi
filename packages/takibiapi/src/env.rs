//! Issues (``{"path", "code", "message"}`` dicts, as the Python engine reports them) and the Python
//! objects the engine needs (``datetime``, ``UUID``, ``Enum``, ...), imported once.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};

#[derive(Debug, Clone)]
pub enum Seg {
    Key(Arc<str>), // shared with the plan's field names: no allocation per field
    Index(usize),
}

#[derive(Debug, Clone)]
pub struct Issue {
    pub path: Vec<Seg>,
    pub code: &'static str,
    pub message: String,
}

/// The current path and the issues found so far.
#[derive(Debug, Default)]
pub struct Ctx {
    pub path: Vec<Seg>,
    pub issues: Vec<Issue>,
}

impl Ctx {
    pub fn fail(&mut self, code: &'static str, message: impl Into<String>) {
        self.issues.push(Issue {
            path: self.path.clone(),
            code,
            message: message.into(),
        });
    }

    pub fn type_error(&mut self, expected: &str, received: &str) {
        self.fail("invalid_type", format!("Expected {expected}, received {received}"));
    }

    /// A context for one attempt (a union member, an object field) at the same path; give the
    /// path back with :meth:`finish`.
    pub fn child(&mut self) -> Ctx {
        Ctx {
            path: std::mem::take(&mut self.path),
            issues: Vec::new(),
        }
    }

    pub fn finish(&mut self, child: Ctx) -> Vec<Issue> {
        self.path = child.path;
        child.issues
    }

    pub fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty(py);
        for issue in &self.issues {
            let path = PyList::empty(py);
            for seg in &issue.path {
                match seg {
                    Seg::Key(k) => path.append(&**k)?,
                    Seg::Index(i) => path.append(i)?,
                }
            }
            let d = PyDict::new(py);
            d.set_item("path", path)?;
            d.set_item("code", issue.code)?;
            d.set_item("message", &issue.message)?;
            out.append(d)?;
        }
        Ok(out)
    }
}

/// ``"null"`` for ``None``, else ``type(value).__name__`` (as in Python's messages).
pub fn type_name(value: &Bound<'_, PyAny>) -> String {
    if value.is_none() {
        return "null".to_owned();
    }
    value
        .get_type()
        .name()
        .map_or_else(|_| "object".to_owned(), |n| n.to_string())
}

#[derive(Debug)]
pub struct Env {
    pub datetime: Py<PyAny>,
    pub date: Py<PyAny>,
    pub time: Py<PyAny>,
    pub uuid: Py<PyAny>,
    pub enum_: Py<PyAny>,
    pub mapping: Py<PyAny>,
    pub int_: Py<PyAny>,
    pub float_: Py<PyAny>,
    dataclass_fields: Py<PyAny>,
    field_names: Py<PyDict>, // dataclass type -> tuple of field names (dataclasses.fields order)
    kinds: Py<PyDict>,       // type -> Kind as an int, for the JSON writer
}

/// How the JSON writer writes values of a type that JSON has no type for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Dataclass,
    Enum,
    Iso, // date / datetime / time: isoformat()
    Uuid,
    Set,
    Mapping,
    Other,
}

const KINDS: [Kind; 7] = [
    Kind::Dataclass,
    Kind::Enum,
    Kind::Iso,
    Kind::Uuid,
    Kind::Set,
    Kind::Mapping,
    Kind::Other,
];

static ENV: PyOnceLock<Env> = PyOnceLock::new();

pub fn env(py: Python<'_>) -> PyResult<&Env> {
    ENV.get_or_try_init(py, || {
        let datetime = py.import("datetime")?;
        let builtins = py.import("builtins")?;
        Ok(Env {
            datetime: datetime.getattr("datetime")?.unbind(),
            date: datetime.getattr("date")?.unbind(),
            time: datetime.getattr("time")?.unbind(),
            uuid: py.import("uuid")?.getattr("UUID")?.unbind(),
            enum_: py.import("enum")?.getattr("Enum")?.unbind(),
            mapping: py.import("collections.abc")?.getattr("Mapping")?.unbind(),
            int_: builtins.getattr("int")?.unbind(),
            float_: builtins.getattr("float")?.unbind(),
            dataclass_fields: py.import("dataclasses")?.getattr("fields")?.unbind(),
            field_names: PyDict::new(py).unbind(),
            kinds: PyDict::new(py).unbind(),
        })
    })
}

impl Env {
    /// The kind of ``value``'s type, decided once per type.
    pub fn kind(&self, value: &Bound<'_, PyAny>) -> PyResult<Kind> {
        let py = value.py();
        let tp = value.get_type();
        let cache = self.kinds.bind(py);
        if let Some(code) = cache.get_item(&tp)? {
            return Ok(KINDS[code.extract::<usize>()?]);
        }
        let kind = if self.dataclass_names(value)?.is_some() && !value.is_instance_of::<pyo3::types::PyType>() {
            Kind::Dataclass
        } else if value.is_instance(self.enum_.bind(py))? {
            Kind::Enum
        } else if value.is_instance(self.date.bind(py))? || value.is_instance(self.time.bind(py))? {
            Kind::Iso
        } else if value.is_instance(self.uuid.bind(py))? {
            Kind::Uuid
        } else if value.is_instance_of::<pyo3::types::PySet>() || value.is_instance_of::<pyo3::types::PyFrozenSet>() {
            Kind::Set
        } else if value.is_instance(self.mapping.bind(py))? {
            Kind::Mapping
        } else {
            Kind::Other
        };
        let code = KINDS.iter().position(|k| *k == kind).unwrap_or(KINDS.len() - 1);
        cache.set_item(&tp, code)?;
        Ok(kind)
    }

    /// The field names of a dataclass instance's type, or None when it is not a dataclass.
    pub fn dataclass_names<'py>(&self, value: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyTuple>>> {
        let py = value.py();
        let tp = value.get_type();
        let cache = self.field_names.bind(py);
        if let Some(names) = cache.get_item(&tp)? {
            return Ok(if names.is_none() {
                None
            } else {
                Some(names.cast_into::<PyTuple>()?)
            });
        }
        let names = if tp.hasattr("__dataclass_fields__")? {
            let fields = self.dataclass_fields.bind(py).call1((&tp,))?;
            let mut names = Vec::new();
            for f in fields.try_iter()? {
                names.push(f?.getattr("name")?.cast_into::<PyString>()?);
            }
            Some(PyTuple::new(py, names)?)
        } else {
            None
        };
        match &names {
            Some(t) => cache.set_item(&tp, t)?,
            None => cache.set_item(&tp, py.None())?,
        }
        Ok(names)
    }
}

/// ``object.__new__(cls)`` with ``dict`` as its ``__dict__``: a dataclass built without calling
/// its generated ``__init__`` (``takibiapi.schema.plan`` marks the classes this is right for).
pub fn new_with_dict<'py>(cls: &Bound<'py, PyAny>, dict: &Bound<'py, PyDict>) -> PyResult<Bound<'py, PyAny>> {
    let py = cls.py();
    let args = PyTuple::empty(py);
    // SAFETY: ``cls`` is a type object (a plain dataclass whose ``__new__`` is ``object.__new__``)
    // and ``args`` an empty tuple, both alive for the call; the slot is ``object``'s ``tp_new``
    // (checked for NULL) and the returned reference is owned and checked.
    let obj = unsafe {
        let slot = pyo3::ffi::PyType_GetSlot(
            std::ptr::addr_of_mut!(pyo3::ffi::PyBaseObject_Type),
            pyo3::ffi::Py_tp_new,
        );
        if slot.is_null() {
            return Err(pyo3::exceptions::PyTypeError::new_err("object has no tp_new"));
        }
        let new: pyo3::ffi::newfunc = std::mem::transmute(slot);
        Bound::from_owned_ptr_or_err(py, new(cls.as_ptr().cast(), args.as_ptr(), std::ptr::null_mut()))?
    };
    // ``object.__setattr__(obj, "__dict__", dict)``: through the ``__dict__`` descriptor, which on
    // Python 3.13 also retires the attributes the instance keeps inline (``PyObject_GenericSetDict``
    // swaps the pointer only, and they read back empty), and past the class's own ``__setattr__``,
    // which a frozen dataclass makes refuse every assignment.
    let name = pyo3::intern!(py, "__dict__");
    // SAFETY: ``obj``, ``name`` and ``dict`` are alive for the call, which only borrows them.
    if unsafe { pyo3::ffi::PyObject_GenericSetAttr(obj.as_ptr(), name.as_ptr(), dict.as_ptr()) } != 0 {
        return Err(PyErr::fetch(py));
    }
    Ok(obj)
}
