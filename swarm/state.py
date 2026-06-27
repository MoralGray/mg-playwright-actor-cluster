from __future__ import annotations

from enum import Enum


class ActorState(Enum):
    IDLE = "idle"
    LOGIN = "login"
    NAVIGATE = "navigate"
    ACTION = "action"
    EXTRACT = "extract"
    REPORT = "report"


TRANSITIONS: dict[ActorState, ActorState] = {
    ActorState.IDLE: ActorState.LOGIN,
    ActorState.LOGIN: ActorState.NAVIGATE,
    ActorState.NAVIGATE: ActorState.ACTION,
    ActorState.ACTION: ActorState.EXTRACT,
    ActorState.EXTRACT: ActorState.REPORT,
    ActorState.REPORT: ActorState.IDLE,
}


def next_state(current: ActorState) -> ActorState:
    return TRANSITIONS[current]


def can_transition(current: ActorState, target: ActorState) -> bool:
    return TRANSITIONS.get(current) == target
