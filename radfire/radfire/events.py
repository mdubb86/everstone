"""Event queue for CalDAV changes"""

import queue
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class EventType(Enum):
    """Types of CalDAV events"""
    UPLOAD = "upload"
    DELETE = "delete"
    MOVE = "move"
    COLLECTION_CREATED = "collection_created"
    COLLECTION_DELETED = "collection_deleted"


@dataclass
class CalDAVEvent:
    """Event emitted by CalDAV storage operations"""
    type: EventType
    timestamp: datetime
    collection: Optional[str] = None
    href: Optional[str] = None
    uid: Optional[str] = None
    item_data: Optional[str] = None
    from_collection: Optional[str] = None
    to_collection: Optional[str] = None
    from_href: Optional[str] = None
    to_href: Optional[str] = None
    display_name: Optional[str] = None

    def __repr__(self) -> str:
        return f"CalDAVEvent({self.type.value}, collection={self.collection}, href={self.href})"


# Thread-safe queue - works across threads (Radicale thread -> async consumer)
caldav_event_queue: queue.Queue[CalDAVEvent] = queue.Queue(maxsize=1000)


def get_event_queue() -> queue.Queue[CalDAVEvent]:
    """Get the global event queue"""
    return caldav_event_queue
