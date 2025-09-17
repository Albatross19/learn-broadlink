"""Interactive helper script for learning Broadlink IR commands.

The script walks the user through capturing IR commands for each operation,
fan, and optional swing mode combination while allowing the temperature range
to be tailored for the target climate device.  The implementation focuses on
providing a predictable structure, clear prompts, and well-scoped helper
utilities so the learning process is easier to follow and extend.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import broadlink
from broadlink.exceptions import ReadError, StorageError

TIMEOUT_SECONDS = 30
CONFIG_FILE = Path("smartir.json")


def prompt_yes_no(question: str, *, default: Optional[bool] = None) -> bool:
    """Return True if the user answers yes to *question*.

    The *default* argument defines the answer that will be assumed when the
    user simply presses enter.  When *default* is ``None`` an explicit answer is
    required.
    """

    if default is None:
        suffix = " (y/n) "
    elif default:
        suffix = " ([y]/n) "
    else:
        suffix = " (y/[n]) "

    while True:
        response = input(f"{question}{suffix}").strip().lower()
        if not response and default is not None:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("Please respond with 'y' or 'n'.")


def prompt_list(prompt_name: str, stored_values: Sequence[str]) -> List[str]:
    """Ask the user for a comma separated list of values.

    If the user provides an empty response the values stored in ``smartir.json``
    are reused to keep the workflow concise.
    """

    raw = input(
        f"Enter {prompt_name} separated by commas, or leave empty to "
        "reuse the stored values: "
    ).strip()

    if not raw:
        return list(stored_values)

    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values


def temperature_to_string(value: Decimal) -> str:
    """Convert a ``Decimal`` temperature to a compact string representation."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def decimal_to_number(value: Decimal) -> int | float:
    """Convert ``Decimal`` values to ``int`` when possible for JSON storage."""

    if value == value.to_integral_value():
        return int(value)
    return float(value)


def prompt_temperature(
    label: str,
    default: Decimal,
    precision: Decimal,
    minimum: Optional[Decimal] = None,
) -> Decimal:
    """Prompt the user for a temperature value respecting a given precision."""

    default_text = temperature_to_string(default)

    while True:
        response = input(
            f"Enter {label} temperature (default {default_text}): "
        ).strip()

        if not response:
            value = default
        else:
            try:
                value = Decimal(response)
            except InvalidOperation:
                print("Invalid temperature value. Please try again.")
                continue

        if precision <= 0:
            print("Precision must be greater than zero.")
            continue

        if value % precision != 0:
            print(
                f"Temperature must align with the configured precision ({precision})."
            )
            continue

        if minimum is not None and value < minimum:
            print("Maximum temperature cannot be lower than minimum temperature.")
            continue

        if minimum is not None and (value - minimum) % precision != 0:
            print(
                "The temperature difference must be a multiple of the "
                f"precision ({precision})."
            )
            continue

        return value


def build_temperature_range(
    minimum: Decimal, maximum: Decimal, precision: Decimal
) -> List[Decimal]:
    """Create a list of temperature values from ``minimum`` to ``maximum``."""

    current = minimum
    values: List[Decimal] = []
    while current <= maximum:
        values.append(current)
        current += precision
    return values


@dataclass
class LearningConfig:
    """Groups the user choices used while learning commands."""

    operation_modes: List[str]
    fan_modes: List[str]
    swing_modes: List[Optional[str]]


class ACLearningSession:
    """Encapsulates the command learning workflow for a Broadlink device."""

    def __init__(self, device: broadlink.device, data: Dict) -> None:
        self.device = device
        self.data = data
        self.commands: Dict = self.data.setdefault("commands", {})

        self.precision = Decimal(str(self.data["precision"]))
        self.min_default = Decimal(str(self.data["minTemperature"]))
        self.max_default = Decimal(str(self.data["maxTemperature"]))

        self.auto_resume = False
        self.skip_swing_learning = not self.data.get("swingModes")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Execute the interactive learning workflow."""

        self._prompt_for_auto_resume()
        config = self._build_learning_config()
        temperatures = self._prepare_temperature_range()
        self._learn_all_commands(config, temperatures)
        self._learn_off_command()

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------
    def _prompt_for_auto_resume(self) -> None:
        if self.commands:
            self.auto_resume = prompt_yes_no(
                "Do you want to resume where you left?", default=False
            )

    def _build_learning_config(self) -> LearningConfig:
        if self.auto_resume:
            operation_modes = list(self.data.get("operationModes", []))
            fan_modes = list(self.data.get("fanModes", []))
            swing_modes = (
                [None]
                if self.skip_swing_learning
                else list(self.data.get("swingModes", []))
            )
            return LearningConfig(operation_modes, fan_modes, swing_modes)

        operation_modes = prompt_list(
            "operation modes", self.data.get("operationModes", [])
        )
        print(f"Will learn these operation modes: {operation_modes}")

        fan_modes = prompt_list("fan modes", self.data.get("fanModes", []))
        print(f"Will learn these fan modes: {fan_modes}")

        swing_modes = self._resolve_swing_modes()
        return LearningConfig(operation_modes, fan_modes, swing_modes)

    def _resolve_swing_modes(self) -> List[Optional[str]]:
        existing_modes = list(self.data.get("swingModes", []))

        if not existing_modes:
            skip = prompt_yes_no(
                "No swing modes detected. Do you want to skip swing mode learning?",
                default=True,
            )
            if skip:
                self._clear_swing_modes()
                return [None]

            self.skip_swing_learning = False
            swing_modes = prompt_list("swing modes", existing_modes)
            if swing_modes:
                print(f"Will learn these swing modes: {swing_modes}")
                self.data["swingModes"] = swing_modes
                return swing_modes

            print("No swing modes provided. Swing mode learning will be skipped.")
            self._clear_swing_modes()
            return [None]

        skip = prompt_yes_no(
            "Do you want to skip swing mode learning?", default=False
        )
        if skip:
            self._clear_swing_modes()
            return [None]

        self.skip_swing_learning = False
        swing_modes = prompt_list("swing modes", existing_modes)
        if swing_modes:
            print(f"Will learn these swing modes: {swing_modes}")
            self.data["swingModes"] = swing_modes
            return swing_modes

        print("No swing modes provided. Swing mode learning will be skipped.")
        self._clear_swing_modes()
        return [None]

    def _clear_swing_modes(self) -> None:
        self.skip_swing_learning = True
        self.data["swingModes"] = []
        print("Swing mode learning will be skipped.")

    def _prepare_temperature_range(self) -> List[Decimal]:
        minimum = prompt_temperature("minimum", self.min_default, self.precision)
        maximum = prompt_temperature(
            "maximum", self.max_default, self.precision, minimum
        )

        self.data["minTemperature"] = decimal_to_number(minimum)
        self.data["maxTemperature"] = decimal_to_number(maximum)

        return build_temperature_range(minimum, maximum, self.precision)

    # ------------------------------------------------------------------
    # Learning helpers
    # ------------------------------------------------------------------
    def _learn_all_commands(
        self, config: LearningConfig, temperature_values: List[Decimal]
    ) -> None:
        temperatures = list(temperature_values)

        for operation_mode in config.operation_modes:
            for fan_mode in config.fan_modes:
                for swing_mode in config.swing_modes:
                    self._learn_for_mode(
                        operation_mode, fan_mode, swing_mode, temperatures
                    )
                    temperatures = list(reversed(temperatures))

    def _learn_for_mode(
        self,
        operation_mode: str,
        fan_mode: str,
        swing_mode: Optional[str],
        temperature_values: List[Decimal],
    ) -> None:
        fan_entry = self.commands.setdefault(operation_mode, {})
        description = f'"{operation_mode}" and "{fan_mode}" fan'

        if self.skip_swing_learning or swing_mode is None:
            target_container = fan_entry
        else:
            fan_entry.setdefault(swing_mode, {})
            target_container = fan_entry[swing_mode]
            description = (
                f'"{operation_mode}", "{fan_mode}" fan and '
                f'"{swing_mode}" swing mode'
            )

        if target_container and self._should_skip_existing_entry(description):
            return

        self._capture_commands(
            target_container, operation_mode, fan_mode, swing_mode, temperature_values
        )

    def _should_skip_existing_entry(self, description: str) -> bool:
        if self.auto_resume:
            return True

        message = (
            f"It seems you already have the definition for {description}. "
            "Do you want to skip to the next step?"
        )
        return prompt_yes_no(message, default=False)

    def _capture_commands(
        self,
        target_container: Dict,
        operation_mode: str,
        fan_mode: str,
        swing_mode: Optional[str],
        temperature_values: List[Decimal],
    ) -> None:
        header = f"Learning for mode {operation_mode}, fan {fan_mode}"
        if not self.skip_swing_learning and swing_mode is not None:
            header = f"{header}, swing {swing_mode}"
        print(header)

        start_temp = temperature_to_string(temperature_values[0])
        response = input(
            "Prepare remote for learning, starting at "
            f"{start_temp}º. Enter \"s\" if this mode has no temperature "
            "selection (e.g. fan mode). Continue? ([y]/n/s) "
        ).strip().lower()

        if response == "n":
            sys.exit(0)

        target_container.clear()

        if response == "s":
            print("Waiting for command")
            command = self._learn_command()
            for temperature in temperature_values:
                target_container[temperature_to_string(temperature)] = command
            return

        for temperature in temperature_values:
            temperature_label = temperature_to_string(temperature)
            print(f"Waiting for command for temperature {temperature_label}")
            command = self._learn_command()
            target_container[temperature_label] = command

    def _learn_command(self) -> str:
        self.device.enter_learning()
        start = time.time()

        while time.time() - start < TIMEOUT_SECONDS:
            time.sleep(0.5)
            try:
                return base64.b64encode(self.device.check_data()).decode("ascii")
            except (ReadError, StorageError):
                continue

        print("No data received...")
        return ""

    def _learn_off_command(self) -> None:
        print("Waiting for the OFF command...")
        self.commands["off"] = self._learn_command()


def main() -> None:
    if len(sys.argv) < 2:
        device_ip = input("Please enter the IP address of your Broadlink device: ")
    else:
        device_ip = sys.argv[1]

    device = broadlink.hello(device_ip)
    print(device)
    device.auth()

    with CONFIG_FILE.open() as config_file:
        data = json.load(config_file)

    session = ACLearningSession(device, data)

    try:
        session.run()
    finally:
        with CONFIG_FILE.open("w") as config_file:
            json.dump(data, config_file, indent=4)


if __name__ == "__main__":
    main()
