from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Header, HTTPException


async def get_user_timezone(
    user_timezone: Annotated[
        str,
        Header(
            alias="X-User-Timezone",
            description="IANA timezone name, for example Asia/Shanghai.",
        ),
    ],
) -> ZoneInfo:
    try:
        return ZoneInfo(user_timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid IANA timezone: {user_timezone}",
        ) from exc
