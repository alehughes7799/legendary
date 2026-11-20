//! Headless harness for running the bridge integration suite through the embedded service.

use legendary_transport::Backend;
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let backend = Backend::default();
    for line in io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let Ok(request) = serde_json::from_str::<Value>(&line) else {
            break;
        };
        let Some(method) = request["method"].as_str() else {
            break;
        };
        let id = request["id"].clone();
        let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
        let response = match backend.request(method, params).await {
            Ok(result) => json!({"id": id, "result": result}),
            Err(error) => json!({"id": id, "error": error}),
        };
        println!("{response}");
        io::stdout().flush().unwrap();
    }
    backend.stop();
}
