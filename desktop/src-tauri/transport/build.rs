fn main() {
    #[cfg(feature = "embedded-sidecar")]
    embed_sidecar();
}

#[cfg(feature = "embedded-sidecar")]
fn embed_sidecar() {
    use sha2::{Digest, Sha256};
    use std::{env, fs, path::PathBuf};

    let target = env::var("TARGET").unwrap();
    let extension = if target.contains("windows") {
        ".exe"
    } else {
        ""
    };
    let source = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .join("../binaries")
        .join(format!("legendary-sidecar-{target}{extension}"));
    println!("cargo:rerun-if-changed={}", source.display());
    println!("cargo:rerun-if-changed=build.rs");
    let payload = fs::read(&source).unwrap_or_else(|error| {
        panic!(
            "Missing sidecar {} ({error}). Run pnpm sidecar:build --target {target} first.",
            source.display()
        )
    });
    assert!(!payload.is_empty(), "The sidecar cannot be empty.");
    if target.contains("windows") {
        assert!(
            payload.starts_with(b"MZ"),
            "The Windows sidecar must be a Windows executable."
        );
    }
    let digest = format!("{:x}", Sha256::digest(&payload));
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    // Compile and hash the same immutable snapshot, even if the source is rebuilt.
    let snapshot = out.join("embedded-sidecar.bin");
    fs::write(&snapshot, payload).unwrap();
    fs::write(out.join("embedded-sidecar.rs"), format!(
        "const PAYLOAD: &[u8] = include_bytes!({snapshot:?});\nconst SHA256: &str = {digest:?};\nconst TARGET: &str = {target:?};\n",
    )).unwrap();
}
