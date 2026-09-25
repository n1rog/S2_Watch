from datetime import datetime, timedelta, timezone


def parse_source_time(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        except ValueError:
            continue
    return None


def is_recent(value: object, lookback: timedelta, now: datetime | None = None) -> bool:
    parsed = parse_source_time(value)
    if parsed is None:
        return True
    current = now or datetime.now(timezone.utc)
    return parsed >= current - lookback
