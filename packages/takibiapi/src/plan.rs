//! The checking plan: a Python type walked once (``takibiapi.schema.plan``) and rebuilt here as Rust
//! nodes, so requests never walk ``typing`` objects again. Only types are checked: ``Annotated``
//! keywords (``minLength``, ``maximum``, ...) are documentation and are not part of the plan.

use std::collections::HashMap;
use std::sync::Arc;

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Container {
    List,
    Tuple,
    Set,
    FrozenSet,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    DateTime,
    Date,
    Uuid,
}

impl Format {
    pub fn message(self) -> &'static str {
        match self {
            Format::DateTime => "Expected date-time",
            Format::Date => "Expected date",
            Format::Uuid => "Expected uuid",
        }
    }
}

/// A ``Literal`` / ``Enum`` value, compared with the exact Python type (``1`` is not ``True``).
#[derive(Debug, Clone, PartialEq)]
pub enum Lit {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
}

impl Lit {
    fn from_py(value: &Bound<'_, PyAny>) -> PyResult<Lit> {
        if value.is_none() {
            Ok(Lit::None)
        } else if value.is_exact_instance_of::<PyBool>() {
            Ok(Lit::Bool(value.extract()?))
        } else if value.is_exact_instance_of::<PyInt>() {
            Ok(Lit::Int(value.extract()?))
        } else if value.is_exact_instance_of::<PyFloat>() {
            Ok(Lit::Float(value.extract()?))
        } else if value.is_exact_instance_of::<PyString>() {
            Ok(Lit::Str(value.extract()?))
        } else {
            Err(PyTypeError::new_err(format!(
                "literal values must be str, int, float, bool or None, got {}",
                value.repr()?
            )))
        }
    }
}

#[derive(Debug)]
pub enum Node {
    Any,
    None,
    Bool,
    Int,
    Float,
    Str,
    Format(Format),
    List {
        items: Box<Node>,
        container: Container,
    },
    Dict(Box<Node>),
    Union {
        members: Vec<Node>,
        nullable: bool,
    },
    Literal {
        values: Vec<Lit>,
        objects: Vec<Py<PyAny>>,
        message: String,
    },
    Enum {
        values: Vec<Lit>,
        members: Vec<Py<PyAny>>,
        cls: Py<PyAny>,
        message: String,
    },
    Ref(usize),
}

#[derive(Debug)]
pub struct FieldDef {
    pub name: Arc<str>,
    pub key: Py<PyString>,
    pub node: Node,
    pub required: bool,
    pub default: Option<Py<PyAny>>,
    pub factory: bool,
}

#[derive(Debug)]
pub struct ObjectDef {
    pub cls: Option<Py<PyAny>>, // the dataclass to build; None for a TypedDict (a dict)
    // The dataclass can be built as object.__new__(cls) + its __dict__ (no __init__ call): its
    // __init__ is the generated one and there is no __post_init__, __slots__ or InitVar.
    pub direct: bool,
    pub fields: Vec<FieldDef>,
    pub index: HashMap<String, usize>,
}

impl ObjectDef {
    /// The field named ``key``; ``hint`` (the field after the previous one) is tried first, as
    /// keys usually come in declaration order.
    pub fn find(&self, key: &str, hint: usize) -> Option<usize> {
        if self.fields.get(hint).is_some_and(|f| &*f.name == key) {
            return Some(hint);
        }
        if self.fields.len() <= 16 {
            return self.fields.iter().position(|f| &*f.name == key);
        }
        self.index.get(key).copied()
    }
}

#[derive(Debug)]
pub struct Plan {
    pub root: Node,
    pub defs: Vec<ObjectDef>,
}

fn item<'py>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<Bound<'py, PyAny>> {
    d.get_item(key)?
        .ok_or_else(|| PyTypeError::new_err(format!("plan node without {key:?}")))
}

fn literals(list: &Bound<'_, PyAny>) -> PyResult<(Vec<Lit>, Vec<Py<PyAny>>, String)> {
    let list = list.cast::<PyList>()?;
    let mut values = Vec::with_capacity(list.len());
    let mut objects = Vec::with_capacity(list.len());
    for v in list.iter() {
        values.push(Lit::from_py(&v)?);
        objects.push(v.unbind());
    }
    Ok((values, objects, format!("Expected one of {}", list.repr()?)))
}

fn build_node(d: &Bound<'_, PyAny>) -> PyResult<Node> {
    let d = d.cast::<PyDict>()?;
    let kind: String = item(d, "k")?.extract()?;
    Ok(match kind.as_str() {
        "any" => Node::Any,
        "none" => Node::None,
        "bool" => Node::Bool,
        "int" => Node::Int,
        "float" => Node::Float,
        "str" => Node::Str,
        "datetime" => Node::Format(Format::DateTime),
        "date" => Node::Format(Format::Date),
        "uuid" => Node::Format(Format::Uuid),
        "list" => {
            let container = match item(d, "container")?.extract::<String>()?.as_str() {
                "list" => Container::List,
                "tuple" => Container::Tuple,
                "set" => Container::Set,
                "frozenset" => Container::FrozenSet,
                other => return Err(PyTypeError::new_err(format!("unknown container {other}"))),
            };
            Node::List {
                items: Box::new(build_node(&item(d, "items")?)?),
                container,
            }
        }
        "dict" => Node::Dict(Box::new(build_node(&item(d, "values")?)?)),
        "union" => {
            let members = item(d, "members")?;
            let members = members
                .cast::<PyList>()?
                .iter()
                .map(|m| build_node(&m))
                .collect::<PyResult<Vec<_>>>()?;
            Node::Union {
                members,
                nullable: item(d, "nullable")?.extract()?,
            }
        }
        "literal" => {
            let (values, objects, message) = literals(&item(d, "values")?)?;
            Node::Literal {
                values,
                objects,
                message,
            }
        }
        "enum" => {
            let (values, _, message) = literals(&item(d, "values")?)?;
            let members = item(d, "members")?;
            let members = members.cast::<PyList>()?.iter().map(Bound::unbind).collect();
            Node::Enum {
                values,
                members,
                cls: item(d, "cls")?.unbind(),
                message,
            }
        }
        "ref" => Node::Ref(item(d, "i")?.extract()?),
        other => return Err(PyTypeError::new_err(format!("unknown plan node {other:?}"))),
    })
}

pub fn build_plan(plan: &Bound<'_, PyDict>) -> PyResult<Plan> {
    let py = plan.py();
    let root = build_node(&item(plan, "root")?)?;
    let mut defs = Vec::new();
    for def in item(plan, "defs")?.cast::<PyList>()?.iter() {
        let def = def.cast::<PyDict>()?;
        let cls = item(def, "cls")?;
        let mut fields = Vec::new();
        let mut index = HashMap::new();
        for (i, f) in item(def, "fields")?.cast::<PyList>()?.iter().enumerate() {
            let f = f.cast::<PyDict>()?;
            let name: String = item(f, "name")?.extract()?;
            let has_default: bool = item(f, "has_default")?.extract()?;
            index.insert(name.clone(), i);
            fields.push(FieldDef {
                key: PyString::intern(py, &name).unbind(),
                name: Arc::from(name),
                node: build_node(&item(f, "node")?)?,
                required: item(f, "required")?.extract()?,
                default: if has_default {
                    Some(item(f, "default")?.unbind())
                } else {
                    None
                },
                factory: item(f, "factory")?.extract()?,
            });
        }
        let direct: bool = item(def, "direct")?.extract()?;
        defs.push(ObjectDef {
            cls: if cls.is_none() { None } else { Some(cls.unbind()) },
            direct,
            fields,
            index,
        });
    }
    Ok(Plan { root, defs })
}
