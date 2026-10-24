"""Existing Home physical-risk classification without module/provider dependencies."""

import enum


class ActuationRisk(enum.StrEnum):
    SAFE = "safe"
    REVERSIBLE = "reversible"
    CONSEQUENTIAL = "consequential"
    PROTECTED = "protected"

    @property
    def requires_approval(self) -> bool:
        return self in {self.CONSEQUENTIAL, self.PROTECTED}


_RISK_MAP: dict[tuple[str, str], ActuationRisk] = {
    ("homeassistant", "update_entity"): ActuationRisk.SAFE,
    ("light", "turn_on"): ActuationRisk.REVERSIBLE,
    ("light", "turn_off"): ActuationRisk.REVERSIBLE,
    ("switch", "turn_on"): ActuationRisk.REVERSIBLE,
    ("switch", "turn_off"): ActuationRisk.REVERSIBLE,
    ("fan", "turn_on"): ActuationRisk.REVERSIBLE,
    ("fan", "turn_off"): ActuationRisk.REVERSIBLE,
    ("humidifier", "turn_on"): ActuationRisk.REVERSIBLE,
    ("humidifier", "turn_off"): ActuationRisk.REVERSIBLE,
    ("climate", "set_temperature"): ActuationRisk.REVERSIBLE,
    ("cover", "close_cover"): ActuationRisk.CONSEQUENTIAL,
    ("cover", "open_cover"): ActuationRisk.PROTECTED,
    ("cover", "toggle"): ActuationRisk.PROTECTED,
    ("scene", "turn_on"): ActuationRisk.CONSEQUENTIAL,
    ("script", "turn_on"): ActuationRisk.CONSEQUENTIAL,
    ("automation", "trigger"): ActuationRisk.CONSEQUENTIAL,
    ("lock", "lock"): ActuationRisk.PROTECTED,
    ("lock", "unlock"): ActuationRisk.PROTECTED,
    ("lock", "open"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_arm_away"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_arm_home"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_arm_night"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_disarm"): ActuationRisk.PROTECTED,
    ("homeassistant", "turn_on"): ActuationRisk.CONSEQUENTIAL,
    ("homeassistant", "turn_off"): ActuationRisk.CONSEQUENTIAL,
    ("homeassistant", "toggle"): ActuationRisk.PROTECTED,
}


def classify_actuation(domain: str, service: str) -> ActuationRisk:
    """Return the declared risk, defaulting unknown physical calls to protected."""
    return _RISK_MAP.get((domain, service), ActuationRisk.PROTECTED)
