use fs2::FileExt;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
};

include!(concat!(env!("OUT_DIR"), "/embedded-sidecar.rs"));

pub(super) fn executable() -> io::Result<PathBuf> {
    let root = dirs::cache_dir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "No user cache directory"))?
        .join("gl.legendary.desktop")
        .join("backend");
    extract(&root, TARGET, PAYLOAD, SHA256)
}

fn verified(path: &Path, length: u64, digest: &str) -> io::Result<bool> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if !metadata.is_file() || metadata.len() != length {
        return Ok(false);
    }
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()) == digest)
}

fn extract(root: &Path, target: &str, payload: &[u8], digest: &str) -> io::Result<PathBuf> {
    let directory = root.join(target).join(digest);
    fs::create_dir_all(&directory)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?;
    }
    let path = directory.join(if target.contains("windows") {
        "legendary-sidecar.exe"
    } else {
        "legendary-sidecar"
    });
    // One writer per payload, including across desktop processes. The OS releases
    // this lock after a crash; old versions use different directories and remain usable.
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(directory.join("extract.lock"))?;
    lock.lock_exclusive()?;
    if !verified(&path, payload.len() as u64, digest)? {
        let mut temporary = tempfile::NamedTempFile::new_in(&directory)?;
        temporary.write_all(payload)?;
        temporary.as_file().sync_all()?;
        if !verified(temporary.path(), payload.len() as u64, digest)? {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Sidecar checksum mismatch",
            ));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            temporary
                .as_file()
                .set_permissions(fs::Permissions::from_mode(0o700))?;
        }
        // Atomic replacement: never execute a partially written service.
        temporary.persist(&path).map_err(|error| error.error)?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Session;
    use serde_json::json;
    use std::{process::Command, sync::Arc, thread};

    fn digest(bytes: &[u8]) -> String {
        format!("{:x}", Sha256::digest(bytes))
    }

    #[test]
    fn extracts_reuses_and_repairs_the_cached_payload() {
        let root = tempfile::tempdir().unwrap();
        let payload = b"service version one";
        let checksum = digest(payload);
        let path = extract(root.path(), "test-windows", payload, &checksum).unwrap();
        assert_eq!(fs::read(&path).unwrap(), payload);
        let modified = fs::metadata(&path).unwrap().modified().unwrap();
        assert_eq!(
            extract(root.path(), "test-windows", payload, &checksum).unwrap(),
            path
        );
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), modified);
        // Same length but different content must also be detected and replaced.
        fs::write(&path, b"service version bad").unwrap();
        extract(root.path(), "test-windows", payload, &checksum).unwrap();
        assert_eq!(fs::read(&path).unwrap(), payload);
        fs::remove_file(&path).unwrap();
        extract(root.path(), "test-windows", payload, &checksum).unwrap();
        assert_eq!(fs::read(&path).unwrap(), payload);
    }

    #[test]
    fn concurrent_extraction_publishes_one_complete_payload() {
        let root = tempfile::tempdir().unwrap();
        let payload = Arc::new(vec![42; 2 * 1024 * 1024]);
        let threads: Vec<_> = (0..8)
            .map(|_| {
                let root = root.path().to_owned();
                let payload = Arc::clone(&payload);
                thread::spawn(move || extract(&root, "test", &payload, &digest(&payload)).unwrap())
            })
            .collect();
        let paths: Vec<_> = threads
            .into_iter()
            .map(|thread| thread.join().unwrap())
            .collect();
        assert!(paths.iter().all(|path| path == &paths[0]));
        assert_eq!(fs::read(&paths[0]).unwrap(), *payload);
        assert_eq!(fs::read_dir(paths[0].parent().unwrap()).unwrap().count(), 2);
    }

    #[test]
    fn updates_keep_the_previous_payload_intact() {
        let root = tempfile::tempdir().unwrap();
        let old = extract(root.path(), "test", b"old", &digest(b"old")).unwrap();
        let new = extract(root.path(), "test", b"new", &digest(b"new")).unwrap();
        assert_ne!(old, new);
        assert_eq!(fs::read(old).unwrap(), b"old");
        assert_eq!(fs::read(new).unwrap(), b"new");
    }

    #[test]
    fn invalid_payload_is_never_published() {
        let root = tempfile::tempdir().unwrap();
        let checksum = digest(b"different bytes");
        let error = extract(root.path(), "test", b"corrupt", &checksum).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        let directory = root.path().join("test").join(checksum);
        assert!(!directory.join("legendary-sidecar").exists());
        assert_eq!(fs::read_dir(directory).unwrap().count(), 1);
    }

    #[test]
    fn unwritable_cache_returns_an_error() {
        let root = tempfile::tempdir().unwrap();
        let blocked = root.path().join("file-instead-of-directory");
        fs::write(&blocked, b"keep").unwrap();
        assert!(extract(&blocked, "test", b"service", &digest(b"service")).is_err());
        assert_eq!(fs::read(blocked).unwrap(), b"keep");
    }

    #[tokio::test]
    async fn embedded_service_runs_from_an_isolated_directory() {
        let root = tempfile::tempdir().unwrap();
        let path = extract(root.path(), TARGET, PAYLOAD, SHA256).unwrap();
        let working = root.path().join("empty-working-directory");
        fs::create_dir(&working).unwrap();
        let mut command = Command::new(path);
        command
            .current_dir(working)
            .env("LEGENDARY_CONFIG_PATH", root.path().join("config"))
            .env_remove("LEGENDARY_DESKTOP_PYTHON");
        let session = Session::spawn(command).unwrap();
        session.ensure_ready().await.unwrap();
        assert_eq!(
            session.request("auth.status", json!({})).await.unwrap()["hasCredentials"],
            false
        );
        assert_eq!(
            session
                .request("library.installed", json!({}))
                .await
                .unwrap()["games"],
            json!([])
        );
        session.stop();
        assert!(!session.alive.load(std::sync::atomic::Ordering::Acquire));
    }
}
