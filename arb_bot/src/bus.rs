use tokio::sync::broadcast;

use crate::events::PriceEvent;

pub type EventSender = broadcast::Sender<PriceEvent>;
pub type EventReceiver = broadcast::Receiver<PriceEvent>;

pub fn new_bus(capacity: usize) -> EventSender {
    let (tx, _rx) = broadcast::channel(capacity);
    tx
}
