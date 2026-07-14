from enum import StrEnum


class EventPriority(StrEnum):
    P0_CRITICAL = "P0_CRITICAL"
    P1_HIGH = "P1_HIGH"
    P2_MEDIUM = "P2_MEDIUM"
    P3_LOW = "P3_LOW"


PRIORITY_RANK = {
    EventPriority.P0_CRITICAL: 0,
    EventPriority.P1_HIGH: 1,
    EventPriority.P2_MEDIUM: 2,
    EventPriority.P3_LOW: 3,
}
