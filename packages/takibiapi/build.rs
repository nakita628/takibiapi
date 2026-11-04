//! `cargo test` links the crate against libpython (maturin builds the extension module without
//! it). uv-managed Pythons keep libpython outside the loader's search path, so embed its
//! directory as an rpath for those local builds. Wheels built by maturin are left untouched.

fn main() {
    println!("cargo:rerun-if-env-changed=PYO3_BUILD_EXTENSION_MODULE");
    if std::env::var_os("PYO3_BUILD_EXTENSION_MODULE").is_some() {
        return;
    }
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        return; // no rpath; Windows finds pythonXY.dll via PATH
    }
    if let Some(lib_dir) = pyo3_build_config::get().lib_dir() {
        println!("cargo:rustc-link-arg=-Wl,-rpath,{lib_dir}");
    }
}
