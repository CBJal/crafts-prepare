//! Optional inspector around telemetry-parser 0.2.6.
//!
//! The crate is MIT OR Apache-2.0, copyright Adrian Eddy. This binary does not
//! copy Gyroflow. A parse result is not a claim that a camera is supported.

use std::env;
use std::fs::File;
use std::io::{Seek, SeekFrom};
use std::process::ExitCode;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

const VERSION: &str = "0.2.6";
const MAX_BYTES: u64 = 2 * 1024 * 1024 * 1024;

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let Some(path) = args.next() else {
        eprintln!("usage: crafts-telemetry [--version] <media-file>");
        return ExitCode::from(2);
    };
    if path == "--version" || path == "-V" {
        println!("crafts-telemetry telemetry-parser {VERSION}");
        return ExitCode::SUCCESS;
    }
    if args.next().is_some() {
        eprintln!("one file path only");
        return ExitCode::from(2);
    }

    let mut file = match File::open(&path) {
        Ok(file) => file,
        Err(error) => {
            eprintln!("open failed: {error}");
            return ExitCode::from(1);
        }
    };
    let size = match file.metadata() {
        Ok(meta) => meta.len(),
        Err(error) => {
            eprintln!("stat failed: {error}");
            return ExitCode::from(1);
        }
    };
    if size == 0 || size > MAX_BYTES {
        eprintln!("file is empty or larger than the 2 GiB parse limit");
        return ExitCode::from(1);
    }
    if file.seek(SeekFrom::Start(0)).is_err() {
        eprintln!("seek failed");
        return ExitCode::from(1);
    }

    let cancel = Arc::new(AtomicBool::new(false));
    match telemetry_parser::Input::from_stream(&mut file, size as usize, &path, |_| {}, cancel) {
        Ok(input) => {
            let sample_groups = input.samples.as_ref().map(|items| items.len()).unwrap_or(0);
            let payload = serde_json::json!({
                "parsed": true,
                "parser": "telemetry-parser",
                "parser_version": VERSION,
                "license": "MIT OR Apache-2.0",
                "camera_type": input.camera_type(),
                "camera_model": input.camera_model(),
                "sample_groups": sample_groups,
                "has_samples": sample_groups > 0,
                "disclaimer": "Parsed with telemetry-parser 0.2.6. This does not certify camera support. Missing samples are not proof that the file lacks motion metadata."
            });
            println!("{payload}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            let payload = serde_json::json!({
                "parsed": false,
                "parser": "telemetry-parser",
                "parser_version": VERSION,
                "detail": error.to_string(),
                "disclaimer": "telemetry-parser did not accept this file. That is not proof of missing gyro metadata and it is not a camera-support result."
            });
            println!("{payload}");
            ExitCode::from(1)
        }
    }
}
