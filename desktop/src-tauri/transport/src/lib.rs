use serde::Serialize;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Read, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tokio::sync::{oneshot, OnceCell};

#[cfg(feature = "embedded-sidecar")]
mod embedded;

const MAX_RESPONSE_BYTES: u64 = 8 * 1024 * 1024;
type Reply = Result<Value, BackendError>;

#[derive(Clone, Debug, Serialize)]
pub struct BackendError {
    code: String,
    message: String,
}

impl BackendError {
    fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

struct Session {
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Reply>>>,
    next_id: AtomicU64,
    alive: AtomicBool,
    ready: OnceCell<()>,
}

impl Session {
    fn start() -> Result<Arc<Self>, BackendError> {
        Self::spawn(sidecar_command()?)
    }

    fn spawn(mut command: Command) -> Result<Arc<Self>, BackendError> {
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        command.env("PYTHONIOENCODING", "utf-8");
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW, keeping IPC pipes
        }
        let mut child = command.spawn().map_err(|_| {
            BackendError::new(
                "BACKEND_UNAVAILABLE",
                "Could not start the bundled game service.",
            )
        })?;
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let session = Arc::new(Self {
            stdin: Mutex::new(child.stdin.take()),
            child: Mutex::new(child),
            pending: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            alive: AtomicBool::new(true),
            ready: OnceCell::new(),
        });
        let reader_session = Arc::clone(&session);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut bytes = Vec::new();
                let read = reader
                    .by_ref()
                    .take(MAX_RESPONSE_BYTES + 1)
                    .read_until(b'\n', &mut bytes);
                if read.is_err() || bytes.is_empty() || bytes.len() as u64 > MAX_RESPONSE_BYTES {
                    break;
                }
                let Ok(response) = serde_json::from_slice::<Value>(&bytes) else {
                    break;
                };
                let Some(id) = response.get("id").and_then(Value::as_u64) else {
                    break;
                };
                let reply = if let Some(error) = response.get("error") {
                    Err(BackendError::new(
                        error["code"].as_str().unwrap_or("INTERNAL_ERROR"),
                        error["message"].as_str().unwrap_or("The request failed."),
                    ))
                } else if let Some(result) = response.get("result") {
                    Ok(result.clone())
                } else {
                    break;
                };
                if let Some(sender) = reader_session.pending.lock().unwrap().remove(&id) {
                    let _ = sender.send(reply);
                }
            }
            reader_session.stop();
        });
        // Drain logs so a full stderr pipe cannot deadlock the Python process.
        std::thread::spawn(move || {
            let _ = std::io::copy(&mut BufReader::new(stderr), &mut std::io::stderr());
        });
        Ok(session)
    }

    async fn ensure_ready(&self) -> Result<(), BackendError> {
        self.ready
            .get_or_try_init(|| async {
                let hello = self.request("system.hello", json!({})).await?;
                if hello["protocolVersion"] != 1 {
                    self.stop();
                    return Err(BackendError::new(
                        "PROTOCOL_MISMATCH",
                        "The bundled game service is incompatible.",
                    ));
                }
                Ok(())
            })
            .await
            .copied()
    }

    async fn request(&self, method: &str, params: Value) -> Reply {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = oneshot::channel();
        {
            let mut pending = self.pending.lock().unwrap();
            if !self.alive.load(Ordering::Acquire) {
                return Err(BackendError::new(
                    "BACKEND_EXITED",
                    "The game service disconnected.",
                ));
            }
            if pending.len() >= 16 {
                return Err(BackendError::new(
                    "BACKEND_BUSY",
                    "The game service is busy.",
                ));
            }
            pending.insert(id, sender);
        }
        let mut payload =
            serde_json::to_vec(&json!({"id": id, "method": method, "params": params})).unwrap();
        payload.push(b'\n');
        let write_result = self
            .stdin
            .lock()
            .unwrap()
            .as_mut()
            .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::BrokenPipe, "Backend stopped"))
            .and_then(|stdin| stdin.write_all(&payload));
        if write_result.is_err() {
            self.stop();
        }
        match tokio::time::timeout(Duration::from_secs(600), receiver).await {
            Ok(Ok(reply)) => reply,
            Ok(Err(_)) => Err(BackendError::new(
                "BACKEND_EXITED",
                "The game service disconnected.",
            )),
            Err(_) => {
                self.stop();
                Err(BackendError::new(
                    "BACKEND_TIMEOUT",
                    "The game service timed out.",
                ))
            }
        }
    }

    fn stop(&self) {
        if self.alive.swap(false, Ordering::AcqRel) {
            // EOF lets the sidecar cancel downloads and release shared memory.
            self.stdin.lock().unwrap().take();
            let mut child = self.child.lock().unwrap();
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            while child.try_wait().ok().flatten().is_none() && std::time::Instant::now() < deadline
            {
                std::thread::sleep(Duration::from_millis(50));
            }
            // PyInstaller onefile has a bootloader parent and a Python child.
            // Stop that process tree while detached games keep their own session.
            #[cfg(unix)]
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGKILL);
            }
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                let _ = Command::new("taskkill")
                    .args(["/PID", &child.id().to_string(), "/T", "/F"])
                    .creation_flags(0x08000000)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
            let _ = child.kill();
            let _ = child.wait();
        }
        for (_, sender) in self.pending.lock().unwrap().drain() {
            let _ = sender.send(Err(BackendError::new(
                "BACKEND_EXITED",
                "The game service disconnected.",
            )));
        }
    }
}

#[derive(Default)]
pub struct Backend(Mutex<Option<Arc<Session>>>);

impl Backend {
    pub async fn request(&self, method: &str, params: Value) -> Reply {
        let session = {
            let mut current = self.0.lock().unwrap();
            let fresh = current
                .as_ref()
                .map_or(true, |s| !s.alive.load(Ordering::Acquire));
            if fresh {
                *current = Some(Session::start()?);
            }
            Arc::clone(current.as_ref().unwrap())
        };
        session.ensure_ready().await?;
        session.request(method, params).await
    }

    pub fn stop(&self) {
        if let Some(session) = self.0.lock().unwrap().take() {
            session.stop();
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.stop();
    }
}

fn sidecar_command() -> Result<Command, BackendError> {
    #[cfg(debug_assertions)]
    if let Some(python) = std::env::var_os("LEGENDARY_DESKTOP_PYTHON") {
        let mut command = Command::new(python);
        command.args(["-u", "-m", "legendary.bridge"]);
        command.current_dir(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        return Ok(command);
    }
    #[cfg(feature = "embedded-sidecar")]
    {
        let path = embedded::executable().map_err(|_| {
            BackendError::new(
                "BACKEND_CACHE_ERROR",
                "Could not prepare the bundled game service in the application cache.",
            )
        })?;
        Ok(Command::new(path))
    }
    #[cfg(not(feature = "embedded-sidecar"))]
    external_sidecar_command()
}

#[cfg(not(feature = "embedded-sidecar"))]
fn external_sidecar_command() -> Result<Command, BackendError> {
    let executable = std::env::current_exe().map_err(|_| {
        BackendError::new(
            "BACKEND_UNAVAILABLE",
            "Could not locate the application directory.",
        )
    })?;
    let name = if cfg!(windows) {
        "legendary-sidecar.exe"
    } else {
        "legendary-sidecar"
    };
    Ok(Command::new(executable.parent().unwrap().join(name)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(script: &str) -> Arc<Session> {
        let mut command = Command::new(
            std::env::var("LEGENDARY_TEST_PYTHON").unwrap_or_else(|_| "python3".into()),
        );
        command.args(["-u", "-c", script]);
        Session::spawn(command).unwrap()
    }

    #[tokio::test]
    async fn matches_concurrent_responses_by_id() {
        let session = fixture("import sys,json\nrequests=[json.loads(sys.stdin.readline()) for _ in range(2)]\nfor r in reversed(requests):\n print(json.dumps({'id':r['id'],'result':r['method']}),flush=True)\nsys.stdin.read()\n");
        let (first, second) = tokio::join!(
            session.request("first", json!({})),
            session.request("second", json!({}))
        );
        assert_eq!(first.unwrap(), "first");
        assert_eq!(second.unwrap(), "second");
        session.stop();
    }

    #[tokio::test]
    async fn rejects_incompatible_protocol() {
        let session = fixture("import sys,json\nr=json.loads(sys.stdin.readline())\nprint(json.dumps({'id':r['id'],'result':{'protocolVersion':99}}),flush=True)\nsys.stdin.read()\n");
        assert_eq!(
            session.ensure_ready().await.unwrap_err().code,
            "PROTOCOL_MISMATCH"
        );
        assert!(!session.alive.load(Ordering::Acquire));
    }

    #[tokio::test]
    async fn fails_pending_requests_on_invalid_output() {
        let session = fixture("import sys\nsys.stdin.readline()\nprint('not protocol JSON',flush=True)\nsys.stdin.read()\n");
        assert_eq!(
            session.request("test", json!({})).await.unwrap_err().code,
            "BACKEND_EXITED"
        );
    }

    #[tokio::test]
    async fn shutdown_unblocks_pending_requests() {
        let session = fixture("import sys\nsys.stdin.read()\n");
        let request = session.request("test", json!({}));
        let stop = async {
            tokio::task::yield_now().await;
            session.stop();
        };
        let (reply, _) = tokio::join!(request, stop);
        assert_eq!(reply.unwrap_err().code, "BACKEND_EXITED");
    }
}
