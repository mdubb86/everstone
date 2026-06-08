import uuid
from datetime import datetime
from typing import Optional
import caldav
from icalendar import Todo, Alarm, Calendar as ICalendar


class ParentNotFound(Exception):
    """Raised when a --parent uid is not found in any list."""
    es_code = "parent_not_found"


class HasSubtasks(Exception):
    """Raised when deleting a task with subtasks without force."""
    es_code = "has_subtasks"


class TasksClient:
    def __init__(self, url, username="", password=""):
        # caldav requires non-None username to send Basic auth headers;
        # use "user" as a fallback when connecting to auth-type=none servers
        self._dav = caldav.DAVClient(
            url=url,
            username=username if username else "user",
            password=password or "",
        )
        self._principal = self._dav.principal()

    def _calendar(self, list_name):
        for cal in self._principal.calendars():
            display_name = cal.get_display_name() if hasattr(cal, 'get_display_name') else cal.name
            if (display_name or cal.id) == list_name:
                return cal
        raise KeyError(f"task list not found: {list_name}")

    def ensure_list(self, list_name):
        try:
            return self._calendar(list_name)
        except KeyError:
            return self._principal.make_calendar(
                name=list_name, cal_id=uuid.uuid4().hex,
                supported_calendar_component_set=["VTODO"])

    def add_task(self, summary, list_name, url: Optional[str] = None,
                 remind_at: Optional[datetime] = None,
                 due: Optional[datetime] = None,
                 tags: Optional[list] = None,
                 parent_uid: Optional[str] = None) -> str:
        if parent_uid:
            _, list_name = self._find_in_any_list(parent_uid)  # child shares parent's list
        cal = self.ensure_list(list_name); uid = uuid.uuid4().hex
        todo = Todo()
        todo.add("uid", uid); todo.add("summary", summary); todo.add("status", "NEEDS-ACTION")
        if parent_uid:
            todo.add("related-to", parent_uid, parameters={"RELTYPE": "PARENT"})
        if url:
            todo.add("url", url)
        if due:
            todo.add("due", due)
        if tags:
            todo.add("categories", tags)
        if remind_at:
            alarm = Alarm()
            alarm.add("action", "DISPLAY"); alarm.add("description", summary)
            alarm.add("trigger", remind_at)
            todo.add_component(alarm)
        ical = ICalendar(); ical.add("prodid", "-//EverStone//es//EN"); ical.add("version", "2.0")
        ical.add_component(todo)
        cal.save_todo(ical=ical.to_ical().decode())
        return uid

    @staticmethod
    def _read_tags(c):
        if "categories" not in c:
            return []
        cats = c.get("categories")
        items = cats if isinstance(cats, list) else [cats]
        out = []
        for entry in items:
            out.extend(str(x) for x in getattr(entry, "cats", [entry]))
        return out

    @staticmethod
    def _read_due(c):
        if "due" not in c:
            return None
        dt = c["due"].dt
        return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)

    @staticmethod
    def _read_parent(c):
        if "related-to" not in c:
            return None
        rel = c.get("related-to")
        items = rel if isinstance(rel, list) else [rel]
        for entry in items:
            params = getattr(entry, "params", {}) or {}
            reltype = str(params.get("RELTYPE", "PARENT")).upper()
            if reltype == "PARENT":
                return str(entry)
        return None

    def _find_in_any_list(self, uid):
        for cal in self._principal.calendars():
            name = cal.get_display_name() if hasattr(cal, "get_display_name") else cal.name
            name = name or cal.id
            for todo in cal.todos(include_completed=True):
                if str(todo.icalendar_component.get("uid", "")) == uid:
                    return todo, name
        raise ParentNotFound(f"parent task not found: {uid}")

    def list_tasks(self, list_name):
        out = []
        for todo in self._calendar(list_name).todos(include_completed=True):
            c = todo.icalendar_component
            out.append({
                "uid": str(c.get("uid", "")), "summary": str(c.get("summary", "")),
                "status": str(c.get("status", "NEEDS-ACTION")),
                "url": str(c["url"]) if "url" in c else None,
                "has_alarm": b"BEGIN:VALARM" in todo.data.encode() if hasattr(todo, 'data') else any(sc.name == "VALARM" for sc in todo.icalendar_instance.walk()),
                "tags": self._read_tags(c),
                "due": self._read_due(c),
                "parent": self._read_parent(c),
            })
        return out

    def _find(self, uid, list_name):
        for todo in self._calendar(list_name).todos(include_completed=True):
            if str(todo.icalendar_component.get("uid", "")) == uid:
                return todo
        raise KeyError(uid)

    def complete_task(self, uid, list_name):
        todo = self._find(uid, list_name); c = todo.icalendar_component
        c["status"] = "COMPLETED"
        if "percent-complete" not in c:
            c.add("percent-complete", 100)
        todo.save()

    def edit_task(self, uid, list_name, summary: Optional[str] = None,
                  due: Optional[datetime] = None,
                  remind_at: Optional[datetime] = None,
                  tags: Optional[list] = None,
                  parent_uid: Optional[str] = None) -> None:
        todo = self._find(uid, list_name); c = todo.icalendar_component
        if summary is not None:
            c["summary"] = summary
        if due is not None:
            if "due" in c:
                del c["due"]
            c.add("due", due)
        if tags is not None:
            if "categories" in c:
                del c["categories"]
            c.add("categories", tags)
        if remind_at is not None:
            for sub in [s for s in c.subcomponents if getattr(s, "name", "") == "VALARM"]:
                c.subcomponents.remove(sub)
            alarm = Alarm()
            alarm.add("action", "DISPLAY"); alarm.add("description", c.get("summary", ""))
            alarm.add("trigger", remind_at)
            c.add_component(alarm)
        if parent_uid is None:
            todo.save()  # no parent change
            return
        # parent_uid given: "" detaches in place; a real uid (re)links and moves
        # the child into the parent's list if it lives elsewhere.
        if "related-to" in c:
            del c["related-to"]
        if parent_uid == "":
            todo.save()
            return
        _, parent_list = self._find_in_any_list(parent_uid)  # raises ParentNotFound
        c.add("related-to", parent_uid, parameters={"RELTYPE": "PARENT"})
        if parent_list == list_name:
            todo.save()
        else:
            # Serialize via a freshly built VCALENDAR so all in-memory edits
            # (summary, RELATED-TO, etc.) are captured in the saved iCalendar data.
            ical = ICalendar()
            ical.add("prodid", "-//EverStone//es//EN")
            ical.add("version", "2.0")
            ical.add_component(c)
            self.ensure_list(parent_list).save_todo(ical=ical.to_ical().decode())
            todo.delete()

    def set_note_link(self, uid, list_name, url):
        todo = self._find(uid, list_name); c = todo.icalendar_component
        if "url" in c:
            del c["url"]
        c.add("url", url); todo.save()

    def children_of(self, uid, list_name):
        out = []
        for todo in self._calendar(list_name).todos(include_completed=True):
            if self._read_parent(todo.icalendar_component) == uid:
                out.append(todo)
        return out

    def delete_task(self, uid, list_name, force: bool = False):
        todo = self._find(uid, list_name)
        children = self.children_of(uid, list_name)
        if children and not force:
            raise HasSubtasks(
                f"Task has {len(children)} subtask(s); pass --force to delete it and them")
        for child in children:
            child.delete()
        todo.delete()

    def clear_list(self, list_name, completed_only: bool = True) -> int:
        removed = 0
        for todo in self._calendar(list_name).todos(include_completed=True):
            status = str(todo.icalendar_component.get("status", "NEEDS-ACTION"))
            if completed_only and status != "COMPLETED":
                continue
            todo.delete()
            removed += 1
        return removed

    def delete_list(self, list_name) -> None:
        self._calendar(list_name).delete()

    def list_collections(self):
        out = []
        for cal in self._principal.calendars():
            name = cal.get_display_name() if hasattr(cal, "get_display_name") else cal.name
            name = name or cal.id
            todos = cal.todos(include_completed=True)
            total = len(todos)
            open_ = sum(
                1 for t in todos
                if str(t.icalendar_component.get("status", "NEEDS-ACTION")) != "COMPLETED"
            )
            out.append({"name": name, "open_count": open_, "total_count": total})
        return out
