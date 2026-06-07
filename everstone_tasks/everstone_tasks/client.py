import uuid
from datetime import datetime
from typing import Optional
import caldav
from icalendar import Todo, Alarm, Calendar as ICalendar


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
                name=list_name, cal_id=list_name,
                supported_calendar_component_set=["VTODO"])

    def add_task(self, summary, list_name, url: Optional[str] = None,
                 remind_at: Optional[datetime] = None) -> str:
        cal = self.ensure_list(list_name); uid = uuid.uuid4().hex
        todo = Todo()
        todo.add("uid", uid); todo.add("summary", summary); todo.add("status", "NEEDS-ACTION")
        if url:
            todo.add("url", url)
        if remind_at:
            alarm = Alarm()
            alarm.add("action", "DISPLAY"); alarm.add("description", summary)
            alarm.add("trigger", remind_at)
            todo.add_component(alarm)
        ical = ICalendar(); ical.add("prodid", "-//everstone-tasks//EN"); ical.add("version", "2.0")
        ical.add_component(todo)
        cal.save_todo(ical=ical.to_ical().decode())
        return uid

    def list_tasks(self, list_name):
        out = []
        for todo in self._calendar(list_name).todos(include_completed=True):
            c = todo.icalendar_component
            out.append({
                "uid": str(c.get("uid", "")), "summary": str(c.get("summary", "")),
                "status": str(c.get("status", "NEEDS-ACTION")),
                "url": str(c["url"]) if "url" in c else None,
                "has_alarm": b"BEGIN:VALARM" in todo.data.encode() if hasattr(todo, 'data') else any(sc.name == "VALARM" for sc in todo.icalendar_instance.walk()),
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

    def set_note_link(self, uid, list_name, url):
        todo = self._find(uid, list_name); c = todo.icalendar_component
        if "url" in c:
            del c["url"]
        c.add("url", url); todo.save()

    def delete_task(self, uid, list_name):
        todo = self._find(uid, list_name)
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
