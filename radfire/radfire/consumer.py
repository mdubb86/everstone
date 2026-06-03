"""Event consumer that processes CalDAV events"""

import asyncio
import queue

from .events import CalDAVEvent, EventType, get_event_queue


async def print_consumer() -> None:
    """Simple consumer that prints all events to stdout"""
    event_queue = get_event_queue()
    print("Event consumer started, waiting for events...")

    while True:
        # Poll the thread-safe queue from async context
        try:
            event: CalDAVEvent = event_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.1)
            continue

        try:
            match event.type:
                case EventType.UPLOAD:
                    print(f"[UPLOAD] Task uploaded: uid={event.uid}, "
                          f"collection={event.collection}, href={event.href}")
                case EventType.DELETE:
                    print(f"[DELETE] Task deleted: collection={event.collection}, "
                          f"href={event.href}")
                case EventType.MOVE:
                    print(f"[MOVE] Task moved: uid={event.uid}, "
                          f"from={event.from_collection} to={event.to_collection}")
                case EventType.COLLECTION_CREATED:
                    print(f"[COLLECTION_CREATED] New list: {event.display_name} "
                          f"at {event.collection}")
                case EventType.COLLECTION_DELETED:
                    print(f"[COLLECTION_DELETED] List deleted: {event.collection}")
                case _:
                    print(f"[UNKNOWN] {event}")
        except Exception as e:
            print(f"Error processing event: {e}")
        finally:
            event_queue.task_done()
