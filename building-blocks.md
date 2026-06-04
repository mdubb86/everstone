> **SUPERSEDED.** This document describes the original bidirectional
> markdown↔CalDAV bridge approach. The current design replaces it with a
> Hermes-centred hub. See `docs/superpowers/specs/2026-06-03-everstone-hermes-design.md`
> for the active design.

# EverStone - Building Blocks Documentation

## Overview

EverStone is a self-hosted Obsidian LiveSync server with CalDAV sync for Tasks.org. This document outlines the core building blocks for bidirectional sync between Obsidian (via CouchDB) and Tasks.org (via CalDAV/Radicale).

## Architecture

```
Obsidian App ←→ CouchDB ←→ Bridge ←→ Radicale (CalDAV) ←→ Tasks.org
```

**Components:**
- **CouchDB**: Stores Obsidian notes as JSON documents
- **Radicale**: CalDAV server storing tasks as VTODO .ics files
- **Bridge**: Python service orchestrating bidirectional sync
- **Event Queue**: In-process asyncio.Queue for real-time events

## Core Building Blocks

### 1. Event Emission from CouchDB

**CouchDB Changes Feed Watcher**

```python
import asyncio
import aiohttp
import json

class CouchDBWatcher:
    """Watch CouchDB changes feed for real-time updates"""
    
    def __init__(self, couchdb_url: str, database: str):
        self.couchdb_url = couchdb_url
        self.database = database
    
    async def watch_changes(self, callback):
        """
        Watch CouchDB changes feed and call callback for each change
        
        Args:
            callback: async function(change_event: dict) -> None
        """
        url = f"{self.couchdb_url}/{self.database}/_changes"
        params = {
            'feed': 'continuous',
            'since': 'now',
            'include_docs': 'true',
            'heartbeat': '30000'  # 30 second heartbeat
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                async for line in response.content:
                    if line.strip():
                        try:
                            change = json.loads(line)
                            if 'doc' in change:
                                await callback(change)
                        except json.JSONDecodeError:
                            continue
    
    async def get_document(self, doc_id: str) -> dict:
        """Fetch a specific document from CouchDB"""
        url = f"{self.couchdb_url}/{self.database}/{doc_id}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None


# Usage example
async def handle_change(change):
    doc_id = change['id']
    deleted = change.get('deleted', False)
    doc = change.get('doc')
    
    if deleted:
        print(f"Document deleted: {doc_id}")
    else:
        print(f"Document changed: {doc_id}")

watcher = CouchDBWatcher("http://couchdb:5984", "obsidian")
await watcher.watch_changes(handle_change)
```

### 2. Event Emission from CalDAV (Radicale)

**Custom Radicale Storage with Events**

```python
# radicale_custom/storage.py
from radicale.storage.multifilesystem import Collection, Storage
from datetime import datetime
import asyncio
import vobject

# Shared event queue
caldav_event_queue = asyncio.Queue(maxsize=1000)


class EventEmittingCollection(Collection):
    """Collection that emits events on all modifications"""
    
    def upload(self, href, item):
        """Called when a VTODO is created or updated"""
        result = super().upload(href, item)
        
        try:
            caldav_event_queue.put_nowait({
                'type': 'upload',
                'collection': self.path,
                'href': href,
                'item_data': item.serialize(),
                'uid': self._extract_uid(item),
                'timestamp': datetime.utcnow().isoformat()
            })
        except asyncio.QueueFull:
            print(f"Warning: Event queue full, dropped event for {href}")
        
        return result
    
    def delete(self, href=None):
        """Called when a VTODO is deleted"""
        if href:
            try:
                caldav_event_queue.put_nowait({
                    'type': 'delete',
                    'collection': self.path,
                    'href': href,
                    'timestamp': datetime.utcnow().isoformat()
                })
            except asyncio.QueueFull:
                print(f"Warning: Event queue full, dropped delete event for {href}")
        
        result = super().delete(href)
        return result
    
    def move(self, item, to_collection, to_href):
        """Called when a VTODO is moved between collections"""
        result = super().move(item, to_collection, to_href)
        
        try:
            caldav_event_queue.put_nowait({
                'type': 'move',
                'from_collection': self.path,
                'to_collection': to_collection.path,
                'from_href': item.href,
                'to_href': to_href,
                'uid': self._extract_uid(item),
                'timestamp': datetime.utcnow().isoformat()
            })
        except asyncio.QueueFull:
            print(f"Warning: Event queue full, dropped move event")
        
        return result
    
    def _extract_uid(self, item):
        """Extract UID from VTODO item"""
        try:
            cal = vobject.readOne(item.serialize())
            if hasattr(cal, 'vtodo'):
                return cal.vtodo.uid.value
        except:
            pass
        return None


class EventEmittingStorage(Storage):
    """Storage class that uses EventEmittingCollection"""
    
    _collection_class = EventEmittingCollection
    
    def create_collection(self, href, items=None, props=None):
        """Called when a new collection (list) is created"""
        result = super().create_collection(href, items, props)
        
        collection_name = href.strip('/').split('/')[-1]
        display_name = collection_name
        
        if props and 'D:displayname' in props:
            display_name = props['D:displayname']
        
        try:
            caldav_event_queue.put_nowait({
                'type': 'collection_created',
                'href': href,
                'name': collection_name,
                'display_name': display_name,
                'timestamp': datetime.utcnow().isoformat()
            })
        except asyncio.QueueFull:
            print(f"Warning: Event queue full, dropped collection create event")
        
        return result
    
    def delete_collection(self, href):
        """Called when a collection (list) is deleted"""
        collection_name = href.strip('/').split('/')[-1]
        
        try:
            caldav_event_queue.put_nowait({
                'type': 'collection_deleted',
                'href': href,
                'name': collection_name,
                'timestamp': datetime.utcnow().isoformat()
            })
        except asyncio.QueueFull:
            print(f"Warning: Event queue full, dropped collection delete event")
        
        result = super().delete_collection(href)
        return result


# Usage in bridge
async def watch_caldav_events():
    """Watch CalDAV event queue"""
    while True:
        event = await caldav_event_queue.get()
        
        try:
            if event['type'] == 'upload':
                print(f"Task uploaded: {event['uid']}")
            elif event['type'] == 'delete':
                print(f"Task deleted: {event['href']}")
            elif event['type'] == 'move':
                print(f"Task moved: {event['uid']}")
            elif event['type'] == 'collection_created':
                print(f"List created: {event['display_name']}")
            elif event['type'] == 'collection_deleted':
                print(f"List deleted: {event['name']}")
        finally:
            caldav_event_queue.task_done()
```

### 3. CRUD Operations - CouchDB (Obsidian)

**Reading Obsidian Documents**

```python
import base64
import json

class ObsidianDocument:
    """Helper for working with Obsidian documents in CouchDB"""
    
    @staticmethod
    def decode_content(doc: dict) -> str:
        """
        Decode Obsidian document content to markdown
        
        Self-hosted LiveSync stores content in 'data' field,
        potentially base64 encoded or as plain text
        """
        if 'data' not in doc:
            return ""
        
        data = doc['data']
        
        # Try as plain text first
        if isinstance(data, str) and not data.startswith('data:'):
            return data
        
        # Handle base64 encoded data
        if isinstance(data, str) and data.startswith('data:'):
            # Format: data:text/markdown;base64,<base64data>
            parts = data.split(',', 1)
            if len(parts) == 2:
                try:
                    return base64.b64decode(parts[1]).decode('utf-8')
                except:
                    return data
        
        return str(data)
    
    @staticmethod
    def encode_content(markdown: str) -> str:
        """
        Encode markdown content for CouchDB storage
        
        Returns plain text (Self-hosted LiveSync handles encoding)
        """
        return markdown
    
    @staticmethod
    def extract_frontmatter(markdown: str) -> dict:
        """Extract YAML frontmatter from markdown"""
        if not markdown.startswith('---'):
            return {}
        
        parts = markdown.split('---', 2)
        if len(parts) < 3:
            return {}
        
        try:
            import yaml
            return yaml.safe_load(parts[1]) or {}
        except:
            return {}
    
    @staticmethod
    def update_frontmatter(markdown: str, updates: dict) -> str:
        """Update frontmatter in markdown document"""
        import yaml
        
        frontmatter = ObsidianDocument.extract_frontmatter(markdown)
        frontmatter.update(updates)
        
        # Remove existing frontmatter
        if markdown.startswith('---'):
            parts = markdown.split('---', 2)
            content = parts[2] if len(parts) >= 3 else markdown
        else:
            content = markdown
        
        # Add updated frontmatter
        fm_yaml = yaml.dump(frontmatter, default_flow_style=False)
        return f"---\n{fm_yaml}---{content}"


class CouchDBOperations:
    """CRUD operations for CouchDB documents"""
    
    def __init__(self, couchdb_url: str, database: str):
        self.couchdb_url = couchdb_url
        self.database = database
    
    async def read_document(self, doc_id: str) -> dict:
        """Read a document from CouchDB"""
        url = f"{self.couchdb_url}/{self.database}/{doc_id}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    return None
                else:
                    raise Exception(f"Failed to read document: {response.status}")
    
    async def create_document(self, doc_id: str, content: dict) -> dict:
        """Create a new document in CouchDB"""
        url = f"{self.couchdb_url}/{self.database}/{doc_id}"
        
        async with aiohttp.ClientSession() as session:
            async with session.put(url, json=content) as response:
                if response.status in [201, 202]:
                    return await response.json()
                else:
                    raise Exception(f"Failed to create document: {response.status}")
    
    async def update_document(self, doc_id: str, content: dict) -> dict:
        """Update an existing document in CouchDB"""
        # Get current revision
        current_doc = await self.read_document(doc_id)
        if not current_doc:
            raise Exception(f"Document {doc_id} not found")
        
        # Update with new content, preserving _rev
        content['_rev'] = current_doc['_rev']
        content['_id'] = doc_id
        
        url = f"{self.couchdb_url}/{self.database}/{doc_id}"
        
        async with aiohttp.ClientSession() as session:
            async with session.put(url, json=content) as response:
                if response.status in [201, 202]:
                    return await response.json()
                else:
                    raise Exception(f"Failed to update document: {response.status}")
    
    async def delete_document(self, doc_id: str) -> dict:
        """Delete a document from CouchDB"""
        current_doc = await self.read_document(doc_id)
        if not current_doc:
            raise Exception(f"Document {doc_id} not found")
        
        url = f"{self.couchdb_url}/{self.database}/{doc_id}"
        params = {'rev': current_doc['_rev']}
        
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    raise Exception(f"Failed to delete document: {response.status}")
    
    async def update_markdown_note(self, doc_id: str, markdown: str):
        """Update a markdown note in CouchDB"""
        doc = await self.read_document(doc_id)
        
        if not doc:
            # Create new document
            doc = {
                '_id': doc_id,
                'data': ObsidianDocument.encode_content(markdown),
                'type': 'plain',
                'mtime': int(datetime.utcnow().timestamp() * 1000)
            }
            return await self.create_document(doc_id, doc)
        else:
            # Update existing
            doc['data'] = ObsidianDocument.encode_content(markdown)
            doc['mtime'] = int(datetime.utcnow().timestamp() * 1000)
            return await self.update_document(doc_id, doc)


# Usage example
couchdb = CouchDBOperations("http://couchdb:5984", "obsidian")

# Read a note
doc = await couchdb.read_document("daily-notes/2024-11-25.md")
markdown = ObsidianDocument.decode_content(doc)

# Update a note
new_markdown = "# My Note\n\n- [ ] Task 1\n- [ ] Task 2"
await couchdb.update_markdown_note("notes/my-note.md", new_markdown)

# Extract frontmatter
frontmatter = ObsidianDocument.extract_frontmatter(markdown)
print(frontmatter.get('tags', []))
```

### 4. CRUD Operations - CalDAV (Tasks)

**CalDAV Task Operations**

```python
from caldav import DAVClient
import vobject
from datetime import datetime

class CalDAVOperations:
    """CRUD operations for CalDAV tasks"""
    
    def __init__(self, caldav_url: str, username: str, password: str):
        self.client = DAVClient(
            url=caldav_url,
            username=username,
            password=password
        )
        self.principal = self.client.principal()
    
    def get_calendar(self, calendar_name: str):
        """Get a calendar/collection by name"""
        try:
            return self.principal.calendar(cal_id=calendar_name)
        except:
            return None
    
    def create_calendar(self, calendar_name: str, display_name: str):
        """Create a new calendar/list"""
        from caldav.elements import dav
        
        calendar = self.principal.make_calendar(
            name=calendar_name,
            cal_id=calendar_name,
            supported_calendar_component_set=['VTODO']
        )
        
        # Set display name
        calendar.set_properties([
            dav.DisplayName(display_name),
        ])
        
        return calendar
    
    def delete_calendar(self, calendar_name: str):
        """Delete a calendar/list"""
        calendar = self.get_calendar(calendar_name)
        if calendar:
            calendar.delete()
            return True
        return False
    
    def list_calendars(self):
        """List all calendars"""
        calendars = self.principal.calendars()
        return [
            {
                'name': cal.name,
                'url': cal.url
            }
            for cal in calendars
        ]
    
    def create_task(self, calendar_name: str, task_data: dict) -> str:
        """
        Create a new task in a calendar
        
        Args:
            calendar_name: Name of the calendar
            task_data: {
                'uid': 'unique-id',
                'summary': 'Task title',
                'description': 'Task description',
                'due': datetime object or None,
                'completed': bool,
                'priority': int (1-9, 1=highest),
                'tags': ['tag1', 'tag2']
            }
        
        Returns:
            UID of created task
        """
        calendar = self.get_calendar(calendar_name)
        if not calendar:
            calendar = self.create_calendar(calendar_name, calendar_name.title())
        
        # Create VTODO
        vcal = vobject.iCalendar()
        vtodo = vcal.add('vtodo')
        
        vtodo.add('uid').value = task_data['uid']
        vtodo.add('summary').value = task_data['summary']
        vtodo.add('dtstamp').value = datetime.utcnow()
        
        if task_data.get('description'):
            vtodo.add('description').value = task_data['description']
        
        if task_data.get('completed'):
            vtodo.add('status').value = 'COMPLETED'
            vtodo.add('completed').value = datetime.utcnow()
        else:
            vtodo.add('status').value = 'NEEDS-ACTION'
        
        if task_data.get('due'):
            vtodo.add('due').value = task_data['due']
        
        if task_data.get('priority'):
            vtodo.add('priority').value = task_data['priority']
        
        if task_data.get('tags'):
            vtodo.add('categories').value = task_data['tags']
        
        # Save to CalDAV
        calendar.save_event(vcal.serialize())
        
        return task_data['uid']
    
    def read_task(self, calendar_name: str, uid: str) -> dict:
        """Read a task by UID"""
        calendar = self.get_calendar(calendar_name)
        if not calendar:
            return None
        
        try:
            todo = calendar.todo_by_uid(uid)
            vtodo = todo.instance.vtodo
            
            return {
                'uid': vtodo.uid.value,
                'summary': vtodo.summary.value,
                'description': getattr(vtodo, 'description', None),
                'completed': vtodo.status.value == 'COMPLETED',
                'due': getattr(vtodo, 'due', None),
                'priority': getattr(vtodo, 'priority', None),
                'tags': getattr(vtodo, 'categories', None)
            }
        except:
            return None
    
    def update_task(self, calendar_name: str, uid: str, updates: dict):
        """
        Update an existing task
        
        Args:
            calendar_name: Name of the calendar
            uid: Task UID
            updates: Dict with fields to update (same structure as create_task)
        """
        calendar = self.get_calendar(calendar_name)
        if not calendar:
            return False
        
        try:
            todo = calendar.todo_by_uid(uid)
            vtodo = todo.instance.vtodo
            
            # Update fields
            if 'summary' in updates:
                vtodo.summary.value = updates['summary']
            
            if 'description' in updates:
                if hasattr(vtodo, 'description'):
                    vtodo.description.value = updates['description']
                else:
                    vtodo.add('description').value = updates['description']
            
            if 'completed' in updates:
                if updates['completed']:
                    vtodo.status.value = 'COMPLETED'
                    if not hasattr(vtodo, 'completed'):
                        vtodo.add('completed').value = datetime.utcnow()
                else:
                    vtodo.status.value = 'NEEDS-ACTION'
                    if hasattr(vtodo, 'completed'):
                        del vtodo.completed
            
            if 'due' in updates:
                if hasattr(vtodo, 'due'):
                    vtodo.due.value = updates['due']
                else:
                    vtodo.add('due').value = updates['due']
            
            if 'priority' in updates:
                if hasattr(vtodo, 'priority'):
                    vtodo.priority.value = updates['priority']
                else:
                    vtodo.add('priority').value = updates['priority']
            
            if 'tags' in updates:
                if hasattr(vtodo, 'categories'):
                    vtodo.categories.value = updates['tags']
                else:
                    vtodo.add('categories').value = updates['tags']
            
            # Update timestamp
            vtodo.dtstamp.value = datetime.utcnow()
            
            # Save
            todo.save()
            return True
        except:
            return False
    
    def delete_task(self, calendar_name: str, uid: str):
        """Delete a task by UID"""
        calendar = self.get_calendar(calendar_name)
        if not calendar:
            return False
        
        try:
            todo = calendar.todo_by_uid(uid)
            todo.delete()
            return True
        except:
            return False
    
    def list_tasks(self, calendar_name: str, include_completed: bool = False) -> list:
        """List all tasks in a calendar"""
        calendar = self.get_calendar(calendar_name)
        if not calendar:
            return []
        
        todos = calendar.todos(include_completed=include_completed)
        
        results = []
        for todo in todos:
            vtodo = todo.instance.vtodo
            results.append({
                'uid': vtodo.uid.value,
                'summary': vtodo.summary.value,
                'completed': vtodo.status.value == 'COMPLETED',
                'due': getattr(vtodo, 'due', None),
                'priority': getattr(vtodo, 'priority', None)
            })
        
        return results


# Usage examples
caldav = CalDAVOperations(
    "http://radicale:5232",
    "user",
    "password"
)

# Create a calendar/list
caldav.create_calendar("work-tasks", "Work Tasks")

# Create a task
task_uid = caldav.create_task("work-tasks", {
    'uid': 'task-123',
    'summary': 'Finish report',
    'description': 'Q4 financial report',
    'due': datetime(2024, 11, 30),
    'completed': False,
    'priority': 1,
    'tags': ['urgent', 'finance']
})

# Read a task
task = caldav.read_task("work-tasks", "task-123")
print(task['summary'])

# Update a task
caldav.update_task("work-tasks", "task-123", {
    'completed': True
})

# Delete a task
caldav.delete_task("work-tasks", "task-123")

# List all tasks
tasks = caldav.list_tasks("work-tasks", include_completed=False)
for task in tasks:
    print(f"- {task['summary']}")

# Delete a calendar
caldav.delete_calendar("old-project")
```

### 5. Task Parsing from Markdown

**Extract Tasks from Obsidian Markdown**

```python
import re
from datetime import datetime
import hashlib

class TaskParser:
    """Parse tasks from Obsidian markdown"""
    
    @staticmethod
    def extract_tasks(markdown: str, note_id: str) -> list:
        """
        Extract tasks from markdown
        
        Returns list of task dicts with:
        - uid: Unique identifier
        - summary: Task text
        - completed: bool
        - due: datetime or None
        - priority: int or None
        - tags: list of strings
        - line_number: int
        """
        tasks = []
        lines = markdown.split('\n')
        
        # Match: - [ ] or - [x] or - [X]
        task_pattern = re.compile(r'^(\s*)- \[([ xX])\] (.+)$')
        
        for line_num, line in enumerate(lines):
            match = task_pattern.match(line)
            if match:
                indent = match.group(1)
                completed = match.group(2).lower() == 'x'
                text = match.group(3).strip()
                
                # Parse metadata from text
                metadata = TaskParser.parse_task_metadata(text)
                
                # Generate stable UID
                uid = TaskParser.generate_uid(note_id, metadata['clean_text'], line_num)
                
                tasks.append({
                    'uid': uid,
                    'summary': metadata['clean_text'],
                    'completed': completed,
                    'due': metadata.get('due'),
                    'priority': metadata.get('priority'),
                    'tags': metadata.get('tags', []),
                    'line_number': line_num,
                    'indent': len(indent),
                    'note_id': note_id
                })
        
        return tasks
    
    @staticmethod
    def parse_task_metadata(text: str) -> dict:
        """
        Parse Obsidian task metadata
        
        Supports:
        - Due dates: 📅 2024-11-25 or 🗓️ 2024-11-25
        - Priority: ⏫ (high), 🔼 (medium), 🔽 (low)
        - Tags: #tag1 #tag2
        """
        data = {'clean_text': text}
        
        # Extract due date
        date_pattern = r'[📅🗓️]\s*(\d{4}-\d{2}-\d{2})'
        date_match = re.search(date_pattern, text)
        if date_match:
            try:
                data['due'] = datetime.fromisoformat(date_match.group(1))
                text = re.sub(date_pattern, '', text).strip()
            except:
                pass
        
        # Extract priority
        if '⏫' in text:
            data['priority'] = 1  # High
            text = text.replace('⏫', '').strip()
        elif '🔼' in text:
            data['priority'] = 5  # Medium
            text = text.replace('🔼', '').strip()
        elif '🔽' in text:
            data['priority'] = 9  # Low
            text = text.replace('🔽', '').strip()
        
        # Extract tags
        tag_pattern = r'#([\w-]+)'
        tags = re.findall(tag_pattern, text)
        if tags:
            data['tags'] = tags
            text = re.sub(tag_pattern, '', text).strip()
        
        # Clean up extra whitespace
        data['clean_text'] = ' '.join(text.split())
        
        return data
    
    @staticmethod
    def generate_uid(note_id: str, task_text: str, line_num: int) -> str:
        """
        Generate stable UID for a task
        
        Uses note_id + task_text (not line_num to handle reordering)
        """
        content = f"{note_id}:{task_text[:100]}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]
    
    @staticmethod
    def update_task_in_markdown(markdown: str, uid: str, updates: dict) -> str:
        """
        Update a specific task in markdown by UID
        
        Args:
            markdown: Original markdown content
            uid: UID of task to update
            updates: Dict with 'completed' and/or 'summary'
        
        Returns:
            Updated markdown
        """
        lines = markdown.split('\n')
        task_pattern = re.compile(r'^(\s*)- \[([ xX])\] (.+)$')
        
        for line_num, line in enumerate(lines):
            match = task_pattern.match(line)
            if match:
                text = match.group(3).strip()
                metadata = TaskParser.parse_task_metadata(text)
                
                # Generate UID for this task
                note_id = updates.get('note_id', 'unknown')
                task_uid = TaskParser.generate_uid(note_id, metadata['clean_text'], line_num)
                
                if task_uid == uid:
                    # Found the task, update it
                    indent = match.group(1)
                    
                    # Update completion status
                    if 'completed' in updates:
                        checkbox = '[x]' if updates['completed'] else '[ ]'
                    else:
                        checkbox = match.group(2)
                        checkbox = f'[{checkbox}]'
                    
                    # Update summary
                    if 'summary' in updates:
                        task_text = updates['summary']
                    else:
                        task_text = match.group(3)
                    
                    lines[line_num] = f"{indent}- {checkbox} {task_text}"
                    break
        
        return '\n'.join(lines)
    
    @staticmethod
    def add_task_to_markdown(markdown: str, task_text: str, completed: bool = False) -> str:
        """
        Add a new task to markdown (appends to end)
        
        Args:
            markdown: Original markdown content
            task_text: Task summary with optional metadata
            completed: Whether task is completed
        
        Returns:
            Updated markdown
        """
        checkbox = '[x]' if completed else '[ ]'
        new_task = f"- {checkbox} {task_text}"
        
        if markdown.strip():
            return f"{markdown}\n{new_task}"
        else:
            return new_task
    
    @staticmethod
    def remove_task_from_markdown(markdown: str, uid: str, note_id: str) -> str:
        """
        Remove a task from markdown by UID
        
        Args:
            markdown: Original markdown content
            uid: UID of task to remove
            note_id: Note ID for UID generation
        
        Returns:
            Updated markdown
        """
        lines = markdown.split('\n')
        task_pattern = re.compile(r'^(\s*)- \[([ xX])\] (.+)$')
        
        for line_num, line in enumerate(lines):
            match = task_pattern.match(line)
            if match:
                text = match.group(3).strip()
                metadata = TaskParser.parse_task_metadata(text)
                
                task_uid = TaskParser.generate_uid(note_id, metadata['clean_text'], line_num)
                
                if task_uid == uid:
                    # Remove this line
                    lines.pop(line_num)
                    break
        
        return '\n'.join(lines)


# Usage examples
markdown = """# My Tasks

- [ ] Buy groceries 📅 2024-11-25 #shopping
- [x] Finish report ⏫ #work
- [ ] Call dentist #health

## Other Notes
Some text here
"""

note_id = "daily-notes/2024-11-25.md"

# Extract tasks
tasks = TaskParser.extract_tasks(markdown, note_id)
for task in tasks:
    print(f"{task['summary']} - Due: {task['due']} - Tags: {task['tags']}")

# Update a task
updated_markdown = TaskParser.update_task_in_markdown(
    markdown,
    tasks[0]['uid'],
    {'completed': True}
)

# Add a new task
updated_markdown = TaskParser.add_task_to_markdown(
    markdown,
    "New task 📅 2024-11-26 #urgent"
)

# Remove a task
updated_markdown = TaskParser.remove_task_from_markdown(
    markdown,
    tasks[1]['uid'],
    note_id
)
```

### 6. Complete Bridge Event Loop

**Main Bridge Service**

```python
import asyncio
from events import caldav_event_queue

class EverStoneBridge:
    """Main bridge service orchestrating bidirectional sync"""
    
    def __init__(self, config: dict):
        self.config = config
        
        # Initialize components
        self.couchdb_watcher = CouchDBWatcher(
            config['couchdb_url'],
            config['couchdb_database']
        )
        self.couchdb_ops = CouchDBOperations(
            config['couchdb_url'],
            config['couchdb_database']
        )
        self.caldav_ops = CalDAVOperations(
            config['caldav_url'],
            config['caldav_username'],
            config['caldav_password']
        )
    
    async def start(self):
        """Start the bridge - run both sync directions"""
        print("Starting EverStone Bridge...")
        
        # Run both watchers concurrently
        await asyncio.gather(
            self.watch_obsidian_changes(),
            self.watch_caldav_changes(),
            self.periodic_health_check()
        )
    
    async def watch_obsidian_changes(self):
        """Watch Obsidian (CouchDB) for changes and sync to CalDAV"""
        print("Watching Obsidian changes...")
        
        async def handle_change(change):
            doc_id = change['id']
            deleted = change.get('deleted', False)
            
            if deleted:
                # Document deleted - handle task removals
                await self.handle_obsidian_document_deleted(doc_id)
            else:
                # Document created or updated
                doc = change['doc']
                await self.handle_obsidian_document_changed(doc)
        
        await self.couchdb_watcher.watch_changes(handle_change)
    
    async def handle_obsidian_document_changed(self, doc: dict):
        """Handle Obsidian document change"""
        doc_id = doc['_id']
        
        # Decode markdown
        markdown = ObsidianDocument.decode_content(doc)
        
        # Extract tasks
        tasks = TaskParser.extract_tasks(markdown, doc_id)
        
        # Sync each task to CalDAV
        for task in tasks:
            await self.sync_task_to_caldav(task)
    
    async def sync_task_to_caldav(self, task: dict):
        """Sync a task from Obsidian to CalDAV"""
        # Determine which calendar based on tags
        calendar_name = self.get_calendar_for_task(task)
        
        # Prepare task data for CalDAV
        caldav_task = {
            'uid': task['uid'],
            'summary': task['summary'],
            'completed': task['completed'],
            'due': task.get('due'),
            'priority': task.get('priority'),
            'tags': task.get('tags', [])
        }
        
        # Check if task exists
        existing = self.caldav_ops.read_task(calendar_name, task['uid'])
        
        if existing:
            # Update existing task
            self.caldav_ops.update_task(calendar_name, task['uid'], caldav_task)
        else:
            # Create new task
            self.caldav_ops.create_task(calendar_name, caldav_task)
    
    async def watch_caldav_changes(self):
        """Watch CalDAV for changes and sync to Obsidian"""
        print("Watching CalDAV changes...")
        
        while True:
            event = await caldav_event_queue.get()
            
            try:
                if event['type'] == 'upload':
                    await self.handle_caldav_task_upload(event)
                elif event['type'] == 'delete':
                    await self.handle_caldav_task_delete(event)
                elif event['type'] == 'move':
                    await self.handle_caldav_task_move(event)
                elif event['type'] == 'collection_created':
                    await self.handle_caldav_collection_created(event)
                elif event['type'] == 'collection_deleted':
                    await self.handle_caldav_collection_deleted(event)
            except Exception as e:
                print(f"Error handling CalDAV event: {e}")
            finally:
                caldav_event_queue.task_done()
    
    async def handle_caldav_task_upload(self, event):
        """Handle task created/updated in CalDAV"""
        collection = event['collection'].strip('/').split('/')[-1]
        uid = event['uid']
        
        # Read the task from CalDAV
        task = self.caldav_ops.read_task(collection, uid)
        if not task:
            return
        
        # Find which Obsidian note this belongs to
        note_id = await self.find_note_for_task(uid, collection)
        
        if not note_id:
            # New task - add to appropriate note
            note_id = self.get_note_for_collection(collection)
        
        # Update the note
        await self.update_task_in_obsidian(note_id, task)
    
    async def update_task_in_obsidian(self, note_id: str, task: dict):
        """Update a task in Obsidian note"""
        # Read current note
        doc = await self.couchdb_ops.read_document(note_id)
        if not doc:
            # Create new note
            markdown = TaskParser.add_task_to_markdown(
                "",
                task['summary'],
                task['completed']
            )
        else:
            markdown = ObsidianDocument.decode_content(doc)
            
            # Try to update existing task
            updated = TaskParser.update_task_in_markdown(
                markdown,
                task['uid'],
                {
                    'summary': task['summary'],
                    'completed': task['completed'],
                    'note_id': note_id
                }
            )
            
            # If UID not found, add as new task
            if updated == markdown:
                markdown = TaskParser.add_task_to_markdown(
                    markdown,
                    task['summary'],
                    task['completed']
                )
            else:
                markdown = updated
        
        # Save back to CouchDB
        await self.couchdb_ops.update_markdown_note(note_id, markdown)
    
    def get_calendar_for_task(self, task: dict) -> str:
        """Determine CalDAV calendar name from task tags"""
        tags = task.get('tags', [])
        
        # Map tags to calendars (configurable)
        tag_mapping = {
            'work': 'work-tasks',
            'personal': 'personal-tasks',
            'shopping': 'shopping',
            'health': 'health'
        }
        
        for tag in tags:
            if tag in tag_mapping:
                return tag_mapping[tag]
        
        # Default calendar
        return 'inbox'
    
    def get_note_for_collection(self, collection: str) -> str:
        """Map CalDAV collection to Obsidian note"""
        # Configurable mapping
        collection_mapping = {
            'work-tasks': 'Work/Tasks.md',
            'personal-tasks': 'Personal/Tasks.md',
            'shopping': 'Shopping.md',
            'health': 'Health/Tasks.md',
            'inbox': 'Inbox.md'
        }
        
        return collection_mapping.get(collection, 'Inbox.md')
    
    async def find_note_for_task(self, uid: str, collection: str) -> str:
        """Find which note contains a task with given UID"""
        # This would need a UID -> note mapping
        # For simplicity, use collection mapping
        return self.get_note_for_collection(collection)
    
    async def periodic_health_check(self):
        """Periodic health monitoring"""
        while True:
            await asyncio.sleep(60)
            queue_size = caldav_event_queue.qsize()
            print(f"Health check - CalDAV queue size: {queue_size}")
    
    async def handle_obsidian_document_deleted(self, doc_id: str):
        """Handle deleted Obsidian document"""
        # Implementation depends on desired behavior
        # Could delete all tasks from that note, or archive them
        pass
    
    async def handle_caldav_task_delete(self, event):
        """Handle task deleted in CalDAV"""
        # Implementation: remove task from Obsidian note
        pass
    
    async def handle_caldav_task_move(self, event):
        """Handle task moved between collections"""
        # Implementation: update task tags or move between notes
        pass
    
    async def handle_caldav_collection_created(self, event):
        """Handle new calendar/list created"""
        print(f"New calendar created: {event['display_name']}")
        # Could auto-create corresponding Obsidian note
    
    async def handle_caldav_collection_deleted(self, event):
        """Handle calendar/list deleted"""
        print(f"Calendar deleted: {event['name']}")
        # Could archive corresponding Obsidian note


# Main entry point
if __name__ == "__main__":
    config = {
        'couchdb_url': 'http://couchdb:5984',
        'couchdb_database': 'obsidian',
        'caldav_url': 'http://localhost:5232',
        'caldav_username': 'user',
        'caldav_password': 'password'
    }
    
    bridge = EverStoneBridge(config)
    asyncio.run(bridge.start())
```

## Summary

These building blocks provide:

1. **Event Emission**: Real-time events from both CouchDB and Radicale
2. **CRUD Operations**: Full create/read/update/delete for both systems
3. **Task Parsing**: Extract and manipulate tasks in Obsidian markdown
4. **Bidirectional Sync**: Complete event loop handling changes from both sides

All components are independent and can be used/extended as needed for the bridge implementation.
