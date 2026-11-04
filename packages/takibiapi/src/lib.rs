//! ``takibiapi._core``: request type checking (``Validator``), JSON reading / writing and query
//! parsing in Rust, with exactly the results of ``takibiapi.schema``'s Python checker and of Python's
//! ``json`` and ``urllib.parse`` (input it cannot match is left to them: ``None``).

// Lint levels live here rather than in Cargo.toml, next to the code they apply to.
#![warn(missing_debug_implementations, unused_qualifications)]
#![warn(clippy::all, clippy::pedantic)]
#![warn(
    clippy::dbg_macro,
    clippy::todo,
    clippy::print_stdout,
    clippy::undocumented_unsafe_blocks
)]
#![allow(clippy::module_name_repetitions, clippy::must_use_candidate)]
#![allow(clippy::missing_errors_doc, clippy::missing_panics_doc)]
// Lengths and digit counts, all far below the limits.
#![allow(clippy::cast_possible_truncation, clippy::cast_possible_wrap)]
#![allow(clippy::cast_precision_loss, clippy::cast_sign_loss)]
// Literal values are matched exactly, as Python's == does.
#![allow(clippy::float_cmp)]
// The decoders are one match over every node kind.
#![allow(clippy::too_many_lines)]

mod encode;
mod engine;
mod env;
mod json;
mod plan;
mod router;

use pyo3::prelude::*;

#[pymodule]
mod _core {
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;
    use pyo3::types::{PyBytes, PyDict, PyList, PyString};

    #[pymodule_export]
    use crate::router::Router;

    use crate::encode::write_any;
    use crate::env::Ctx;
    use crate::json::{Fail, Parser};
    use crate::plan::{build_plan, Plan};

    fn malformed(at: usize) -> PyErr {
        PyValueError::new_err(format!("Malformed JSON at byte {at}"))
    }

    /// ``(ok, value)``: the typed value, or the issues when it does not match.
    type Checked = (bool, Py<PyAny>);

    fn result(py: Python<'_>, value: Option<Bound<'_, PyAny>>, cx: &Ctx) -> PyResult<Checked> {
        match value {
            Some(v) if cx.issues.is_empty() => Ok((true, v.unbind())),
            _ => Ok((false, cx.to_py(py)?.into_any().unbind())),
        }
    }

    /// A type compiled from ``takibiapi.schema.plan(tp)``.
    #[pyclass(frozen, module = "takibiapi._core")]
    struct Validator {
        plan: Plan,
    }

    #[pymethods]
    impl Validator {
        #[new]
        fn new(plan: &Bound<'_, PyDict>) -> PyResult<Self> {
            Ok(Validator {
                plan: build_plan(plan)?,
            })
        }

        /// Check a Python value; ``coerce``: it came as text (``"1"`` for an ``int``).
        fn decode(&self, py: Python<'_>, value: &Bound<'_, PyAny>, coerce: bool) -> PyResult<Checked> {
            let mut cx = Ctx::default();
            let out = self.plan.decode(&self.plan.root, value, coerce, &mut cx)?;
            result(py, out, &cx)
        }

        /// Parse and check JSON bytes in one pass. ``ValueError`` for malformed JSON; None for
        /// input left to Python's ``json``.
        fn decode_json(&self, py: Python<'_>, data: &[u8]) -> PyResult<Option<Checked>> {
            let mut cx = Ctx::default();
            let run = |cx: &mut Ctx| -> Result<_, Fail> {
                let mut p = Parser::new(data)?;
                let out = self.plan.decode_json(py, &self.plan.root, &mut p, cx)?;
                p.end()?;
                Ok(out)
            };
            match run(&mut cx) {
                Ok(out) => Ok(Some(result(py, out, &cx)?)),
                Err(Fail::Malformed(at)) => Err(malformed(at)),
                Err(Fail::Unsupported) => Ok(None),
                Err(Fail::Py(err)) => Err(err),
            }
        }
    }

    /// ``json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=default)`` as
    /// bytes, or None for values left to Python's ``json``.
    #[pyfunction]
    #[pyo3(signature = (value, default = None))]
    fn dumps(
        py: Python<'_>,
        value: &Bound<'_, PyAny>,
        default: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Option<Py<PyBytes>>> {
        let mut out = Vec::with_capacity(128);
        match write_any(&mut out, value, 0, default) {
            Ok(()) => Ok(Some(PyBytes::new(py, &out).unbind())),
            Err(Fail::Py(err)) => Err(err),
            Err(_) => Ok(None),
        }
    }

    /// ``(json.loads(data),)``; ``ValueError`` for malformed JSON, None for input left to Python.
    #[pyfunction]
    fn loads(py: Python<'_>, data: &[u8]) -> PyResult<Option<(Py<PyAny>,)>> {
        let run = || -> Result<_, Fail> {
            let mut p = Parser::new(data)?;
            let value = p.any(py)?;
            p.end()?;
            Ok(value)
        };
        match run() {
            Ok(v) => Ok(Some((v.unbind(),))),
            Err(Fail::Malformed(at)) => Err(malformed(at)),
            Err(Fail::Unsupported) => Ok(None),
            Err(Fail::Py(err)) => Err(err),
        }
    }

    fn unquote(part: &[u8]) -> Option<String> {
        let mut out = Vec::with_capacity(part.len());
        let mut i = 0;
        while i < part.len() {
            match part[i] {
                b'+' => out.push(b' '),
                b'%' => {
                    let hex = |c: u8| (c as char).to_digit(16);
                    match (
                        part.get(i + 1).and_then(|&c| hex(c)),
                        part.get(i + 2).and_then(|&c| hex(c)),
                    ) {
                        (Some(h), Some(l)) => {
                            out.push((h * 16 + l) as u8);
                            i += 3;
                            continue;
                        }
                        _ => out.push(b'%'),
                    }
                }
                c => out.push(c),
            }
            i += 1;
        }
        // Python replaces invalid UTF-8 with U+FFFD: leave that to it.
        String::from_utf8(out).ok()
    }

    /// ``{key: [values]}`` like ``urllib.parse.parse_qsl(qs.decode("latin-1"),
    /// keep_blank_values=True)``; None for input left to Python (non-ASCII, invalid UTF-8).
    #[pyfunction]
    fn parse_query<'py>(py: Python<'py>, qs: &[u8]) -> PyResult<Option<Bound<'py, PyDict>>> {
        if !qs.is_ascii() {
            return Ok(None);
        }
        let out = PyDict::new(py);
        for piece in qs.split(|&c| c == b'&') {
            if piece.is_empty() {
                continue;
            }
            let (name, value) = match piece.iter().position(|&c| c == b'=') {
                Some(i) => (&piece[..i], &piece[i + 1..]),
                None => (piece, &b""[..]),
            };
            let (Some(name), Some(value)) = (unquote(name), unquote(value)) else {
                return Ok(None);
            };
            let name = PyString::new(py, &name);
            match out.get_item(&name)? {
                Some(list) => list.cast::<PyList>()?.append(value)?,
                None => out.set_item(name, PyList::new(py, [value])?)?,
            }
        }
        Ok(Some(out))
    }
}
