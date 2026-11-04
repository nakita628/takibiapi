//! JSON output, byte for byte what ``json.dumps(value, ensure_ascii=False, separators=(",", ":"))``
//! writes. Values it cannot write the same way (non-finite floats, lone surrogates, non-``str``
//! keys, unknown types) are ``Unsupported``: the caller then falls back to Python.

use std::fmt::Write as _;

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

use crate::env::{env, Kind};
use crate::json::{Fail, JResult};

const MAX_DEPTH: usize = 512;

fn write_str(out: &mut Vec<u8>, s: &str) {
    out.push(b'"');
    let bytes = s.as_bytes();
    let mut run = 0;
    for (i, &c) in bytes.iter().enumerate() {
        let escape: &[u8] = match c {
            b'"' => b"\\\"",
            b'\\' => b"\\\\",
            b'\n' => b"\\n",
            b'\r' => b"\\r",
            b'\t' => b"\\t",
            0x08 => b"\\b",
            0x0c => b"\\f",
            0..=0x1f => b"",
            _ => continue,
        };
        out.extend_from_slice(&bytes[run..i]);
        if escape.is_empty() {
            let _ = write!(Hex(out), "\\u{c:04x}");
        } else {
            out.extend_from_slice(escape);
        }
        run = i + 1;
    }
    out.extend_from_slice(&bytes[run..]);
    out.push(b'"');
}

struct Hex<'a>(&'a mut Vec<u8>);

impl std::fmt::Write for Hex<'_> {
    fn write_str(&mut self, s: &str) -> std::fmt::Result {
        self.0.extend_from_slice(s.as_bytes());
        Ok(())
    }
}

/// ``repr(float)``: the shortest digits that round-trip, in fixed notation when the decimal
/// exponent is in ``(-4, 16]`` and scientific notation (``1e+16``, ``1e-05``) otherwise.
fn write_float(out: &mut Vec<u8>, value: f64) -> JResult<()> {
    if !value.is_finite() {
        return Err(Fail::Unsupported); // json.dumps writes NaN / Infinity (not JSON)
    }
    // ``{:e}`` gives the shortest round-tripping length; when several strings of that length
    // round-trip, Python picks the one closest to the value, which correct rounding to that many
    // digits (``{:.Ne}``) gives.
    let shortest = format!("{value:e}"); // e.g. "-1.5e-7", "1e16", "0e0"
    let n_digits = shortest
        .split_once('e')
        .map_or(1, |(m, _)| m.bytes().filter(u8::is_ascii_digit).count());
    let sci = format!("{value:.prec$e}", prec = n_digits - 1);
    let (mantissa, exp) = sci.split_once('e').expect("{:e} has an exponent");
    let exp: i32 = exp.parse().expect("{:e} exponent is an integer");
    let (negative, mantissa) = mantissa.strip_prefix('-').map_or((false, mantissa), |m| (true, m));
    let digits: String = mantissa.chars().filter(|&c| c != '.').collect();
    let decpt = exp + 1; // the value is 0.<digits> * 10^decpt
    if negative {
        out.push(b'-');
    }
    let n = digits.len() as i32;
    if -4 < decpt && decpt <= 16 {
        if decpt <= 0 {
            out.extend_from_slice(b"0.");
            out.extend(std::iter::repeat_n(b'0', (-decpt) as usize));
            out.extend_from_slice(digits.as_bytes());
        } else if decpt >= n {
            out.extend_from_slice(digits.as_bytes());
            out.extend(std::iter::repeat_n(b'0', (decpt - n) as usize));
            out.extend_from_slice(b".0");
        } else {
            out.extend_from_slice(&digits.as_bytes()[..decpt as usize]);
            out.push(b'.');
            out.extend_from_slice(&digits.as_bytes()[decpt as usize..]);
        }
    } else {
        out.push(digits.as_bytes()[0]);
        if n > 1 {
            out.push(b'.');
            out.extend_from_slice(&digits.as_bytes()[1..]);
        }
        let e = decpt - 1;
        let _ = write!(Hex(out), "e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs());
    }
    Ok(())
}

fn write_int(out: &mut Vec<u8>, value: &Bound<'_, PyAny>) -> JResult<()> {
    if let Ok(i) = value.extract::<i64>() {
        let _ = write!(Hex(out), "{i}");
    } else {
        // Beyond i64 (or an int subclass): int.__repr__, as json does.
        let text = env(value.py())?
            .int_
            .bind(value.py())
            .getattr("__repr__")?
            .call1((value,))?;
        out.extend_from_slice(text.cast::<PyString>().map_err(PyErr::from)?.to_str()?.as_bytes());
    }
    Ok(())
}

fn write_py_str(out: &mut Vec<u8>, value: &Bound<'_, PyAny>) -> JResult<()> {
    let s = value.cast::<PyString>().map_err(PyErr::from)?;
    // to_str fails for lone surrogates, which json writes as \udXXX: leave those to Python.
    let s = s.to_str().map_err(|_| Fail::Unsupported)?;
    write_str(out, s);
    Ok(())
}

/// Any value, as ``json.dumps(value, default=takibiapi.schema.to_json)`` writes it. ``default``
/// (``json.dumps``'s): called for the objects nothing else writes, and its result written instead.
pub fn write_any(
    out: &mut Vec<u8>,
    value: &Bound<'_, PyAny>,
    depth: usize,
    default: Option<&Bound<'_, PyAny>>,
) -> JResult<()> {
    if depth > MAX_DEPTH {
        return Err(Fail::Unsupported); // json raises for cycles / runaway nesting
    }
    let py = value.py();
    if value.is_instance_of::<PyString>() {
        return write_py_str(out, value);
    }
    if value.is_none() {
        out.extend_from_slice(b"null");
        return Ok(());
    }
    if value.is_instance_of::<PyBool>() {
        out.extend_from_slice(if value.is_truthy()? { b"true" } else { b"false" });
        return Ok(());
    }
    if value.is_instance_of::<PyInt>() {
        return write_int(out, value);
    }
    if value.is_instance_of::<PyFloat>() {
        return write_float(out, value.extract::<f64>()?);
    }
    if value.is_instance_of::<PyList>() || value.is_instance_of::<PyTuple>() {
        out.push(b'[');
        for (i, item) in value.try_iter()?.enumerate() {
            if i > 0 {
                out.push(b',');
            }
            write_any(out, &item?, depth + 1, default)?;
        }
        out.push(b']');
        return Ok(());
    }
    if let Ok(dict) = value.cast::<PyDict>() {
        out.push(b'{');
        for (i, (k, v)) in dict.iter().enumerate() {
            if i > 0 {
                out.push(b',');
            }
            if !k.is_instance_of::<PyString>() {
                return Err(Fail::Unsupported); // json turns int / float / bool keys into text
            }
            write_py_str(out, &k)?;
            out.push(b':');
            write_any(out, &v, depth + 1, default)?;
        }
        out.push(b'}');
        return Ok(());
    }
    // json's ``default`` (takibiapi.schema.to_json) for everything else, by the kind of the type
    // (decided once per type: the isinstance checks, Mapping's above all, are slow).
    let e = env(py)?;
    match e.kind(value)? {
        Kind::Dataclass => {
            if let Some(names) = e.dataclass_names(value)? {
                out.push(b'{');
                for (i, name) in names.iter().enumerate() {
                    if i > 0 {
                        out.push(b',');
                    }
                    write_py_str(out, &name)?;
                    out.push(b':');
                    write_any(
                        out,
                        &value.getattr(name.cast::<PyString>().map_err(PyErr::from)?)?,
                        depth + 1,
                        default,
                    )?;
                }
                out.push(b'}');
                return Ok(());
            }
        }
        Kind::Enum => return write_any(out, &value.getattr("value")?, depth + 1, default),
        Kind::Iso => return write_py_str(out, &value.call_method0("isoformat")?),
        Kind::Uuid => return write_py_str(out, &value.str()?.into_any()),
        Kind::Set => {
            out.push(b'[');
            for (i, item) in value.try_iter()?.enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                write_any(out, &item?, depth + 1, default)?;
            }
            out.push(b']');
            return Ok(());
        }
        Kind::Mapping => {
            let dict = PyDict::new(py);
            for item in value.call_method0("items")?.try_iter()? {
                let (k, v): (Bound<'_, PyAny>, Bound<'_, PyAny>) = item?.extract()?;
                dict.set_item(k.str()?, v)?;
            }
            return write_any(out, &dict.into_any(), depth + 1, default);
        }
        Kind::Other => {}
    }
    if let Some(default) = default {
        let replaced = default.call1((value,))?;
        if replaced.is(value) {
            return Err(Fail::Unsupported); // json would recurse forever
        }
        return write_any(out, &replaced, depth + 1, Some(default));
    }
    Err(Fail::Unsupported)
}
