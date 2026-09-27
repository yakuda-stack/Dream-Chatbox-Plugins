#!/usr/bin/env python3
"""Vendored from ZenithVal/OSCLeash (MIT) - see LICENSE and ../VENDOR.md.
Modified: python 3.10/3.11 compatible config path and a missing
`import sys`. Nothing else was touched."""
from threading import Thread
import json
from sys import platform
import os
import sys  # vendored change (yakuda): used by the restart below, never imported upstream
import time

from Controllers.DataController import DefaultConfig, ConfigSettings, Leash
from Controllers.PackageController import Package
from Controllers.ThreadController import Program

# Make sure to change this to the correct version number on releases.
__version__ = "v"+"VERSION_PLACEHOLDER"

def createDefaultConfigFile(configPath): # Creates a default config
    try:
        directory: str = os.path.dirname(configPath)

        # Create parent directories of `configPath` if they don't exist.
        if not os.path.isdir(directory):
            os.makedirs(directory)

        with open(configPath, "w") as cf:
            json.dump(DefaultConfig, cf, indent=4)

        print("Default config file created\n")
        time.sleep(2)

    except Exception as e:
        print(e)
        program.pause()
        exit()


if __name__ == "__main__":

    #*************Setup*************#
    program = Program()
    program.setWindowTitle()
    program.cls()

    if "PLACEHOLDER" in __version__:
        __version__ = "(Local Build)"

    print('\x1b[1;32;40m' + f"OSCLeash {__version__}" + '\x1b[0m')

    # Choose configuration path based on operating system if no override is set
    configPathOverride = os.environ.get('OSCLEASH_CONFIG_PATH')
    if configPathOverride is not None:
        configPath = configPathOverride
    elif platform == 'win32':
        configPath = f"{os.environ.get('LocalAppData')}\Programs\OSCLeash\Config.json"
    elif platform == 'linux': 
        # vendored change (yakuda): the inner f-string used the same quotes
        # as the outer one - only valid from python 3.12 on, a SyntaxError
        # on 3.10/3.11 that stopped every leash from starting.
        configPath = os.path.join(os.environ.get('XDG_CONFIG_HOME', f"{os.environ.get('HOME')}/.config/"), "OSCLeash", "Config.json")

    # Test if Config file exists. Create the default if it does not.
    if not os.path.isfile(configPath):
        # print error message in red
        print('\x1b[1;31;40m' + "Config file was not found...", "\nCreating default config file..." + '\x1b[0m')
        createDefaultConfigFile(configPath)
    else:
        print(f"Config file found at {configPath}\n")

    # load settings
    try:
        configData = json.load(open(configPath))
    except Exception as e:
        print('\x1b[1;31;40m' + 'Malformed Config.json file. Fix or delete it to generate a new one.' + '\x1b[0m')
        print(f"{e}\nDefault Config will be loaded.\n")

        configData = DefaultConfig
        program.pause()
        
    settings = ConfigSettings(configData)

    time.sleep(1)

    # TODO: Remove Xbox support if not needed
    if settings.XboxJoystickMovement:
        try:
            import vgamepad as vg
            settings.addGamepadControls(vg.VX360Gamepad(), vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER) # Add emulated gamepad
        except Exception as e:
            print('\x1b[1;31;40m' + f'Error: {e}\nWarning: Switching to default OSC settings. Please wait...\n Check documentation for controller emulator tool.' + '\x1b[0m')
            settings.XboxJoystickMovement = False
            program.pause()

    # Collect Data for leash
    leashes = []
    for leashName in configData["PhysboneParameters"]:
        leashes.append(Leash(leashName, configData["DirectionalParameters"], settings))

    try:
        # Manage data coming in
        if len(leashes) == 0: raise Exception("No leashes found. Please update config file.")
        package = Package(leashes, configData['UseOSCQuery'])
        package.listen()

        # Start server
        serverThread = Thread(target=package.runServer, args=(settings.IP, settings.ListeningPort))
        serverThread.start()
        time.sleep(.1)
        
        #initialize input
        if serverThread.is_alive():
            leashes[0].Active = True
            print("Started, awaiting input...")
            Thread(target=program.leashRun, args=(leashes[0],)).start()
        else: raise Exception()
            
    except Exception as e:
        print(e)
        program.pause()
        os.execl(sys.executable, sys.executable, *sys.argv)
