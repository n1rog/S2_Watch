import json
from typing import Any

from s2_watch.models import ANY_APPLICATION, WatchForm


SUPPORTED_APPLICATIONS = frozenset(
    ("JS8Call", "MagCommstat", "Commstat", "FLMSG", "JS8SuperSpotter", ANY_APPLICATION)
)


def forms_to_json(forms: list[WatchForm]) -> str:
    payload = {
        "format": "s2-watch-forms",
        "version": 1,
        "watch_forms": [
            {
                "application": form.application,
                "name": form.name,
                "match_text": form.match_text,
                "enabled": form.enabled,
                "use_regex": form.use_regex,
                "message_type": form.message_type,
                "sender": form.sender,
            }
            for form in forms
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def forms_from_json(text: str) -> list[WatchForm]:
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON: {error.msg}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("watch_forms"), list):
        raise ValueError("The file must contain a 'watch_forms' list.")

    forms = []
    for index, item in enumerate(payload["watch_forms"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Watch form {index} must be an object.")
        application = item.get("application")
        name = item.get("name")
        if application not in SUPPORTED_APPLICATIONS:
            raise ValueError(f"Watch form {index} has an unsupported application.")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Watch form {index} must have a name.")
        string_fields = ("match_text", "message_type", "sender")
        values = {}
        for field in string_fields:
            value = item.get(field, "")
            if not isinstance(value, str):
                raise ValueError(f"Watch form {index} field '{field}' must be text.")
            values[field] = value
        forms.append(
            WatchForm(
                application=application,
                name=name.strip(),
                match_text=values["match_text"],
                enabled=bool(item.get("enabled", True)),
                use_regex=bool(item.get("use_regex", False)),
                message_type=values["message_type"].strip(),
                sender=values["sender"].strip(),
            )
        )
    return forms
