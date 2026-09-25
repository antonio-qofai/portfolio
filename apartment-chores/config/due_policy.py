"""When chores are due, and in what timezone.

Data only. All scheduling happens in this timezone; anything reading a clock
converts at the boundary and never compares naive datetimes.
"""

from dataclasses import dataclass
from datetime import time

TIMEZONE = "America/Chicago"


@dataclass(frozen=True)
class DuePolicy:
    """Due times for ordinary weeks and for cleaner prep.

    normal_day_offset counts days from the Monday that starts the week, so 6
    is the Sunday that ends it.

    prep_time is the time of day on the cleaner's visit date. The date itself
    is read off the Cleaner Visits row and never assumed, because her day can
    move. Prep finished after she arrives is worthless.
    """

    timezone: str
    normal_day_offset: int
    normal_time: time
    prep_time: time


DEFAULT_DUE_POLICY = DuePolicy(
    timezone=TIMEZONE,
    normal_day_offset=6,
    normal_time=time(20, 0),
    prep_time=time(11, 0),
)
