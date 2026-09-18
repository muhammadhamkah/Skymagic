//! Event persistence: newline-delimited JSON (one `MarketEvent` per line).
//!
//! NDJSON is deliberately simple and inspectable for a first harness — easy to
//! `grep`, `wc -l`, or load into pandas. It is behind a thin API so we can swap
//! in a columnar format (parquet) later without touching the rest of the code.

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::Path;
use std::sync::mpsc::{self, Sender};
use std::thread::{self, JoinHandle};

use anyhow::{Context, Result};

use crate::events::MarketEvent;

/// Append-only NDJSON writer running on its own thread, so disk writes never
/// block the async network loop. Send events with [`Recorder::record`]; call
/// [`Recorder::finish`] to flush and join.
pub struct Recorder {
    tx: Option<Sender<MarketEvent>>,
    handle: Option<JoinHandle<Result<u64>>>,
}

impl Recorder {
    pub fn create(path: impl AsRef<Path>) -> Result<Self> {
        if let Some(parent) = path.as_ref().parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)
                    .with_context(|| format!("creating data dir {}", parent.display()))?;
            }
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path.as_ref())
            .with_context(|| format!("opening {}", path.as_ref().display()))?;

        let (tx, rx) = mpsc::channel::<MarketEvent>();
        let handle = thread::spawn(move || -> Result<u64> {
            let mut w = BufWriter::new(file);
            let mut n = 0u64;
            for ev in rx {
                serde_json::to_writer(&mut w, &ev)?;
                w.write_all(b"\n")?;
                n += 1;
                // Periodic flush bounds data loss on an ungraceful stop without
                // paying a syscall per event.
                if n % 1000 == 0 {
                    w.flush()?;
                }
            }
            w.flush()?;
            Ok(n)
        });

        Ok(Self {
            tx: Some(tx),
            handle: Some(handle),
        })
    }

    pub fn record(&self, ev: MarketEvent) {
        if let Some(tx) = &self.tx {
            // The writer thread only dies on a poisoned file handle; dropping
            // here just means we stop persisting, which the caller will notice
            // at `finish`.
            let _ = tx.send(ev);
        }
    }

    /// Flush and return the number of events written.
    pub fn finish(mut self) -> Result<u64> {
        drop(self.tx.take());
        match self.handle.take() {
            Some(h) => h.join().expect("recorder thread panicked"),
            None => Ok(0),
        }
    }
}

/// Lazily read events back from an NDJSON file, in stored order.
pub struct EventReader {
    lines: std::io::Lines<BufReader<File>>,
}

impl EventReader {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let file = File::open(path.as_ref())
            .with_context(|| format!("opening {}", path.as_ref().display()))?;
        Ok(Self {
            lines: BufReader::new(file).lines(),
        })
    }
}

impl Iterator for EventReader {
    type Item = Result<MarketEvent>;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            match self.lines.next()? {
                Ok(line) if line.trim().is_empty() => continue,
                Ok(line) => {
                    return Some(
                        serde_json::from_str::<MarketEvent>(&line)
                            .context("parsing NDJSON event line"),
                    )
                }
                Err(e) => return Some(Err(e.into())),
            }
        }
    }
}
