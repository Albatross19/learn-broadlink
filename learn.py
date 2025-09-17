import sys

import broadlink
import json
import time
import base64
from decimal import Decimal, InvalidOperation

from broadlink.exceptions import ReadError, StorageError

TIMEOUT = 30

skip_swing_learning = False


# noinspection PyUnresolvedReferences
def learn_command():
    device.enter_learning()
    start = time.time()
    while time.time() - start < TIMEOUT:
        time.sleep(0.5)
        try:
            return base64.b64encode(device.check_data()).decode('ascii')
        except (ReadError, StorageError):
            continue
    else:
        print("No data received...")
        return ''


def input_list(prompt_name, property_name):
    input_string = input(f'Enter {prompt_name} separated by commas, or leave empty to auto-detect: ')
    if len(input_string) == 0:
        return list(data.get(property_name, []))
    else:
        return [item.strip() for item in input_string.split(',') if item.strip()]


def temperature_to_string(value):
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def decimal_to_number(value):
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def prompt_temperature(prompt_name, default, precision, min_value=None):
    default_text = temperature_to_string(default)
    while True:
        user_input = input(f'Enter {prompt_name} temperature (default {default_text}): ').strip()
        if not user_input:
            value = default
        else:
            try:
                value = Decimal(user_input)
            except InvalidOperation:
                print('Invalid temperature value. Please try again.')
                continue

        if precision <= 0:
            print('Precision must be greater than zero.')
            continue

        if value % precision != 0:
            print(f'Temperature must align with the configured precision ({precision}).')
            continue

        if min_value is not None and value < min_value:
            print('Maximum temperature cannot be lower than minimum temperature.')
            continue

        if min_value is not None and (value - min_value) % precision != 0:
            print(f'The temperature difference must be a multiple of the precision ({precision}).')
            continue

        return value


def build_temperature_range(min_temperature, max_temperature, precision):
    current = min_temperature
    temp_values = []
    while current <= max_temperature:
        temp_values.append(current)
        current += precision
    return temp_values


def learn_commands(operation_mode, fan_mode, swing_mode, temp_range):
    if operation_mode not in commands:
        commands[operation_mode] = {}
    if fan_mode not in commands[operation_mode]:
        commands[operation_mode][fan_mode] = {}

    fan_entry = commands[operation_mode][fan_mode]

    if skip_swing_learning:
        target_container = fan_entry
        if target_container and (
            auto_resume_mode or
            input(
                f'It seems you already have the definition for "{operation_mode}" '
                f'and "{fan_mode}" fan. Do you want to skip to the next step? (y/[n]) '
            ) == 'y'
        ):
            return
    else:
        if swing_mode not in fan_entry:
            fan_entry[swing_mode] = {}
        else:
            if auto_resume_mode or \
               input(f'It seems you already have the definition for "{operation_mode}", "{fan_mode}" fan and '
                     f'"{swing_mode}" swing mode. Do you want to skip to the next step? (y/[n]) ') == 'y':
                return
        target_container = fan_entry[swing_mode]

    start_temp = temperature_to_string(temp_range[0])
    response = input(f'Prepare remote for learning, starting at {start_temp}º. '
                     f'Enter "s" if this mode has no temperature selection (e.g. fan mode). Continue? ([y]/n/s) ')
    if response == 'n':
        exit()
    if response == 's':
        print(f'Waiting for command')
        base64command = learn_command()
        for temp in temp_range:
            target_container[temperature_to_string(temp)] = base64command
    else:
        for temp in temp_range:
            temp_text = temperature_to_string(temp)
            print(f'Waiting for command for temperature {temp_text}')
            base64command = learn_command()
            target_container[temp_text] = base64command


def main():
    global skip_swing_learning

    if auto_resume_mode:
        operation_modes = list(data.get('operationModes', []))
        fan_modes = list(data.get('fanModes', []))
        swing_modes = [None] if skip_swing_learning else list(data.get('swingModes', []))
    else:
        operation_modes = input_list('operation modes', 'operationModes')
        print(f'Will learn this operation modes: {operation_modes}')
        fan_modes = input_list('fan modes', 'fanModes')
        print(f'Will learn this fan modes: {fan_modes}')

        existing_swing_modes = list(data.get('swingModes', []))

        if not existing_swing_modes:
            response = input('No swing modes detected. Do you want to skip swing mode learning? ([y]/n) ').strip().lower()
            if response == 'n':
                skip_swing_learning = False
                swing_modes = input_list('swing modes', 'swingModes')
                if swing_modes:
                    print(f'Will learn this swing modes: {swing_modes}')
                    data['swingModes'] = swing_modes
                else:
                    print('No swing modes provided. Swing mode learning will be skipped.')
                    skip_swing_learning = True
                    swing_modes = [None]
                    if 'swingModes' in data:
                        data['swingModes'] = []
            else:
                skip_swing_learning = True
                swing_modes = [None]
                print('Swing mode learning will be skipped.')
                if 'swingModes' in data:
                    data['swingModes'] = []
        else:
            response = input('Do you want to skip swing mode learning? ([n]/y) ').strip().lower()
            if response == 'y':
                skip_swing_learning = True
                swing_modes = [None]
                print('Swing mode learning will be skipped.')
                if 'swingModes' in data:
                    data['swingModes'] = []
            else:
                skip_swing_learning = False
                swing_modes = input_list('swing modes', 'swingModes')
                if swing_modes:
                    print(f'Will learn this swing modes: {swing_modes}')
                    data['swingModes'] = swing_modes
                else:
                    print('No swing modes provided. Swing mode learning will be skipped.')
                    skip_swing_learning = True
                    swing_modes = [None]
                    if 'swingModes' in data:
                        data['swingModes'] = []

        if skip_swing_learning:
            swing_modes = [None]

    precision = Decimal(str(data['precision']))
    min_default = Decimal(str(data['minTemperature']))
    max_default = Decimal(str(data['maxTemperature']))

    min_temperature = prompt_temperature('minimum', min_default, precision)
    max_temperature = prompt_temperature('maximum', max_default, precision, min_temperature)

    data['minTemperature'] = decimal_to_number(min_temperature)
    data['maxTemperature'] = decimal_to_number(max_temperature)

    temp_range = build_temperature_range(min_temperature, max_temperature, precision)

    for operation_mode in operation_modes:
        for fan_mode in fan_modes:
            for swing_mode in swing_modes:
                if skip_swing_learning:
                    print(f'Learning for mode {operation_mode}, fan {fan_mode}')
                else:
                    print(
                        f'Learning for mode {operation_mode}, fan {fan_mode}, swing {swing_mode}')
                learn_commands(operation_mode, fan_mode, swing_mode, temp_range)
                temp_range = list(reversed(temp_range))


if len(sys.argv) < 2:
    device_ip = input('Please enter the IP address of your Broadlink device: ')
else:
    device_ip = sys.argv[1]

device = broadlink.hello(device_ip)
print(device)
device.auth()

with open('smartir.json') as f:
    data = json.load(f)

commands = data['commands']
skip_swing_learning = len(data.get('swingModes', [])) == 0
try:
    auto_resume_mode = input('Do you want to resume where you left? (y/n) ') == 'y' if data['commands'] != {} else False
    main()

    print('Waiting for the OFF command...')
    commands['off'] = learn_command()

finally:
    with open('smartir.json', 'w') as f:
        json.dump(data, f, indent=4)
