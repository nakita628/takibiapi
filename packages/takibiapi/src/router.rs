//! The path router (``takibiapi.router.Router`` in Rust): OpenAPI path templates (``/todos/{id}``),
//! a map for paths without parameters and a segment trie for the rest. Static segments win over
//! parameters; the same results as the Python router.

use std::collections::{BTreeSet, HashMap};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};

#[derive(Debug, Default)]
struct Node {
    children: HashMap<String, Node>,
    param: Option<(String, Box<Node>)>,
    handlers: HashMap<String, Py<PyAny>>,
}

fn segments(path: &str) -> impl Iterator<Item = &str> {
    path.split('/').filter(|s| !s.is_empty())
}

fn param_name(seg: &str) -> Option<&str> {
    seg.strip_prefix('{').and_then(|s| s.strip_suffix('}'))
}

fn normalized(path: &str) -> String {
    let mut out = String::with_capacity(path.len() + 1);
    for seg in segments(path) {
        out.push('/');
        out.push_str(seg);
    }
    if out.is_empty() {
        out.push('/');
    }
    out
}

fn lookup<'a>(
    node: &'a Node,
    segs: &[&'a str],
    method: &str,
    params: &mut Vec<(&'a str, &'a str)>,
) -> Option<&'a Py<PyAny>> {
    let Some((seg, rest)) = segs.split_first() else {
        return node.handlers.get(method);
    };
    if let Some(child) = node.children.get(*seg) {
        if let Some(found) = lookup(child, rest, method, params) {
            return Some(found);
        }
    }
    if let Some((name, child)) = &node.param {
        params.push((name.as_str(), seg));
        if let Some(found) = lookup(child, rest, method, params) {
            return Some(found);
        }
        params.pop();
    }
    None
}

fn methods(node: &Node, segs: &[&str], out: &mut BTreeSet<String>) {
    let Some((seg, rest)) = segs.split_first() else {
        out.extend(node.handlers.keys().cloned());
        return;
    };
    if let Some(child) = node.children.get(*seg) {
        methods(child, rest, out);
    }
    if let Some((_, child)) = &node.param {
        methods(child, rest, out);
    }
}

#[pyclass(module = "takibiapi._core")]
#[derive(Debug, Default)]
pub struct Router {
    fixed: HashMap<String, HashMap<String, Py<PyAny>>>, // path -> {method: entry}
    root: Node,
}

#[pymethods]
impl Router {
    #[new]
    fn new() -> Self {
        Router::default()
    }

    fn add(&mut self, method: &str, path: &str, entry: Py<PyAny>) -> PyResult<()> {
        let method = method.to_ascii_uppercase();
        let duplicate = || PyValueError::new_err(format!("{method} {path} is already registered"));
        if !segments(path).any(|s| param_name(s).is_some()) {
            let by_method = self.fixed.entry(normalized(path)).or_default();
            if by_method.contains_key(&method) {
                return Err(duplicate());
            }
            by_method.insert(method, entry);
            return Ok(());
        }
        let mut node = &mut self.root;
        for seg in segments(path) {
            node = match param_name(seg) {
                Some(name) => {
                    let (existing, child) = node.param.get_or_insert_with(|| (name.to_owned(), Box::default()));
                    if existing != name {
                        return Err(PyValueError::new_err(format!(
                            "conflicting parameter names in {path:?}: {{{existing}}} vs {seg}"
                        )));
                    }
                    child
                }
                None => node.children.entry(seg.to_owned()).or_default(),
            };
        }
        if node.handlers.contains_key(&method) {
            return Err(duplicate());
        }
        node.handlers.insert(method, entry);
        Ok(())
    }

    /// ``(entry, {param: text})`` for ``method path``, or None.
    #[pyo3(name = "match")]
    fn find<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        path: &str,
    ) -> PyResult<Option<(Py<PyAny>, Bound<'py, PyDict>)>> {
        let by_method = self.fixed.get(path).or_else(|| {
            (path.ends_with('/') && path.len() > 1 || path.contains("//"))
                .then(|| self.fixed.get(&normalized(path)))
                .flatten()
        });
        if let Some(entry) = by_method.and_then(|m| m.get(method)) {
            return Ok(Some((entry.clone_ref(py), PyDict::new(py))));
        }
        let segs: Vec<&str> = segments(path).collect();
        let mut params = Vec::new();
        match lookup(&self.root, &segs, method, &mut params) {
            Some(entry) => {
                let dict = PyDict::new(py);
                for (name, value) in params {
                    dict.set_item(PyString::new(py, name), value)?;
                }
                Ok(Some((entry.clone_ref(py), dict)))
            }
            None => Ok(None),
        }
    }

    /// The methods registered for ``path``, sorted (for 405 Method Not Allowed).
    fn allowed(&self, path: &str) -> Vec<String> {
        let mut out = BTreeSet::new();
        if let Some(by_method) = self.fixed.get(&normalized(path)) {
            out.extend(by_method.keys().cloned());
        }
        let segs: Vec<&str> = segments(path).collect();
        methods(&self.root, &segs, &mut out);
        out.into_iter().collect()
    }
}
