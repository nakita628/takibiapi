//! Type checking against a plan: ``decode_json`` (JSON bytes -> typed values in one pass) and
//! ``decode`` (Python values, e.g. path / query text with ``coerce``). Each follows
//! ``takibiapi.schema._Checker`` rule for rule, message for message. Only types are checked.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyFrozenSet, PyInt, PyList, PySet, PyString, PyTuple};

use crate::env::{env, new_with_dict, type_name, Ctx, Issue, Seg};
use crate::json::{Fail, JResult, Num, Parser};
use crate::plan::{Container, Format, Lit, Node, ObjectDef, Plan};

type Out<'py> = Option<Bound<'py, PyAny>>;

fn int_text(s: &str) -> bool {
    let d = s.strip_prefix(['+', '-']).unwrap_or(s);
    !d.is_empty() && d.bytes().all(|c| c.is_ascii_digit())
}

/// ``[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?`` (``takibiapi.schema._FLOAT``).
fn float_text(s: &str) -> bool {
    let b = s.as_bytes();
    let mut i = usize::from(matches!(b.first(), Some(b'+' | b'-')));
    let digits = |i: &mut usize| {
        let start = *i;
        while b.get(*i).is_some_and(u8::is_ascii_digit) {
            *i += 1;
        }
        *i - start
    };
    let whole = digits(&mut i);
    if b.get(i) == Some(&b'.') {
        i += 1;
        if digits(&mut i) == 0 && whole == 0 {
            return false;
        }
    } else if whole == 0 {
        return false;
    }
    if matches!(b.get(i), Some(b'e' | b'E')) {
        i += 1;
        if matches!(b.get(i), Some(b'+' | b'-')) {
            i += 1;
        }
        if digits(&mut i) == 0 {
            return false;
        }
    }
    i == b.len()
}

fn py_int<'py>(py: Python<'py>, text: &str) -> PyResult<Bound<'py, PyAny>> {
    match text.parse::<i64>() {
        Ok(i) => Ok(i.into_pyobject(py)?.into_any()),
        Err(_) => env(py)?.int_.bind(py).call1((text,)),
    }
}

fn container<'py>(py: Python<'py>, kind: Container, items: Vec<Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyAny>> {
    Ok(match kind {
        Container::List => PyList::new(py, items)?.into_any(),
        Container::Tuple => PyTuple::new(py, items)?.into_any(),
        Container::Set => PySet::new(py, items)?.into_any(),
        Container::FrozenSet => PyFrozenSet::new(py, items)?.into_any(),
    })
}

fn parse_format<'py>(py: Python<'py>, format: Format, text: &str, cx: &mut Ctx) -> PyResult<Out<'py>> {
    let e = env(py)?;
    let parsed = match format {
        Format::Uuid => e.uuid.bind(py).call1((text,)),
        Format::Date if text.contains(['T', ' ']) => {
            cx.fail("invalid_format", format.message());
            return Ok(None);
        }
        Format::Date => e.date.bind(py).call_method1("fromisoformat", (text,)),
        Format::DateTime => e.datetime.bind(py).call_method1("fromisoformat", (text,)),
    };
    match parsed {
        Ok(v) => Ok(Some(v)),
        Err(err) if err.is_instance_of::<pyo3::exceptions::PyValueError>(py) => {
            cx.fail("invalid_format", format.message());
            Ok(None)
        }
        Err(err) => Err(err),
    }
}

fn lit_matches_py(lit: &Lit, value: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(match lit {
        Lit::None => value.is_none(),
        Lit::Bool(b) => value.is_exact_instance_of::<PyBool>() && value.extract::<bool>()? == *b,
        Lit::Int(i) => value.is_exact_instance_of::<PyInt>() && value.extract::<i64>().is_ok_and(|v| v == *i),
        Lit::Float(f) => value.is_exact_instance_of::<PyFloat>() && value.extract::<f64>()? == *f,
        Lit::Str(s) => value.is_exact_instance_of::<PyString>() && value.cast::<PyString>()?.to_cow()? == s.as_str(),
    })
}

/// A literal matching text with ``coerce`` (``"1"`` for ``Literal[1]``, ``"true"`` for ``True``).
fn lit_matches_text(lit: &Lit, text: &str) -> bool {
    match lit {
        Lit::Bool(b) => (text == "true" && *b) || (text == "false" && !*b),
        Lit::Int(i) => int_text(text) && text.parse::<i64>().is_ok_and(|v| v == *i),
        Lit::Float(f) => float_text(text) && text.parse::<f64>().is_ok_and(|v| v == *f),
        Lit::Str(_) | Lit::None => false,
    }
}

fn py_literal(values: &[Lit], value: &Bound<'_, PyAny>, coerce: bool) -> PyResult<Option<usize>> {
    for (i, lit) in values.iter().enumerate() {
        if lit_matches_py(lit, value)? {
            return Ok(Some(i));
        }
        if coerce {
            if let Ok(s) = value.cast::<PyString>() {
                if lit_matches_text(lit, &s.to_cow()?) {
                    return Ok(Some(i));
                }
            }
        }
    }
    Ok(None)
}

fn is_mapping(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    if value.is_instance_of::<PyDict>() {
        return Ok(true);
    }
    let py = value.py();
    value.is_instance(env(py)?.mapping.bind(py))
}

fn mapping_get<'py>(value: &Bound<'py, PyAny>, key: &Bound<'py, PyString>) -> PyResult<Option<Bound<'py, PyAny>>> {
    if let Ok(d) = value.cast::<PyDict>() {
        return d.get_item(key);
    }
    match value.get_item(key) {
        Ok(v) => Ok(Some(v)),
        Err(err) if err.is_instance_of::<pyo3::exceptions::PyKeyError>(value.py()) => Ok(None),
        Err(err) => Err(err),
    }
}

type Items<'py> = Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>;

fn mapping_items<'py>(value: &Bound<'py, PyAny>) -> PyResult<Option<Items<'py>>> {
    if let Ok(d) = value.cast::<PyDict>() {
        return Ok(Some(d.iter().collect()));
    }
    if !is_mapping(value)? {
        return Ok(None);
    }
    let mut out = Vec::new();
    for item in value.call_method0("items")?.try_iter()? {
        out.push(item?.extract()?);
    }
    Ok(Some(out))
}

#[derive(Default)]
struct Slot<'py> {
    value: Option<Bound<'py, PyAny>>,
    seen: bool,
    issues: Vec<Issue>,
}

fn build_object<'py>(
    py: Python<'py>,
    def: &ObjectDef,
    values: Vec<Option<Bound<'py, PyAny>>>,
) -> PyResult<Bound<'py, PyAny>> {
    let dict = PyDict::new(py);
    for (f, v) in def.fields.iter().zip(values) {
        match v {
            Some(v) => dict.set_item(f.key.bind(py), v)?,
            // Built without __init__: fill the defaults it would have filled.
            None if def.direct => match &f.default {
                Some(d) if f.factory => dict.set_item(f.key.bind(py), d.bind(py).call0()?)?,
                Some(d) => dict.set_item(f.key.bind(py), d.bind(py))?,
                None => {}
            },
            None => {}
        }
    }
    match &def.cls {
        Some(cls) if def.direct => new_with_dict(cls.bind(py), &dict),
        Some(cls) => cls.bind(py).call((), Some(&dict)),
        None => Ok(dict.into_any()),
    }
}

fn expected(node: &Node) -> &'static str {
    match node {
        Node::Int => "integer",
        _ => "number",
    }
}

impl Plan {
    // -- JSON bytes ---------------------------------------------------------------------------- //
    pub fn decode_json<'py>(
        &self,
        py: Python<'py>,
        node: &Node,
        p: &mut Parser<'_>,
        cx: &mut Ctx,
    ) -> JResult<Out<'py>> {
        match node {
            Node::Any => Ok(Some(p.any(py)?)),
            Node::None => {
                if p.peek()? == b'n' {
                    p.keyword()?;
                    return Ok(Some(py.None().into_bound(py)));
                }
                let name = p.skip_named()?;
                cx.type_error("null", name);
                Ok(None)
            }
            Node::Bool => {
                if matches!(p.peek()?, b't' | b'f') {
                    let v = p.keyword()?.unwrap_or_default();
                    return Ok(Some(PyBool::new(py, v).to_owned().into_any()));
                }
                let name = p.skip_named()?;
                cx.type_error("boolean", name);
                Ok(None)
            }
            Node::Int | Node::Float => {
                let c = p.peek()?;
                if c == b'-' || c.is_ascii_digit() {
                    let num = p.number()?;
                    let out = match (node, &num) {
                        (Node::Int, Num::Int(_) | Num::Big(_)) => Some(num.to_py(py)?),
                        (Node::Int, Num::Float(f)) if f.is_finite() && f.fract() == 0.0 => {
                            Some(env(py)?.int_.bind(py).call1((*f,))?)
                        }
                        (Node::Float, Num::Int(i)) => Some(PyFloat::new(py, *i as f64).into_any()),
                        (Node::Float, Num::Big(s)) => {
                            let f: f64 = env(py)?.float_.bind(py).call1((py_int(py, s)?,))?.extract()?;
                            f.is_finite().then(|| PyFloat::new(py, f).into_any())
                        }
                        (Node::Float, Num::Float(f)) if f.is_finite() => Some(PyFloat::new(py, *f).into_any()),
                        _ => None,
                    };
                    if out.is_none() {
                        cx.type_error(expected(node), num.type_name());
                    }
                    return Ok(out);
                }
                let name = p.skip_named()?;
                cx.type_error(expected(node), name);
                Ok(None)
            }
            Node::Str => {
                if p.peek()? == b'"' {
                    return Ok(Some(PyString::new(py, &p.string()?).into_any()));
                }
                let name = p.skip_named()?;
                cx.type_error("string", name);
                Ok(None)
            }
            Node::Format(format) => {
                if p.peek()? == b'"' {
                    let text = p.string()?;
                    return Ok(parse_format(py, *format, &text, cx)?);
                }
                p.skip()?;
                cx.fail("invalid_format", format.message());
                Ok(None)
            }
            Node::List { items, container: kind } => {
                if p.peek()? != b'[' {
                    let name = p.skip_named()?;
                    cx.type_error("array", name);
                    return Ok(None);
                }
                let before = cx.issues.len();
                let mut out = Vec::new();
                p.array(|p, i| {
                    cx.path.push(Seg::Index(i));
                    let v = self.decode_json(py, items, p, cx);
                    cx.path.pop();
                    if let Some(v) = v? {
                        out.push(v);
                    }
                    Ok(())
                })?;
                if cx.issues.len() > before {
                    return Ok(None);
                }
                Ok(Some(container(py, *kind, out)?))
            }
            Node::Dict(values) => {
                if p.peek()? != b'{' {
                    let name = p.skip_named()?;
                    cx.type_error("object", name);
                    return Ok(None);
                }
                let before = cx.issues.len();
                let dict = PyDict::new(py);
                p.object(|p, key| {
                    cx.path.push(Seg::Key(Arc::from(key.as_ref())));
                    let v = self.decode_json(py, values, p, cx);
                    cx.path.pop();
                    if let Some(v) = v? {
                        dict.set_item(PyString::new(py, &key), v)?;
                    }
                    Ok(())
                })?;
                Ok((cx.issues.len() == before).then(|| dict.into_any()))
            }
            Node::Union { members, nullable } => {
                if *nullable && p.peek()? == b'n' {
                    p.keyword()?;
                    return Ok(Some(py.None().into_bound(py)));
                }
                let start = p.i;
                for member in members {
                    p.i = start;
                    let mut attempt = cx.child();
                    let out = self.decode_json(py, member, p, &mut attempt);
                    let issues = cx.finish(attempt);
                    if let Some(v) = out? {
                        if issues.is_empty() {
                            return Ok(Some(v));
                        }
                    }
                }
                p.i = start;
                if *nullable && members.len() == 1 {
                    return self.decode_json(py, &members[0], p, cx); // T | None: T's own issues
                }
                p.skip()?;
                cx.fail("invalid_union", "Value does not match any of the allowed types");
                Ok(None)
            }
            Node::Literal {
                values,
                objects,
                message,
            } => {
                if let Some(i) = json_literal(p, values)? {
                    return Ok(Some(objects[i].bind(py).clone()));
                }
                cx.fail("invalid_value", message.clone());
                Ok(None)
            }
            Node::Enum {
                values,
                members,
                message,
                ..
            } => {
                if let Some(i) = json_literal(p, values)? {
                    return Ok(Some(members[i].bind(py).clone()));
                }
                cx.fail("invalid_value", message.clone());
                Ok(None)
            }
            Node::Ref(i) => self.decode_json_object(py, &self.defs[*i], p, cx),
        }
    }

    fn decode_json_object<'py>(
        &self,
        py: Python<'py>,
        def: &ObjectDef,
        p: &mut Parser<'_>,
        cx: &mut Ctx,
    ) -> JResult<Out<'py>> {
        if p.peek()? != b'{' {
            let name = p.skip_named()?;
            cx.type_error("object", name);
            return Ok(None);
        }
        let mut slots: Vec<Slot<'py>> = (0..def.fields.len()).map(|_| Slot::default()).collect();
        let mut hint = 0;
        p.object(|p, key| {
            let Some(i) = def.find(&key, hint) else {
                return p.skip();
            };
            hint = i + 1;
            let mut attempt = cx.child();
            attempt.path.push(Seg::Key(def.fields[i].name.clone()));
            let out = self.decode_json(py, &def.fields[i].node, p, &mut attempt);
            attempt.path.pop();
            let slot = &mut slots[i];
            slot.issues = cx.finish(attempt);
            slot.value = out?;
            slot.seen = true;
            Ok(())
        })?;
        let before = cx.issues.len();
        let mut values = Vec::with_capacity(slots.len());
        // Issues in field order (as the Python engine reports them), missing fields included.
        for (f, mut slot) in def.fields.iter().zip(slots) {
            values.push(slot.value.take());
            if slot.seen {
                cx.issues.append(&mut slot.issues);
            } else if f.required {
                cx.path.push(Seg::Key(f.name.clone()));
                cx.fail("missing", "Required");
                cx.path.pop();
            }
        }
        if cx.issues.len() > before {
            return Ok(None);
        }
        Ok(Some(build_object(py, def, values)?))
    }

    // -- Python values ------------------------------------------------------------------------- //
    pub fn decode<'py>(
        &self,
        node: &Node,
        value: &Bound<'py, PyAny>,
        coerce: bool,
        cx: &mut Ctx,
    ) -> PyResult<Out<'py>> {
        let py = value.py();
        match node {
            Node::Any => Ok(Some(value.clone())),
            Node::None => {
                if value.is_none() {
                    return Ok(Some(value.clone()));
                }
                cx.type_error("null", &type_name(value));
                Ok(None)
            }
            Node::Bool => {
                if coerce {
                    if let Ok(s) = value.cast::<PyString>() {
                        match s.to_cow()?.as_ref() {
                            "true" => return Ok(Some(PyBool::new(py, true).to_owned().into_any())),
                            "false" => return Ok(Some(PyBool::new(py, false).to_owned().into_any())),
                            _ => {}
                        }
                    }
                }
                if value.is_exact_instance_of::<PyBool>() {
                    return Ok(Some(value.clone()));
                }
                cx.type_error("boolean", &type_name(value));
                Ok(None)
            }
            Node::Int => {
                if coerce {
                    if let Ok(s) = value.cast::<PyString>() {
                        let text = s.to_cow()?;
                        if int_text(&text) {
                            return Ok(Some(py_int(py, text.strip_prefix('+').unwrap_or(&text))?));
                        }
                    }
                }
                if value.is_instance_of::<PyInt>() && !value.is_exact_instance_of::<PyBool>() {
                    return Ok(Some(value.clone()));
                }
                if let Ok(f) = value.cast::<PyFloat>() {
                    let v = f.value();
                    if v.is_finite() && v.fract() == 0.0 {
                        return Ok(Some(env(py)?.int_.bind(py).call1((v,))?));
                    }
                }
                cx.type_error("integer", &type_name(value));
                Ok(None)
            }
            Node::Float => {
                let mut v: Option<f64> = None;
                if coerce {
                    if let Ok(s) = value.cast::<PyString>() {
                        let text = s.to_cow()?;
                        if float_text(&text) {
                            v = text.parse::<f64>().ok();
                        }
                    }
                }
                if v.is_none()
                    && (value.is_instance_of::<PyInt>() || value.is_instance_of::<PyFloat>())
                    && !value.is_exact_instance_of::<PyBool>()
                {
                    v = value.extract::<f64>().ok();
                }
                match v {
                    Some(f) if f.is_finite() => Ok(Some(PyFloat::new(py, f).into_any())),
                    _ => {
                        cx.type_error("number", &type_name(value));
                        Ok(None)
                    }
                }
            }
            Node::Str => {
                if value.is_instance_of::<PyString>() {
                    return Ok(Some(value.clone()));
                }
                cx.type_error("string", &type_name(value));
                Ok(None)
            }
            Node::Format(format) => {
                if let Ok(s) = value.cast::<PyString>() {
                    return parse_format(py, *format, &s.to_cow()?, cx);
                }
                cx.fail("invalid_format", format.message());
                Ok(None)
            }
            Node::List { items, container: kind } => {
                let is_array = value.is_instance_of::<PyList>()
                    || value.is_instance_of::<PyTuple>()
                    || value.is_instance_of::<PySet>()
                    || value.is_instance_of::<PyFrozenSet>();
                if !is_array {
                    cx.type_error("array", &type_name(value));
                    return Ok(None);
                }
                let before = cx.issues.len();
                let mut out = Vec::new();
                for (i, item) in value.try_iter()?.enumerate() {
                    cx.path.push(Seg::Index(i));
                    let v = self.decode(items, &item?, coerce, cx);
                    cx.path.pop();
                    if let Some(v) = v? {
                        out.push(v);
                    }
                }
                if cx.issues.len() > before {
                    return Ok(None);
                }
                Ok(Some(container(py, *kind, out)?))
            }
            Node::Dict(values) => {
                let Some(items) = mapping_items(value)? else {
                    cx.type_error("object", &type_name(value));
                    return Ok(None);
                };
                let before = cx.issues.len();
                let dict = PyDict::new(py);
                for (k, v) in items {
                    let key = k.str()?;
                    cx.path.push(Seg::Key(Arc::from(key.to_cow()?.as_ref())));
                    let out = self.decode(values, &v, coerce, cx);
                    cx.path.pop();
                    if let Some(out) = out? {
                        dict.set_item(key, out)?;
                    }
                }
                Ok((cx.issues.len() == before).then(|| dict.into_any()))
            }
            Node::Union { members, nullable } => {
                if *nullable && value.is_none() {
                    return Ok(Some(value.clone()));
                }
                for member in members {
                    let mut attempt = cx.child();
                    let out = self.decode(member, value, coerce, &mut attempt);
                    let issues = cx.finish(attempt);
                    if let Some(v) = out? {
                        if issues.is_empty() {
                            return Ok(Some(v));
                        }
                    }
                }
                if *nullable && members.len() == 1 {
                    return self.decode(&members[0], value, coerce, cx);
                }
                cx.fail("invalid_union", "Value does not match any of the allowed types");
                Ok(None)
            }
            Node::Literal {
                values,
                objects,
                message,
            } => {
                if let Some(i) = py_literal(values, value, coerce)? {
                    return Ok(Some(if coerce {
                        objects[i].bind(py).clone()
                    } else {
                        value.clone()
                    }));
                }
                cx.fail("invalid_value", message.clone());
                Ok(None)
            }
            Node::Enum {
                values,
                members,
                cls,
                message,
            } => {
                if value.is_instance(cls.bind(py))? {
                    return Ok(Some(value.clone()));
                }
                if let Some(i) = py_literal(values, value, coerce)? {
                    return Ok(Some(members[i].bind(py).clone()));
                }
                cx.fail("invalid_value", message.clone());
                Ok(None)
            }
            Node::Ref(i) => {
                let def = &self.defs[*i];
                if !is_mapping(value)? {
                    cx.type_error("object", &type_name(value));
                    return Ok(None);
                }
                let before = cx.issues.len();
                let mut values = Vec::with_capacity(def.fields.len());
                for f in &def.fields {
                    cx.path.push(Seg::Key(f.name.clone()));
                    if let Some(raw) = mapping_get(value, f.key.bind(py))? {
                        values.push(self.decode(&f.node, &raw, coerce, cx)?);
                    } else {
                        if f.required {
                            cx.fail("missing", "Required");
                        }
                        values.push(None);
                    }
                    cx.path.pop();
                }
                if cx.issues.len() > before {
                    return Ok(None);
                }
                Ok(Some(build_object(py, def, values)?))
            }
        }
    }
}

/// The index of the literal the scalar at the cursor equals (with its exact JSON type).
fn json_literal(p: &mut Parser<'_>, values: &[Lit]) -> JResult<Option<usize>> {
    let token = match p.peek()? {
        b'"' => Lit::Str(p.string()?.into_owned()),
        b't' | b'f' | b'n' => match p.keyword()? {
            Some(b) => Lit::Bool(b),
            None => Lit::None,
        },
        b'[' | b'{' => {
            p.skip()?;
            return Ok(None);
        }
        b'N' | b'I' => return Err(Fail::Unsupported),
        _ => match p.number()? {
            Num::Int(i) => Lit::Int(i),
            Num::Float(f) => Lit::Float(f),
            Num::Big(_) => return Ok(None),
        },
    };
    Ok(values.iter().position(|v| *v == token))
}
