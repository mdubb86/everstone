"""Custom Radicale storage with event emission"""

import queue
from datetime import datetime, timezone
from typing import Optional

import vobject
from radicale.storage.multifilesystem import Collection
from radicale.storage.multifilesystem import Storage as BaseStorage

from .events import CalDAVEvent, EventType, caldav_event_queue


def _emit_event(event: CalDAVEvent) -> None:
    """Emit an event to the queue (non-blocking)"""
    print(f"DEBUG: Emitting event: {event}")
    try:
        caldav_event_queue.put_nowait(event)
        print(f"DEBUG: Event queued, queue size: {caldav_event_queue.qsize()}")
    except queue.Full:
        print(f"Warning: Event queue full, dropped event: {event}")


def _extract_uid(item) -> Optional[str]:
    """Extract UID from a VTODO/VEVENT item"""
    try:
        cal = vobject.readOne(item.serialize())
        if hasattr(cal, 'vtodo'):
            return cal.vtodo.uid.value
        if hasattr(cal, 'vevent'):
            return cal.vevent.uid.value
    except Exception:
        pass
    return None


class EventEmittingCollection(Collection):
    """Collection that emits events on all modifications"""

    def upload(self, href: str, item) -> tuple:
        """Called when a VTODO/VEVENT is created or updated"""
        print(f"DEBUG: EventEmittingCollection.upload called: {href}")
        result = super().upload(href, item)

        _emit_event(CalDAVEvent(
            type=EventType.UPLOAD,
            timestamp=datetime.now(timezone.utc),
            collection=self.path,
            href=href,
            item_data=item.serialize(),
            uid=_extract_uid(item),
        ))

        return result

    def delete(self, href: Optional[str] = None) -> None:
        """Called when a VTODO/VEVENT is deleted"""
        if href:
            _emit_event(CalDAVEvent(
                type=EventType.DELETE,
                timestamp=datetime.now(timezone.utc),
                collection=self.path,
                href=href,
            ))

        return super().delete(href)

    def move(self, item, to_collection, to_href: str):
        """Called when a VTODO/VEVENT is moved between collections"""
        result = super().move(item, to_collection, to_href)

        _emit_event(CalDAVEvent(
            type=EventType.MOVE,
            timestamp=datetime.now(timezone.utc),
            from_collection=self.path,
            to_collection=to_collection.path,
            from_href=item.href,
            to_href=to_href,
            uid=_extract_uid(item),
        ))

        return result


class Storage(BaseStorage):
    """Storage class that uses EventEmittingCollection"""

    _collection_class = EventEmittingCollection

    def create_collection(self, href: str, items=None, props=None):
        """Called when a new collection (calendar/list) is created"""
        result = super().create_collection(href, items, props)

        collection_name = href.strip('/').split('/')[-1]
        display_name = collection_name

        if props and 'D:displayname' in props:
            display_name = props['D:displayname']

        _emit_event(CalDAVEvent(
            type=EventType.COLLECTION_CREATED,
            timestamp=datetime.now(timezone.utc),
            collection=href,
            href=href,
            display_name=display_name,
        ))

        return result
