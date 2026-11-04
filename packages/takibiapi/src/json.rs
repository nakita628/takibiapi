//! A JSON reader that gives the same values as Python's ``json.loads``. Input it cannot read the
//! same way (``NaN`` / ``Infinity``, lone surrogates, a BOM or UTF-16 / 32) is ``Unsupported``:
//! the caller then falls back to Python.

use std::borrow::Cow;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyFloat, PyList, PyString};

use crate::env::env;

#[derive(Debug)]
pub enum Fail {
    Malformed(usize),
    Unsupported,
    Py(PyErr),
}

impl From<PyErr> for Fail {
    fn from(err: PyErr) -> Self {
        Fail::Py(err)
    }
}

pub type JResult<T> = Result<T, Fail>;

pub enum Num<'a> {
    Int(i64),
    Big(&'a str), // an integer beyond i64
    Float(f64),
}

impl Num<'_> {
    pub fn type_name(&self) -> &'static str {
        match self {
            Num::Float(_) => "float",
            _ => "int",
        }
    }

    pub fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match self {
            Num::Int(i) => Ok(i.into_pyobject(py)?.into_any()),
            Num::Big(s) => env(py)?.int_.bind(py).call1((*s,)),
            Num::Float(f) => Ok(PyFloat::new(py, *f).into_any()),
        }
    }
}

#[derive(Debug)]
pub struct Parser<'a> {
    b: &'a [u8],
    pub i: usize,
}

impl<'a> Parser<'a> {
    pub fn new(b: &'a [u8]) -> JResult<Self> {
        // Python detects UTF-16 / 32 by NUL bytes and accepts a UTF-8 BOM: leave those to it.
        if b.starts_with(&[0xEF, 0xBB, 0xBF]) || b.iter().take(4).any(|&c| c == 0) {
            return Err(Fail::Unsupported);
        }
        Ok(Parser { b, i: 0 })
    }

    pub fn malformed<T>(&self) -> JResult<T> {
        Err(Fail::Malformed(self.i))
    }

    pub fn ws(&mut self) {
        while let Some(&c) = self.b.get(self.i) {
            if c == b' ' || c == b'\t' || c == b'\n' || c == b'\r' {
                self.i += 1;
            } else {
                break;
            }
        }
    }

    /// The first byte of the next value (after whitespace).
    pub fn peek(&mut self) -> JResult<u8> {
        self.ws();
        match self.b.get(self.i) {
            Some(&c) => Ok(c),
            None => self.malformed(),
        }
    }

    pub fn expect(&mut self, c: u8) -> JResult<()> {
        if self.peek()? == c {
            self.i += 1;
            Ok(())
        } else {
            self.malformed()
        }
    }

    pub fn end(&mut self) -> JResult<()> {
        self.ws();
        if self.i == self.b.len() {
            Ok(())
        } else {
            self.malformed()
        }
    }

    fn literal(&mut self, word: &[u8]) -> JResult<()> {
        if self.b[self.i..].starts_with(word) {
            self.i += word.len();
            Ok(())
        } else {
            self.malformed()
        }
    }

    /// ``true`` / ``false`` / ``null`` at the cursor (already peeked).
    pub fn keyword(&mut self) -> JResult<Option<bool>> {
        match self.b[self.i] {
            b't' => self.literal(b"true").map(|()| Some(true)),
            b'f' => self.literal(b"false").map(|()| Some(false)),
            b'n' => self.literal(b"null").map(|()| None),
            _ => self.malformed(),
        }
    }

    pub fn number(&mut self) -> JResult<Num<'a>> {
        let start = self.i;
        let b = self.b;
        let mut i = self.i;
        if b.get(i) == Some(&b'-') {
            i += 1;
        }
        if b.get(i) == Some(&b'I') {
            return Err(Fail::Unsupported); // -Infinity
        }
        match b.get(i) {
            Some(b'0') => i += 1,
            Some(c) if c.is_ascii_digit() => {
                while b.get(i).is_some_and(u8::is_ascii_digit) {
                    i += 1;
                }
            }
            _ => {
                self.i = i;
                return self.malformed();
            }
        }
        let mut is_float = false;
        if b.get(i) == Some(&b'.') && b.get(i + 1).is_some_and(u8::is_ascii_digit) {
            is_float = true;
            i += 1;
            while b.get(i).is_some_and(u8::is_ascii_digit) {
                i += 1;
            }
        }
        if matches!(b.get(i), Some(b'e' | b'E')) {
            let mut j = i + 1;
            if matches!(b.get(j), Some(b'+' | b'-')) {
                j += 1;
            }
            if b.get(j).is_some_and(u8::is_ascii_digit) {
                is_float = true;
                i = j;
                while b.get(i).is_some_and(u8::is_ascii_digit) {
                    i += 1;
                }
            }
        }
        self.i = i;
        // ASCII digits only, so this is valid UTF-8.
        let text = std::str::from_utf8(&b[start..i]).map_err(|_| Fail::Malformed(start))?;
        if is_float {
            return text.parse::<f64>().map(Num::Float).map_err(|_| Fail::Malformed(start));
        }
        Ok(text.parse::<i64>().map_or(Num::Big(text), Num::Int))
    }

    /// A string at the cursor (already peeked as ``"``); borrowed when it has no escapes.
    pub fn string(&mut self) -> JResult<Cow<'a, str>> {
        self.i += 1;
        let start = self.i;
        let b = self.b;
        loop {
            match b.get(self.i) {
                Some(b'"') => {
                    let s = std::str::from_utf8(&b[start..self.i]).map_err(|_| Fail::Malformed(start))?;
                    self.i += 1;
                    return Ok(Cow::Borrowed(s));
                }
                Some(b'\\') => break,
                Some(&c) if c < 0x20 => return self.malformed(),
                Some(_) => self.i += 1,
                None => return self.malformed(),
            }
        }
        let mut out = std::str::from_utf8(&b[start..self.i])
            .map_err(|_| Fail::Malformed(start))?
            .to_owned();
        let mut run = self.i;
        loop {
            match b.get(self.i) {
                Some(b'"') => {
                    out.push_str(std::str::from_utf8(&b[run..self.i]).map_err(|_| Fail::Malformed(run))?);
                    self.i += 1;
                    return Ok(Cow::Owned(out));
                }
                Some(b'\\') => {
                    out.push_str(std::str::from_utf8(&b[run..self.i]).map_err(|_| Fail::Malformed(run))?);
                    self.i += 1;
                    let c = match b.get(self.i) {
                        Some(b'"') => '"',
                        Some(b'\\') => '\\',
                        Some(b'/') => '/',
                        Some(b'b') => '\u{8}',
                        Some(b'f') => '\u{c}',
                        Some(b'n') => '\n',
                        Some(b'r') => '\r',
                        Some(b't') => '\t',
                        Some(b'u') => {
                            self.i += 1;
                            let hi = self.hex4()?;
                            let code = if (0xD800..0xDC00).contains(&hi) {
                                if b.get(self.i) == Some(&b'\\') && b.get(self.i + 1) == Some(&b'u') {
                                    self.i += 2;
                                    let lo = self.hex4()?;
                                    if !(0xDC00..0xE000).contains(&lo) {
                                        return Err(Fail::Unsupported); // a lone surrogate
                                    }
                                    0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00)
                                } else {
                                    return Err(Fail::Unsupported);
                                }
                            } else if (0xDC00..0xE000).contains(&hi) {
                                return Err(Fail::Unsupported);
                            } else {
                                hi
                            };
                            out.push(char::from_u32(code).ok_or(Fail::Unsupported)?);
                            run = self.i;
                            continue;
                        }
                        _ => return self.malformed(),
                    };
                    out.push(c);
                    self.i += 1;
                    run = self.i;
                }
                Some(&c) if c < 0x20 => return self.malformed(),
                Some(_) => self.i += 1,
                None => return self.malformed(),
            }
        }
    }

    fn hex4(&mut self) -> JResult<u32> {
        let digits = self.b.get(self.i..self.i + 4).ok_or(Fail::Malformed(self.i))?;
        let mut v = 0;
        for &d in digits {
            v = v * 16 + (d as char).to_digit(16).ok_or(Fail::Malformed(self.i))?;
        }
        self.i += 4;
        Ok(v)
    }

    /// Calls ``each`` for every element of the array at the cursor (already peeked as ``[``).
    pub fn array(&mut self, mut each: impl FnMut(&mut Self, usize) -> JResult<()>) -> JResult<()> {
        self.i += 1;
        if self.peek()? == b']' {
            self.i += 1;
            return Ok(());
        }
        let mut n = 0;
        loop {
            each(self, n)?;
            n += 1;
            match self.peek()? {
                b',' => self.i += 1,
                b']' => {
                    self.i += 1;
                    return Ok(());
                }
                _ => return self.malformed(),
            }
        }
    }

    /// Calls ``each`` with every key of the object at the cursor (already peeked as ``{``); it
    /// must read the value.
    pub fn object(&mut self, mut each: impl FnMut(&mut Self, Cow<'a, str>) -> JResult<()>) -> JResult<()> {
        self.i += 1;
        if self.peek()? == b'}' {
            self.i += 1;
            return Ok(());
        }
        loop {
            if self.peek()? != b'"' {
                return self.malformed();
            }
            let key = self.string()?;
            self.expect(b':')?;
            each(self, key)?;
            match self.peek()? {
                b',' => self.i += 1,
                b'}' => {
                    self.i += 1;
                    return Ok(());
                }
                _ => return self.malformed(),
            }
        }
    }

    /// Reads (and checks the syntax of) one value without building it.
    pub fn skip(&mut self) -> JResult<()> {
        match self.peek()? {
            b'"' => self.string().map(drop),
            b'[' => self.array(|p, _| p.skip()),
            b'{' => self.object(|p, _| p.skip()),
            b't' | b'f' | b'n' => self.keyword().map(drop),
            b'N' | b'I' => Err(Fail::Unsupported),
            _ => self.number().map(drop),
        }
    }

    /// The Python type name of the value at the cursor, then skips it (for type errors).
    pub fn skip_named(&mut self) -> JResult<&'static str> {
        let name = match self.peek()? {
            b'"' => "str",
            b'[' => "list",
            b'{' => "dict",
            b't' | b'f' => "bool",
            b'n' => "null",
            b'N' | b'I' => return Err(Fail::Unsupported),
            _ => return Ok(self.number()?.type_name()),
        };
        self.skip()?;
        Ok(name)
    }

    /// Builds any value, as ``json.loads`` would.
    pub fn any<'py>(&mut self, py: Python<'py>) -> JResult<Bound<'py, PyAny>> {
        Ok(match self.peek()? {
            b'"' => PyString::new(py, &self.string()?).into_any(),
            b'[' => {
                let list = PyList::empty(py);
                self.array(|p, _| {
                    list.append(p.any(py)?)?;
                    Ok(())
                })?;
                list.into_any()
            }
            b'{' => {
                let dict = PyDict::new(py);
                self.object(|p, key| {
                    let value = p.any(py)?;
                    dict.set_item(PyString::new(py, &key), value)?;
                    Ok(())
                })?;
                dict.into_any()
            }
            b't' | b'f' | b'n' => match self.keyword()? {
                Some(v) => pyo3::types::PyBool::new(py, v).to_owned().into_any(),
                None => py.None().into_bound(py),
            },
            b'N' | b'I' => return Err(Fail::Unsupported),
            _ => self.number()?.to_py(py)?,
        })
    }
}
